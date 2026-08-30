#!/usr/bin/env python3
"""🚪 TELEGRAM GATEWAY — ЕДИНАЯ точка доступа к Телеграму для ВСЕХ сервисов vps (31.07.2026).

ЗАЧЕМ: одна сессия = один процесс. Если два сервиса подключаются одним ключом —
Телеграм казнит ключ («two different IP addresses simultaneously»), что нас и убивало.
Здесь единственное подключение держит ЭТОТ сервис, а все остальные (студия, копир,
брифы, радары, разовые задачи) ходят через локальный HTTP — своих коннектов не делают.

API (только localhost:8099):
  GET  /me                                    — кто мы
  GET  /read?chat=<@name|id>&limit=50         — прочитать сообщения канала/чата
  GET  /dialogs?limit=100                     — список диалогов
  POST /send      {chat, text}                — отправить сообщение
  POST /album     {chat, text, files:[...]}   — альбом фото с подписью
  POST /story     {files:[...]}               — сторис в свой профиль

Транспорт наружу: WARP (прямой к DC заблокирован РКН).
Сессия: <путь>/publish_session.session (создаётся один раз с личной машины).
"""
import asyncio
import os
import sys

sys.path.insert(0, "/root")
from tg_login import C, proxy_tuple  # паспорт /root/telethon.env

SESSION = "/root/tg_gateway_data/gateway"   # приватно: чужой код ключ не найдёт и не убьёт его параллельным подключением
PORT = 8099

from fastapi import Body, FastAPI, HTTPException
from telethon import TelegramClient
from telethon.network.connection.tcpabridged import ConnectionTcpAbridged

app = FastAPI(title="Telegram Gateway")
_cl: TelegramClient | None = None
_lock = asyncio.Lock()


async def client() -> TelegramClient:
    global _cl
    async with _lock:
        if _cl is None:
            _cl = TelegramClient(SESSION, int(C["TG_API_ID"]), C["TG_API_HASH"],
                                 connection=ConnectionTcpAbridged, proxy=proxy_tuple(),
                                 connection_retries=3, timeout=30)
        if not _cl.is_connected():
            await _cl.connect()
        return _cl


@app.get("/me")
async def me():
    cl = await client()
    u = await cl.get_me()
    if not u:
        raise HTTPException(503, "сессия не авторизована")
    return {"ok": True, "username": getattr(u, "username", None), "id": u.id}


@app.get("/peek_full")
async def peek_full(chat: str, mid: int):
    """Отладка: полный to_dict одного сообщения (не-null поля)."""
    cl = await client()
    ent = await cl.get_entity(chat if not chat.lstrip("-").isdigit() else int(chat))
    m = await cl.get_messages(ent, ids=int(mid))
    if m is None:
        return {"ok": False, "missing": True}
    def clean(o, depth=0):
        if depth > 6:
            return "…"
        if isinstance(o, dict):
            return {k: clean(v, depth + 1) for k, v in o.items()
                    if v not in (None, False, [], {}, "")}
        if isinstance(o, list):
            return [clean(x, depth + 1) for x in o[:5]]
        if isinstance(o, (bytes, bytearray)):
            return f"<bytes {len(o)}>"
        return str(o)[:200]
    return {"ok": True, "dict": clean(m.to_dict())}


@app.get("/peek")
async def peek(chat: str, ids: str):
    """Отладка: сырые атрибуты конкретных сообщений (тип медиа, альбом, entities)."""
    cl = await client()
    ent = await cl.get_entity(chat if not chat.lstrip("-").isdigit() else int(chat))
    want = [int(x) for x in ids.split(",") if x.strip()]
    out = []
    msgs = await cl.get_messages(ent, ids=want)
    for m in (msgs if isinstance(msgs, list) else [msgs]):
        if m is None:
            out.append({"id": None, "missing": True})
            continue
        out.append({
            "id": m.id,
            "cls": type(m).__name__,
            "text_len": len(m.message or ""),
            "raw_text_prev": (m.message or "")[:80],
            "media_cls": type(m.media).__name__ if m.media else None,
            "grouped_id": getattr(m, "grouped_id", None),
            "entities": len(m.entities or []),
            "views": getattr(m, "views", None),
            "post_author": getattr(m, "post_author", None),
            "reply_to": bool(getattr(m, "reply_to", None)),
        })
    return {"ok": True, "items": out}


