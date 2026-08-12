#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Зрение для Йоды: описание присланных фото через excash Gemini 3.1 Pro.
OpenClaw вызывает: vision.py <путь к изображению> (плейсхолдер {{MediaPath}}).
Печатает подробное описание в stdout — оно уходит основной модели как контекст."""
import base64, os, sys
import requests

DEBUG = "/tmp/vision_debug.log"
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

def main():
    img = next((a for a in sys.argv[1:] if os.path.exists(a) and a.lower().endswith(IMG_EXT)), None)
    if not img:
        img = next((a for a in sys.argv[1:] if os.path.exists(a)), None)
    if not img:
        dbg(f"нет изображения argv={sys.argv}")
        sys.exit("нет изображения")
    if not (KEY and URL):
        sys.exit("нет EXCASH ключей в ~/.openclaw/.env")
    ext = os.path.splitext(img)[1].lstrip(".").lower()
    mime = {"jpg": "jpeg", "heic": "jpeg", "heif": "jpeg"}.get(ext, ext) or "jpeg"
    b64 = base64.b64encode(open(img, "rb").read()).decode()
    txt = ""
    for attempt in range(3):
        txt = _ask(b64, mime)
        if txt:
            break
        dbg(f"попытка {attempt+1}: пусто, повтор")
    if not txt:
        sys.exit("не удалось описать изображение")
    dbg(f"OK {os.path.basename(img)}: {len(txt)} симв")
    print(txt)


def _ask(b64, mime):
    r = requests.post(URL.rstrip("/") + "/chat/completions",
        headers={"Authorization": "Bearer " + KEY, "Content-Type": "application/json"},
        json={"model": "gpt-5.6-sol", "max_tokens": 8000, "temperature": 0.2,
              "messages": [{"role": "user", "content": [
                  {"type": "image_url", "image_url": {"url": f"data:image/{mime};base64,{b64}"}},
                  {"type": "text", "text": PROMPT}]}]}, timeout=240)
    r.raise_for_status()
    msg = ((r.json().get("choices") or [{}])[0] or {}).get("message") or {}
    c = msg.get("content")
    if isinstance(c, list):
        c = "\n".join(x.get("text", "") for x in c if isinstance(x, dict))
    return (c or "").strip()

main()
