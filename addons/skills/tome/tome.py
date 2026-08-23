#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Йода → доктор: отправка файлов и ссылок/сообщений в Telegram.
Решает «файл застрял на сервере»: любой путь на vps → тебе в личку ботом.
Прямой канал к Telegram, при сбое — через WARP socks (как в мостах).

  tome.py msg "текст или ссылка https://..."
  tome.py file /путь/к/файлу [--caption "подпись"]
  tome.py photo /путь/к/картинке [--caption "подпись"]   # как фото, а не документ
"""
import argparse
import time, os, re, sys
import requests

CFG = os.path.expanduser("~/.openclaw/openclaw.json")
OWNER = 123456789  # <-- ваш Telegram ID (узнать: @userinfobot)

def token():
    s = open(CFG, encoding="utf-8").read()
    m = re.search(r'botToken"\s*:\s*"([0-9]+:[A-Za-z0-9_-]+)"', s)
    if not m:
        sys.exit("botToken не найден в openclaw.json")
    return m.group(1)

BASE = f"https://api.telegram.org/bot{token()}"
PROXY = {"https": "socks5h://127.0.0.1:40111", "http": "socks5h://127.0.0.1:40111"}

def call(method, data=None, files=None):
    last = None
    for proxies in (None, PROXY):          # сначала напрямую, потом через WARP
        try:
            r = requests.post(f"{BASE}/{method}", data=data, files=files,
                              proxies=proxies, timeout=90)
            j = r.json()
            if j.get("ok"):
                return j
            last = j.get("description")
            if last and "chat not found" in last.lower():
                break  # проксей не поможет
        except Exception as e:
            last = f"{type(e).__name__}: {e}"
    sys.exit(f"Telegram API error ({method}): {last}")


TG_LIMIT = 3900          # с запасом от 4096


def _split_msg(text):
    """Режем по абзацам, потом по строкам — чтобы не рвать статью посередине."""
    text = (text or "").strip()
    if len(text) <= TG_LIMIT:
        return [text] if text else []
    parts, cur = [], ""
    for block in text.split("\n\n"):
        if len(block) > TG_LIMIT:                     # огромный блок — по строкам
            for line in block.split("\n"):
                if len(cur) + len(line) + 1 > TG_LIMIT:
                    parts.append(cur.rstrip()); cur = ""
                cur += line + "\n"
            continue
        if len(cur) + len(block) + 2 > TG_LIMIT:
            parts.append(cur.rstrip()); cur = ""
        cur += block + "\n\n"
    if cur.strip():
        parts.append(cur.rstrip())
    return parts


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("msg"); m.add_argument("text")
    mf = sub.add_parser("msgfile", help="отправить текст из файла, длинное режется")
    mf.add_argument("path")
    f = sub.add_parser("file"); f.add_argument("path"); f.add_argument("--caption", default="")
    p = sub.add_parser("photo"); p.add_argument("path"); p.add_argument("--caption", default="")
    a = ap.parse_args()

    if a.cmd in ("msg", "msgfile"):
        if a.cmd == "msgfile":
            if not os.path.exists(a.path):
                sys.exit(f"файл не найден: {a.path}")
            text = open(a.path, encoding="utf-8", errors="replace").read()
        else:
            text = a.text
        parts = _split_msg(text)
        if not parts:
            sys.exit("пустой текст — нечего отправлять")
        for i, part in enumerate(parts, 1):
            suffix = f"\n\n— часть {i} из {len(parts)}" if len(parts) > 1 else ""
            call("sendMessage", data={"chat_id": OWNER, "text": part + suffix,
                                      "disable_web_page_preview": True})
            if i < len(parts):
                time.sleep(0.4)
        print(f"✓ отправлено доктору ({len(parts)} сообщ., {len(text)} символов)")
        return

    if not os.path.exists(a.path):
        sys.exit(f"файл не найден: {a.path}")
    size = os.path.getsize(a.path)
    if size > 49 * 1024 * 1024:
        sys.exit(f"файл {size // 1024 // 1024}МБ больше лимита Telegram (~50МБ) — не отправить")
    method = "sendPhoto" if a.cmd == "photo" else "sendDocument"
    field = "photo" if a.cmd == "photo" else "document"
    with open(a.path, "rb") as fh:
        call(method, data={"chat_id": OWNER, "caption": a.caption},
             files={field: (os.path.basename(a.path), fh)})
    print(f"✓ отправлено доктору ({os.path.basename(a.path)}, {size // 1024}КБ)")

main()