def _flatten_rich_text(t) -> str:
    """RichText-дерево (TextConcat/TextBold/TextPlain/TextCustomEmoji…) → строка."""
    if t is None:
        return ""
    if isinstance(t, str):
        return t
    out = []
    inner = getattr(t, "text", None)
    if isinstance(inner, str):
        out.append(inner)
    elif inner is not None:
        out.append(_flatten_rich_text(inner))
    for sub in (getattr(t, "texts", None) or []):
        out.append(_flatten_rich_text(sub))
    if not out:
        alt = getattr(t, "alt", None)  # TextCustomEmoji → сам эмодзи
        if alt:
            out.append(alt)
    return "".join(out)


def _rich_text(m) -> str:
    """Текст сообщения: обычный ИЛИ собранный из блоков RichMessage (Instant-View)."""
    t = m.message or ""
    if t:
        return t
    rm = getattr(m, "rich_message", None)
    if not rm:
        return ""
    parts = []
    for b in (getattr(rm, "blocks", None) or []):
        piece = _flatten_rich_text(getattr(b, "text", None))
        if not piece:
            cap = getattr(b, "caption", None)
            piece = _flatten_rich_text(getattr(cap, "text", None)) if cap else ""
        if piece and piece.strip():
            parts.append(piece.strip())
    return "\n\n".join(parts)


@app.get("/read")
async def read(chat: str, limit: int = 50, media: int = 0):
    """Прочитать последние сообщения канала/чата — для радаров тем и хуков."""
    cl = await client()
    try:
        ent = await cl.get_entity(chat if not chat.lstrip("-").isdigit() else int(chat))
    except Exception as exc:
        raise HTTPException(404, f"чат не найден: {str(exc)[:120]}")
    out = []
    async for m in cl.iter_messages(ent, limit=min(int(limit), 300)):
        row = {
            "id": m.id,
            "date": m.date.isoformat() if m.date else None,
            "views": getattr(m, "views", 0) or 0,
            "forwards": getattr(m, "forwards", 0) or 0,
            "fwd": getattr(m, "fwd_from", None) is not None,
            "reactions": sum((getattr(x, "count", 0) or 0)
                             for x in (getattr(getattr(m, "reactions", None), "results", None) or [])),
            "text": _rich_text(m)[:4000],
            "out": bool(getattr(m, "out", False)),
            "has_media": bool(getattr(m, "media", None)),
            "sender": _who(await m.get_sender()) if getattr(m, "sender_id", None) else "",
        }
        # 31.07: media=1 — фото для vision-разбора (копир: «стопы на КАРТИНКЕ»)
        if media and getattr(m, "photo", None):
            try:
                raw = await cl.download_media(m, bytes)
                if raw and len(raw) < 3_500_000:
                    import base64
                    row["photo_b64"] = base64.b64encode(raw).decode()
            except Exception:
                pass
        out.append(row)
    return {"ok": True, "chat": chat, "count": len(out), "messages": out}


@app.get("/dialogs")
async def dialogs(limit: int = 100, kinds: str = "all", days: int = 0,
                  max_scan: int = 1500):
    """kinds=user — только личные переписки (без каналов, групп, ботов, Избранного).

    Фильтр применяется ДО лимита. Без этого лимит съедали каналы: при limit=120
    на 75 каналов и 31 группу приходилось 5 личных чатов, и половина долгов
    доктора просто не попадала в выборку — каждый день разная (инцидент 30.08).

    days>0 — не углубляться дальше N дней. Закреплённые чаты идут первыми
    независимо от даты, поэтому останавливаемся не на первом старом, а после
    STALE_STOP подряд идущих старых.
    """
    from datetime import datetime, timedelta, timezone
    cl = await client()
    out = []
    only_user = (kinds or "all").lower() == "user"
    cutoff = (datetime.now(timezone.utc) - timedelta(days=int(days))) if days else None
    STALE_STOP, stale, scanned = 30, 0, 0
    async for d in cl.iter_dialogs(limit=None if (only_user or cutoff) else min(int(limit), 500)):
        scanned += 1
        if scanned > int(max_scan):
            break
        if cutoff:
            if d.date and d.date < cutoff:
                stale += 1
                if stale >= STALE_STOP:
                    break
                continue
            stale = 0
        _m = getattr(d, "message", None)
        # out=True -> последним писал доктор; False -> ждут его ответа
        _out = bool(getattr(_m, "out", False)) if _m else None
        _sender = ""
        try:
            _s = getattr(_m, "sender", None)
            if _s is not None:
                _sender = (getattr(_s, "first_name", "") or "") + " " + (getattr(_s, "last_name", "") or "")
                _sender = _sender.strip() or (getattr(_s, "title", "") or getattr(_s, "username", "") or "")
        except Exception:
            pass
        _ent = getattr(d, "entity", None)
        _is_bot = bool(getattr(_ent, "bot", False)) if _ent is not None else False
        _is_self = bool(getattr(_ent, "is_self", False)) if _ent is not None else False
        if only_user and (d.is_channel or bool(getattr(d, "is_group", False))
                          or _is_bot or _is_self):
            continue
        out.append({"id": d.id, "name": d.name, "is_channel": d.is_channel,
                    "is_group": bool(getattr(d, "is_group", False)),
                    "is_bot": _is_bot, "is_self": _is_self,
                    "unread": getattr(d, "unread_count", 0) or 0,
                    "date": d.date.isoformat() if d.date else None,
                    "out": _out, "sender": _sender,
                    "last": ((_m.message or "") if _m else "")[:300]})
        if len(out) >= int(limit):
            break
    return {"ok": True, "count": len(out), "scanned": scanned, "dialogs": out}


