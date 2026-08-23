#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Зрение для Йоды: описание присланных фото через excash.
OpenClaw вызывает: vision.py <путь к изображению> (плейсхолдер {{MediaPath}}).
Печатает подробное описание в stdout — оно уходит основной модели как контекст.

⚠️ Шлюз excash отклоняет картиночные запросы примерно от 40 КБ (HTTP 400 приходит
HTML-страницей от прокси, не от модели). Просто ужать нельзя — пропадёт мелкий текст
на меню и документах, а это главный сценарий. Поэтому: если целиком не влезает —
режем на перекрывающиеся плитки, читаем каждую в хорошем разрешении и сводим."""
import base64
import urllib.request
import json
import io
import os
import sys
import time

import requests

DEBUG = "/tmp/vision_debug.log"
BUDGET = 34 * 1024          # безопасный потолок одного запроса, байт
MODEL = "gpt-5.6-sol"


def dbg(m):
    try:
        open(DEBUG, "a", encoding="utf-8").write(str(m) + "\n")
    except Exception:
        pass


ENV = {}
_p = os.path.expanduser("~/.openclaw/.env")
if os.path.exists(_p):
    for line in open(_p, encoding="utf-8"):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            ENV[k.strip()] = v.strip().strip('"').strip("'")

KEY, URL = ENV.get("EXCASH_API_KEY", ""), ENV.get("EXCASH_API_URL", "")
IMG_EXT = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".heic", ".heif")

PROMPT = """Опиши это изображение максимально полезно и подробно для врача, который его прислал.

Обязательно:
1. ВЕСЬ текст с картинки — дословно. Меню, вывески, ценники, таблички, этикетки, документы,
   надписи на любом языке. Если текст на китайском/английском и т.п. — приведи оригинал
   И перевод на русский. Цены и цифры не пропускай.
2. Что изображено: место, обстановка, объекты, люди (без домыслов о личностях), еда, интерьер.
3. Если это меню или витрина — перечисли позиции с ценами и коротко поясни, что это за блюда
   (из чего, острое ли, есть ли свинина/морепродукты — врач может спросить про состав).
4. Если это медицинское изображение (снимок, рана, документ) — опиши объективно, что видно,
   без диагноза.
5. Если качество плохое или текст не читается — так и напиши, не выдумывай.

Пиши по-русски, структурно, без воды."""

TILE_PROMPT = """Это ФРАГМЕНТ {n} из {total} одного изображения (соседние фрагменты
перекрываются). Опиши только то, что видишь в этом фрагменте.

