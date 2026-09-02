#!/usr/bin/env python3
"""excash_guard — прокси перед шлюзом excash.

Зачем. 02.09 модель gpt-5.6-sol легла на стороне excash. Без стрима она честно
отдаёт HTTP 500, а в стриме — HTTP 200 и объект ошибки первым же кадром SSE.
OpenClaw видит «ответ пустой», трижды повторяет запрос К ТОЙ ЖЕ модели
(MAX_EMPTY_ERROR_RETRIES зашито в коде) и сдаётся с «LLM request failed»,
так и не дойдя до резервной модели. Чат замолкает.

Что делает. Проксирует всё как есть, но если поток начинается с кадра ошибки
(до единого куска содержимого) — отдаёт наверх честный HTTP 502. Тогда
срабатывает штатный автофолбэк OpenClaw и его же уведомление
«↪️ Model Fallback: …» в чат. Никакой своей логики выбора моделей.

Слушает 127.0.0.1:8788, наружу не смотрит.
"""
import json
import logging
import os
import time

import aiohttp
from aiohttp import web

UPSTREAM = os.environ.get("EXCASH_UPSTREAM", "https://<ваш-агрегатор>/v1").rstrip("/")
PORT = int(os.environ.get("EXCASH_GUARD_PORT", "8788"))
# сколько ждать первого осмысленного кадра, прежде чем признать поток живым
FIRST_CHUNK_TIMEOUT = float(os.environ.get("EXCASH_GUARD_FIRST_CHUNK_TIMEOUT", "90"))
HOP = {"host", "content-length", "connection", "keep-alive", "transfer-encoding",
       "upgrade", "proxy-authenticate", "proxy-authorization", "te", "trailers"}

log = logging.getLogger("excash_guard")
STATS = {"ok": 0, "converted": 0, "upstream_error": 0}


def _err_frame(chunk: bytes):
    """Кадр SSE с ошибкой и без содержимого? Возвращает текст ошибки либо None."""
    for line in chunk.split(b"\n"):
        line = line.strip()
        if not line.startswith(b"data:"):
            continue
        payload = line[5:].strip()
        if payload in (b"[DONE]", b""):
            continue
        try:
            obj = json.loads(payload)
        except Exception:
            return None            # неразборчивый кадр — не наше дело, пропускаем
        if isinstance(obj, dict) and obj.get("error"):
            e = obj["error"]
            return e.get("message") if isinstance(e, dict) else str(e)
        return None                # обычный кадр с содержимым — поток здоров
    return None


async def handle(req: web.Request) -> web.StreamResponse:
    body = await req.read()
    headers = {k: v for k, v in req.headers.items() if k.lower() not in HOP}
    url = UPSTREAM + "/" + req.match_info.get("tail", "")
    t0 = time.time()
    model = ""
    try:
        model = (json.loads(body or b"{}") or {}).get("model", "")
    except Exception:
        pass

    timeout = aiohttp.ClientTimeout(total=None, sock_read=600, sock_connect=30)
    session = aiohttp.ClientSession(timeout=timeout, auto_decompress=False)
    try:
        up = await session.request(req.method, url, data=body or None,
                                   headers=headers, params=req.rel_url.query)
    except Exception as e:
        await session.close()
        STATS["upstream_error"] += 1
        log.warning("upstream недоступен model=%s: %s", model, e)
        return web.json_response(
            {"error": {"message": f"excash недоступен: {type(e).__name__}",
                       "type": "upstream_unreachable"}}, status=502)

    ctype = up.headers.get("Content-Type", "")
    if "text/event-stream" not in ctype:
        raw = await up.read()
        await session.close()
        if up.status >= 400:
            STATS["upstream_error"] += 1
            log.warning("upstream %s model=%s: %s", up.status, model, raw[:200])
        else:
            STATS["ok"] += 1
        out = {k: v for k, v in up.headers.items() if k.lower() not in HOP}
        return web.Response(status=up.status, body=raw, headers=out)

    # --- поток: ждём первый содержательный кадр, прежде чем открыть ответ клиенту
    buf = b""
    try:
        async for chunk in up.content.iter_chunked(8192):
            buf += chunk
            msg = _err_frame(chunk)
            if msg:
                await session.close()
                STATS["converted"] += 1
                log.warning("ПОДМЕНА: excash отдал 200 с ошибкой в потоке "
                            "(model=%s): %s -> отдаю 502, чтобы сработал фолбэк",
                            model, msg)
                return web.json_response(
                    {"error": {"message": f"excash/{model}: {msg}",
                               "type": "upstream_stream_error"}}, status=502)
            if b"data:" in buf and b'"delta"' in buf or b'"content"' in buf:
                break                      # поток здоров — дальше просто льём
            if time.time() - t0 > FIRST_CHUNK_TIMEOUT:
                break
    except Exception as e:
        await session.close()
        STATS["upstream_error"] += 1
        log.warning("обрыв потока model=%s: %s", model, e)
        return web.json_response(
            {"error": {"message": f"excash: обрыв потока ({type(e).__name__})",
                       "type": "upstream_stream_broken"}}, status=502)

    out = {k: v for k, v in up.headers.items() if k.lower() not in HOP}
    resp = web.StreamResponse(status=up.status, headers=out)
    await resp.prepare(req)
    try:
        if buf:
            await resp.write(buf)
        async for chunk in up.content.iter_chunked(8192):
            await resp.write(chunk)
        await resp.write_eof()
        STATS["ok"] += 1
    except Exception as e:
        log.warning("обрыв при передаче клиенту model=%s: %s", model, e)
    finally:
        await session.close()
    return resp


async def health(_req):
    return web.json_response({"ok": True, "upstream": UPSTREAM, **STATS})


def main():
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    app = web.Application(client_max_size=256 * 1024 * 1024)
    app.router.add_get("/_guard/health", health)
    app.router.add_route("*", "/{tail:.*}", handle)
    log.info("excash_guard на 127.0.0.1:%d -> %s", PORT, UPSTREAM)
    web.run_app(app, host="127.0.0.1", port=PORT, print=None)


if __name__ == "__main__":
    main()