@app.post("/send")
async def send(payload: dict = Body(...)):
    cl = await client()
    chat = payload.get("chat")
    text = payload.get("text") or ""
    if not chat or not text:
        raise HTTPException(400, "нужны chat и text")
    m = await cl.send_message(chat if not str(chat).lstrip("-").isdigit() else int(chat),
                              text, parse_mode=payload.get("parse_mode", "html"),
                              link_preview=bool(payload.get("link_preview", False)))
    return {"ok": True, "message_id": m.id}


@app.get("/buttons")
async def buttons(chat: str, limit: int = 10):
    """Последние сообщения диалога с inline-кнопками (текст + callback_data) — для e2e-проверок ботов."""
    cl = await client()
    ent = await cl.get_entity(chat if not chat.lstrip("-").isdigit() else int(chat))
    out = []
    async for m in cl.iter_messages(ent, limit=min(int(limit), 50)):
        rows = []
        for row in (getattr(m, "buttons", None) or []):
            rows.append([
                {
                    "text": getattr(b, "text", ""),
                    "data": (b.data.decode("utf-8", "replace")
                             if getattr(b, "data", None) else None),
                    "url": getattr(b, "url", None),
                }
                for b in row
            ])
        out.append({"id": m.id, "out": bool(getattr(m, "out", False)),
                    "text": (_rich_text(m) or "")[:600], "buttons": rows,
                    "doc_name": (getattr(getattr(m, "file", None), "name", None) or None)})
    return {"ok": True, "chat": chat, "messages": out}


@app.post("/delete")
async def delete_msgs(payload: dict = Body(...)):
    """Удалить сообщения в диалоге (у обеих сторон) — уборка после e2e-тестов ботов."""
    cl = await client()
    chat = payload.get("chat")
    ids = payload.get("ids") or []
    if not chat or not ids:
        raise HTTPException(400, "нужны chat и ids[]")
    ent = await cl.get_entity(str(chat) if not str(chat).lstrip("-").isdigit() else int(chat))
    ids = [int(x) for x in ids][:100]
    await cl.delete_messages(ent, ids, revoke=True)
    return {"ok": True, "deleted": len(ids)}


@app.get("/click")
async def click(chat: str, msg_id: int, data: str):
    """Нажать inline-кнопку с callback_data=data в сообщении msg_id — e2e-тест бот-флоу."""
    cl = await client()
    ent = await cl.get_entity(chat if not chat.lstrip("-").isdigit() else int(chat))
    msg = await cl.get_messages(ent, ids=int(msg_id))
    if not msg:
        raise HTTPException(404, "сообщение не найдено")
    try:
        r = await msg.click(data=data.encode("utf-8"))
    except Exception as exc:
        raise HTTPException(502, f"click failed: {str(exc)[:200]}")
    alert = getattr(r, "message", None) if r is not None else None
    return {"ok": True, "alert": alert}