Главное — ВЕСЬ текст дословно (с переводом, если не по-русски), цены и цифры не пропускай.
Затем кратко — что изображено. Не додумывай то, чего не видно, и не пытайся описать
изображение целиком. Плохо читается — так и скажи."""



DS_KEY = ENV.get("DEEPSEEK_API_KEY", "")
DS_MODEL = "deepseek-v4-flash-vision-exp"
DS_BUDGET = 900 * 1024        # DeepSeek спокойно берёт 254КБ, ставим запас


def _ask_deepseek(data, mime, prompt):
    """Картинка ЦЕЛИКОМ одним запросом. У DeepSeek лимит на изображение в разы
    выше, чем у шлюза excash (~40КБ), поэтому резать на плитки не нужно."""
    if not DS_KEY:
        return ""
    body = json.dumps({
        "model": DS_MODEL, "max_tokens": 8000, "temperature": 0.2,
        "messages": [{"role": "user", "content": [
            {"type": "image_url", "image_url": {
                "url": f"data:image/{mime};base64," + base64.b64encode(data).decode()}},
            {"type": "text", "text": prompt}]}]}).encode()
    req = urllib.request.Request(
        "https://api.deepseek.com/chat/completions", data=body,
        headers={"Authorization": "Bearer " + DS_KEY, "Content-Type": "application/json"})
    resp = json.loads(urllib.request.urlopen(req, timeout=180).read().decode())
    msg = ((resp.get("choices") or [{}])[0] or {}).get("message") or {}
    c = msg.get("content")
    if isinstance(c, list):
        c = "\n".join(x.get("text", "") for x in c if isinstance(x, dict))
    return (c or "").strip()


def _encode(im, max_px, quality):
    im2 = im.copy()
    if im2.mode not in ("RGB", "L"):
        im2 = im2.convert("RGB")
    if max(im2.size) > max_px:
        im2.thumbnail((max_px, max_px))
    buf = io.BytesIO()
    im2.save(buf, "JPEG", quality=quality, optimize=True)
    return buf.getvalue(), im2.size


def _fit(im, budget=BUDGET):
    """Лучшее качество, влезающее в бюджет. None — если не влезает вообще."""
    for max_px, q in ((1600, 88), (1400, 85), (1200, 82), (1000, 80),
                      (860, 78), (720, 76), (600, 74), (480, 70)):
        data, size = _encode(im, max_px, q)
        if len(data) <= budget:
            return data, size, q
    return None, None, None


def _ask(data, prompt):
    r = requests.post(
        URL.rstrip("/") + "/chat/completions",
        headers={"Authorization": "Bearer " + KEY, "Content-Type": "application/json"},
        json={"model": MODEL, "max_tokens": 8000, "temperature": 0.2,
              "messages": [{"role": "user", "content": [
                  {"type": "image_url", "image_url": {
                      "url": "data:image/jpeg;base64," + base64.b64encode(data).decode()}},
                  {"type": "text", "text": prompt}]}]}, timeout=240)
    r.raise_for_status()
    msg = ((r.json().get("choices") or [{}])[0] or {}).get("message") or {}
    c = msg.get("content")
    if isinstance(c, list):
        c = "\n".join(x.get("text", "") for x in c if isinstance(x, dict))
    return (c or "").strip()


def _try(data, prompt, tag):
    for attempt in range(2):
        try:
            txt = _ask(data, prompt)
        except Exception as e:
            code = getattr(getattr(e, "response", None), "status_code", None)
            dbg("%s: %s%s" % (tag, type(e).__name__, " HTTP %s" % code if code else ""))
            return ""
        if txt:
            return txt
        dbg("%s: пустой ответ, повтор %d" % (tag, attempt + 1))
    return ""


def _tiles(im):
    """Режем ГОРИЗОНТАЛЬНЫМИ полосами всегда — строки текста (меню, документы,
    ценники) идут поперёк, и вертикальный разрез оторвал бы названия от цен.
    Перекрытие 12%, чтобы строка на стыке не потерялась."""
    w, h = im.size
    # сколько полос нужно, чтобы каждая влезла в бюджет с приличным разрешением
    area = w * h
    n = 2
    if area > 1_600_000:
        n = 3
    if area > 3_500_000:
        n = 4
    if h > w * 2:                                # очень вытянутая вертикаль
        n = max(n, 3)
    over = 0.12
    step = h / n
    out = []
    for i in range(n):
        top = max(0, int(step * i - step * over))
        bot = min(h, int(step * (i + 1) + step * over))
        out.append(im.crop((0, top, w, bot)))
    return out


def main():
    img = next((a for a in sys.argv[1:] if os.path.exists(a) and a.lower().endswith(IMG_EXT)), None)
    if not img:
        img = next((a for a in sys.argv[1:] if os.path.exists(a)), None)
    if not img:
        dbg("нет изображения argv=%s" % sys.argv)
        sys.exit("нет изображения")
    if not (KEY and URL):
        sys.exit("нет EXCASH ключей в ~/.openclaw/.env")

    from PIL import Image
    im = Image.open(img)
    dbg("=== %s %s ===" % (os.path.basename(img), im.size))

    # 0) DeepSeek: картинка целиком, максимальное качество — основной путь
    if DS_KEY:
        try:
            data, size, q = _fit(im, budget=DS_BUDGET)
            if data:
                dbg("deepseek целиком %s q%s -> %d КБ" % (size, q, len(data) // 1024))
                txt = ""
                for attempt in range(3):
                    try:
                        txt = _ask_deepseek(data, "jpeg", PROMPT)
                    except Exception as e:
                        code = getattr(getattr(e, "response", None), "status_code", None)
                        dbg("deepseek попытка %d: %s%s" % (
                            attempt + 1, type(e).__name__, " HTTP %s" % code if code else ""))
                        # 4xx — проблема запроса, повтор не поможет; сеть — поможет
                        if code and 400 <= code < 500:
                            break
                        time.sleep(2 + attempt * 3)
                        continue
                    if txt:
                        break
                    dbg("deepseek попытка %d: пустой ответ" % (attempt + 1))
                if txt:
                    dbg("OK deepseek: %d симв" % len(txt))
                    print(txt)
                    return
                dbg("deepseek не ответил — иду на excash")
        except Exception as e:
            dbg("deepseek путь упал: %s" % type(e).__name__)

    # 1) резерв excash: лестница качества под его лимит ~40КБ
    data, size, q = _fit(im)
    if data:
        dbg("целиком %s q%s -> %d КБ" % (size, q, len(data) // 1024))
        txt = _try(data, PROMPT, "целиком")
        # если ужалось сильно, мелкий текст мог потеряться — но пробуем как есть
        if txt and (max(size) >= 860 or max(im.size) <= 900):
            dbg("OK целиком: %d симв" % len(txt))
            print(txt)
            return
        if txt:
            dbg("целиком прочитано, но мелко (%s) — уточняю плитками" % (size,))
            whole = txt
        else:
            whole = ""
    else:
        whole = ""
        dbg("целиком не влезает даже в 480px")

    # 2) плитки
    parts = _tiles(im)
    dbg("режу на %d плиток" % len(parts))
    chunks = []
    for i, tile in enumerate(parts, 1):
        tdata, tsize, tq = _fit(tile)
        if not tdata:
            dbg("плитка %d не влезла" % i)
            continue
        dbg("плитка %d %s q%s -> %d КБ" % (i, tsize, tq, len(tdata) // 1024))
        t = _try(tdata, TILE_PROMPT.format(n=i, total=len(parts)), "плитка %d" % i)
        if t:
            chunks.append("### Фрагмент %d из %d\n%s" % (i, len(parts), t))

    if not chunks:
        if whole:
            print(whole)
            return
        sys.exit("не удалось описать изображение (шлюз отклонил все варианты)")

    head = ("Изображение разобрано по фрагментам (целиком не проходит через шлюз — "
            "ограничение на размер запроса). Ниже части сверху вниз; "
            "соседние фрагменты перекрываются, повторы — это одно и то же место.\n")
    dbg("OK плитками: %d фрагментов" % len(chunks))
    print(head + "\n\n".join(chunks))


main()