@app.post("/album")
async def album(payload: dict = Body(...)):
    """Альбом фото + подпись одним постом (то, чего не умеет Bot API при длинном тексте)."""
    cl = await client()
    chat = payload.get("chat")
    files = [f for f in (payload.get("files") or []) if os.path.exists(f)]
    if not chat or not files:
        raise HTTPException(400, "нужны chat и существующие files")
    msgs = await cl.send_file(chat if not str(chat).lstrip("-").isdigit() else int(chat),
                              files[:10], caption=(payload.get("text") or "")[:4096],
                              parse_mode=payload.get("parse_mode", "html"))
    ids = [m.id for m in (msgs if isinstance(msgs, list) else [msgs])]
    return {"ok": True, "message_ids": ids}


@app.post("/story")
async def story(payload: dict = Body(...)):
    cl = await client()
    files = [f for f in (payload.get("files") or []) if os.path.exists(f)]
    if not files:
        raise HTTPException(400, "нужны files")
    from telethon.tl.functions.stories import SendStoryRequest
    from telethon.tl.types import InputPrivacyValueAllowAll
    peer = payload.get("peer") or "me"          # свой профиль либо канал
    if peer != "me":
        peer = await cl.get_entity(str(peer).lstrip("@"))
    sent = []
    for f in files[:20]:
        up = await cl.upload_file(f)
        from telethon.tl.types import InputMediaUploadedPhoto
        r = await cl(SendStoryRequest(peer=peer, media=InputMediaUploadedPhoto(up),
                                      privacy_rules=[InputPrivacyValueAllowAll()],
                                      pinned=False, noforwards=False))
        sent.append(getattr(r, "id", None))
        await asyncio.sleep(3)
    return {"ok": True, "count": len(sent)}


@app.post("/post")
async def post(payload: dict = Body(...)):
    """Публикация поста в канал: альбом с подписью + хвостовые части ответами.

    Текст приходит уже нарезанным (parts[0] влезает в подпись) — разбивку и
    кастом-эмодзи делает вызывающая сторона, здесь только отправка.
    """
    cl = await client()
    chat = payload.get("chat")
    parts = [p for p in (payload.get("parts") or []) if (p or "").strip()]
    files = [f for f in (payload.get("files") or []) if os.path.exists(f)]
    if not chat or not parts:
        raise HTTPException(400, "нужны chat и parts")
    ent = await cl.get_entity(str(chat).lstrip("@") if not str(chat).lstrip("-").isdigit() else int(chat))
    first = parts[0]
    if files:
        sent = await cl.send_file(ent, files[:10], caption=first, parse_mode="html")
        first_id = getattr(sent[0] if isinstance(sent, (list, tuple)) else sent, "id", None)
    else:
        sent = await cl.send_message(ent, first, parse_mode="html", link_preview=False)
        first_id = getattr(sent, "id", None)
    for part in parts[1:]:
        await cl.send_message(ent, part, parse_mode="html", link_preview=False, reply_to=first_id)
    return {"ok": True, "message_id": first_id}


def _who(ent) -> str:
    """Человекочитаемое имя собеседника или канала."""
    if ent is None:
        return ""
    t = getattr(ent, "title", None)
    if t:
        return str(t)
    fn = (getattr(ent, "first_name", "") or "") + " " + (getattr(ent, "last_name", "") or "")
    return fn.strip() or (getattr(ent, "username", "") or "") or str(getattr(ent, "id", ""))


@app.get("/search")
async def search(query: str, chat: str = "", limit: int = 20):
    """Поиск по сообщениям — в конкретном чате или по всем сразу."""
    cl = await client()
    ent = None
    if chat:
        ent = await cl.get_entity(chat.lstrip("@") if not chat.lstrip("-").isdigit() else int(chat))
    out = []
    async for m in cl.iter_messages(ent, search=query, limit=min(int(limit), 100)):
        out.append({
            "id": m.id,
            "date": m.date.isoformat() if m.date else None,
            "chat": _who(ent) if ent else _who(await m.get_chat()),
            "sender": "Я" if getattr(m, "out", False) else (
                _who(await m.get_sender()) if getattr(m, "sender_id", None) else ""),
            "text": (m.message or "")[:1000],
        })
    return {"ok": True, "count": len(out), "messages": out}


@app.get("/folders")
async def folders():
    """Папки Телеграма и каналы в них — на этом строится дайджест."""
    from telethon.tl import functions as _f
    cl = await client()
    res = await cl(_f.messages.GetDialogFiltersRequest())
    out = []
    for fl in (getattr(res, "filters", res) or []):
        title = getattr(fl, "title", None)
        if title is None:
            continue
        peers = []
        for p in (getattr(fl, "include_peers", []) or []):
            try:
                ent = await cl.get_entity(p)
                peers.append({"id": ent.id, "name": _who(ent),
                              "username": getattr(ent, "username", None)})
            except Exception:
                continue
        out.append({"title": str(getattr(title, "text", title)), "peers": peers})
    return {"ok": True, "folders": out}


@app.post("/sendfile")
async def sendfile(payload: dict = Body(...)):
    """Файл или фото с подписью."""
    cl = await client()
    chat, path = payload.get("chat"), payload.get("path")
    if not chat or not path or not os.path.exists(path):
        raise HTTPException(400, "нужны chat и существующий path")
    m = await cl.send_file(chat if not str(chat).lstrip("-").isdigit() else int(chat),
                           path, caption=(payload.get("caption") or "")[:1024],
                           voice_note=bool(payload.get("voice")))
    return {"ok": True, "message_id": getattr(m, "id", None)}


@app.post("/media")
async def media(payload: dict = Body(...)):
    """Скачать последние фото из чата — вернуть пути к файлам."""
    from telethon.tl.types import InputMessagesFilterPhotos
    cl = await client()
    chat = payload.get("chat")
    limit = min(int(payload.get("limit") or 5), 30)
    outdir = payload.get("dir") or "/tmp"
    if not chat:
        raise HTTPException(400, "нужен chat")
    ent = await cl.get_entity(str(chat).lstrip("@") if not str(chat).lstrip("-").isdigit() else int(chat))
    got = []
    async for m in cl.iter_messages(ent, limit=200, filter=InputMessagesFilterPhotos):
        if len(got) >= limit:
            break
        path = os.path.join(outdir, f"tg_{m.id}.jpg")
        try:
            await m.download_media(file=path)
            os.chmod(path, 0o644)
            got.append({"path": path, "date": m.date.isoformat() if m.date else None,
                        "caption": (m.message or "")[:200]})
        except Exception:
            continue
    return {"ok": True, "count": len(got), "files": got}


@app.post("/richpost")
async def richpost(payload: dict = Body(...)):
    """Пост, где фото стоят ВНУТРИ текста, а не альбомом снизу.

    Вход: {chat, html, files:[{id, path}]}. В html картинка ставится тегом
    <img src="tg://photo?id=ID">, где ID — тот же, что в files. Лимит текста ~32768.
    """
    import re as _re
    from telethon.tl.functions.messages import SendMessageRequest, UploadMediaRequest
    from telethon.tl.types import (InputMediaUploadedPhoto, InputPhoto,
                                   InputRichFilePhoto, InputRichMessageHTML)

    cl = await client()
    chat = payload.get("chat")
    html = payload.get("html") or ""
    files = payload.get("files") or []
    if not chat or not html:
        raise HTTPException(400, "нужны chat и html")

    ent = await cl.get_entity(str(chat).lstrip("@") if not str(chat).lstrip("-").isdigit() else int(chat))

    rich = []
    for f in files[:20]:
        fid, path = str(f.get("id") or ""), f.get("path") or ""
        if not _re.fullmatch(r"[A-Za-z0-9_-]{1,64}", fid):
            raise HTTPException(400, f"недопустимый id картинки: {fid!r} (только A-Za-z0-9_-)")
        if not os.path.exists(path):
            raise HTTPException(400, f"файл не найден: {path}")
        if f'tg://photo?id={fid}' not in html:
            continue                      # картинка не размечена в тексте — не грузим зря
        up = await cl.upload_file(path)
        res = await cl(UploadMediaRequest(peer=ent, media=InputMediaUploadedPhoto(up)))
        ph = res.photo
        rich.append(InputRichFilePhoto(
            id=fid, photo=InputPhoto(id=ph.id, access_hash=ph.access_hash,
                                     file_reference=ph.file_reference)))

    r = await cl(SendMessageRequest(peer=ent, message="",
                                    rich_message=InputRichMessageHTML(html=html, files=rich)))
    ids = [u.message.id for u in getattr(r, "updates", []) if hasattr(u, "message")]
    return {"ok": True, "message_id": (ids or [None])[0], "images": len(rich)}


@app.get("/health")
async def health():
    try:
        cl = await client()
        return {"ok": True, "connected": cl.is_connected(),
                "authorized": await cl.is_user_authorized()}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:200]}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")
