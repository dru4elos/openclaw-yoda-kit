#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Telegram доктора для Йоды — ЧЕРЕЗ ЕДИНЫЙ ШЛЮЗ tg-gateway (127.0.0.1:8099).

  yoda_tg.py dialogs [--n 25]
  yoda_tg.py read "<чат>" [--n 30]
  yoda_tg.py search "<текст>" [--chat "<чат>"] [--n 20]
  yoda_tg.py media "<чат>" [--n 5]      -> фото в /tmp/tg_*.jpg, печатает пути
  yoda_tg.py digest [--folder СТАРТАПЫ] [--hours 24]
  yoda_tg.py send "<чат>" "<текст>" --confirm yes
  yoda_tg.py sendfile "<чат>" <путь> [--caption ""] --confirm yes

СВОЮ TELETHON-СЕССИЮ ЗДЕСЬ ЗАВОДИТЬ НЕЛЬЗЯ. Раньше скрипт держал отдельную
(loft_outreach/cancel_sess) и она регулярно умирала с AuthKeyDuplicatedError:
Телеграм аннулирует ключ, если им подключаются с двух IP. Единственное
подключение держит шлюз, все остальные ходят к нему по HTTP.
"""
import argparse
import os
import sys
from datetime import datetime, timedelta, timezone

import requests

GW = os.environ.get("TG_GATEWAY_URL", "http://127.0.0.1:8099")
MEDIA_DIR = "/tmp"


def gw(path, method="GET", **kw):
    """Запрос к шлюзу. Понятная ошибка вместо стектрейса телетона."""
    try:
        r = (requests.post(GW + path, json=kw.get("json") or {}, timeout=kw.get("timeout", 300))
             if method == "POST" else
             requests.get(GW + path, params=kw.get("params") or {}, timeout=kw.get("timeout", 300)))
        r.raise_for_status()
        return r.json()
    except requests.HTTPError as e:
        sys.exit(f"шлюз ответил {e.response.status_code}: {e.response.text[:200]}")
    except Exception as e:
        sys.exit(f"шлюз недоступен ({type(e).__name__}). Проверь: systemctl status tg-gateway")


def _when(iso, fmt="%d.%m %H:%M"):
    if not iso:
        return ""
    try:
        return datetime.fromisoformat(iso).strftime(fmt)
    except Exception:
        return ""


def cmd_dialogs(a):
    print(f"Последние {a.n} диалогов:")
    for d in gw("/dialogs", params={"limit": a.n}).get("dialogs") or []:
        unread = f" [непроч.{d['unread']}]" if d.get("unread") else ""
        last = (d.get("last") or "").replace("\n", " ")[:70]
        print(f"- {d['name']}{unread} | {_when(d.get('date'))} | {last}")


def cmd_read(a):
    r = gw("/read", params={"chat": a.chat, "limit": a.n})
    msgs = r.get("messages") or []
    print(f"Чат: {a.chat} — последние {len(msgs)} сообщений (снизу вверх свежие):")
    lines = []
    for m in msgs:
        who = "Я" if m.get("out") else (m.get("sender") or "?")
        body = (m.get("text") or "").replace("\n", " ")
        if not body and m.get("has_media"):
            body = "[медиа]"
        lines.append(f"[{_when(m.get('date'))}] {who}: {body[:400]}")
    for line in reversed(lines):
        print(line)


def cmd_search(a):
    params = {"query": a.query, "limit": a.n}
    if a.chat:
        params["chat"] = a.chat
    r = gw("/search", params=params)
    print(f"Поиск «{a.query}»" + (f" в «{a.chat}»" if a.chat else " по всем чатам") + ":")
    found = r.get("messages") or []
    for m in found:
        print(f"- [{_when(m.get('date'), '%d.%m.%y %H:%M')}] {m.get('chat')} | "
              f"{m.get('sender') or '?'}: {(m.get('text') or '')[:200]}")
    if not found:
        print("(ничего не найдено)")


def cmd_media(a):
    r = gw("/media", method="POST", json={"chat": a.chat, "limit": a.n, "dir": MEDIA_DIR})
    files = r.get("files") or []
    print(f"Картинки из «{a.chat}» (последние {len(files)}):")
    for f in files:
        cap = (f.get("caption") or "").replace("\n", " ")[:60]
        print(f"{f['path']} | {_when(f.get('date'))} | {cap}")
    if not files:
        print("(картинок не найдено)")


def _excash():
    env = {}
    for p in ("/home/openclaw/.openclaw/.env", "/home/knee_bot/keys.env"):
        if os.path.exists(p):
            for line in open(p, encoding="utf-8"):
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    env.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    return env.get("EXCASH_API_KEY", ""), env.get("EXCASH_API_URL", "")


def _sum_llm(text, folders):
    k, u = _excash()
    if not k:
        return "[нет ключа LLM — сырые заголовки]\n" + text[:3000]
    prompt = (
        "Ниже — посты за последние сутки из Telegram-каналов доктора "
        f"(папки: {folders}). Сделай ДАЙДЖЕСТ на русском для занятого человека:\n\n"
        "## Главное\n(3-5 пунктов — что действительно стоит внимания, с указанием канала)\n"
        "## Стартапы и продукт\n## DS / ML / ИИ\n## Мимо кассы\n(одной строкой, что было шумом)\n\n"
        "Правила: только по существу, без воды и без пересказа рекламы. Если пост важный — "
        "поясни ЧЕМ именно. Дубли из разных каналов объединяй. Максимум 350 слов."
    )
    try:
        r = requests.post(u.rstrip("/") + "/chat/completions",
                          headers={"Authorization": "Bearer " + k, "Content-Type": "application/json"},
                          json={"model": "gemini-3.1-pro", "max_tokens": 8000, "temperature": 0.3,
                                "messages": [{"role": "user",
                                              "content": prompt + "\n\n=== ПОСТЫ ===\n" + text[:250000]}]},
                          timeout=600)
        r.raise_for_status()
        return ((r.json().get("choices") or [{}])[0].get("message", {}) or {}).get("content", "").strip()
    except Exception as e:
        return f"[ошибка LLM: {type(e).__name__}]\n" + text[:2000]


def cmd_digest(a):
    want = [x.strip().lower() for x in (a.folder or ["СТАРТАПЫ", "DS ML"])]
    all_folders = gw("/folders", timeout=600).get("folders") or []
    peers, names = [], []
    for fl in all_folders:
        if fl["title"].strip().lower() in want:
            names.append(fl["title"])
            peers.extend(fl.get("peers") or [])
    if not peers:
        have = ", ".join(f["title"] for f in all_folders)
        sys.exit(f"папки не найдены: {', '.join(want)}. Есть: {have}")

    cutoff = datetime.now(timezone.utc) - timedelta(hours=a.hours)
    chunks, total = [], 0
    for p in peers:
        target = p.get("username") or str(p["id"])
        try:
            msgs = gw("/read", params={"chat": target, "limit": 40}).get("messages") or []
        except SystemExit:
            continue
        posts = []
        for m in msgs:
            try:
                dt = datetime.fromisoformat(m["date"]) if m.get("date") else None
            except Exception:
                dt = None
            if dt and dt < cutoff:
                break
            txt = (m.get("text") or "").strip()
            if len(txt) > 40:
                posts.append(txt[:1200])
        if posts:
            total += len(posts)
            chunks.append(f"### Канал: {p['name']}\n" + "\n---\n".join(posts[:12]))

    if not chunks:
        print(f"За {a.hours} ч в папках {', '.join(names)} ничего нового.")
        return
    print(f"Собрано {total} постов из {len(chunks)} каналов ({', '.join(names)}). Готовлю выжимку…",
          file=sys.stderr)
    out = _sum_llm("\n\n".join(chunks), ", ".join(names))
    print(f"📰 Дайджест за {a.hours} ч — {', '.join(names)}\n\n" + out)


def _resolve_strict(chat):
    """Для отправки: только точное совпадение — чтобы не написать не тому человеку."""
    chat = (chat or "").strip()
    if not chat:
        sys.exit("нужно указать чат")
    low = chat.lower()
    if low in ("me", "saved", "savedmessages", "избранное", "избранные", "себе"):
        return "me"
    if chat.lstrip("-").isdigit() or chat.startswith("@"):
        return chat
    dialogs = gw("/dialogs", params={"limit": 400}).get("dialogs") or []
    exact = [d for d in dialogs if (d.get("name") or "").strip().lower() == low]
    if len(exact) == 1:
        return str(exact[0]["id"])
    if len(exact) > 1:
        sys.exit(f"несколько чатов с именем «{chat}» — укажи @username или id")
    part = [d["name"] for d in dialogs if low in (d.get("name") or "").strip().lower()]
    if part:
        sys.exit("точного чата «%s» нет. Похожие: %s. Уточни ПОЛНОЕ имя, @username или id."
                 % (chat, "; ".join(part[:8])))
    sys.exit(f"чат «{chat}» не найден — уточни @username или id")


OWNER_ID = "123456789"  # <-- ваш Telegram ID

def _bot_copy(target, text):
    """Копия доктору голосом БОТА о каждой отправке с его личного аккаунта.
    Неотключаемая подотчётность после инцидента (агент сам ответил третьему лицу)."""
    try:
        import json as _j, re as _re, urllib.request as _u
        cfg = open("/home/openclaw/.openclaw/openclaw.json", encoding="utf-8").read()
        m = _re.search(r'"botToken"\s*:\s*"([^"]+)"', cfg)
        if not m:
            return
        token = m.group(1)
        body = _j.dumps({"chat_id": OWNER_ID,
                         "text": f"\U0001F4E4 С твоего аккаунта отправлено в [{target}]:\n{text[:800]}"}).encode()
        req = _u.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=body,
                         headers={"Content-Type": "application/json"})
        _u.urlopen(req, timeout=10).read()
    except Exception:
        pass  # копия best-effort: сбой уведомления не должен ломать отправку


def cmd_send(a):
    """Отправка от имени доктора. Требует --confirm yes."""
    if a.confirm != "yes":
        sys.exit("ОТКАЗ: отправка требует --confirm yes. Сначала покажи доктору получателя и "
                 "полный текст, получи явное согласие.")
    target = _resolve_strict(a.chat)
    gw("/send", method="POST", json={"chat": target, "text": a.text})
    with open("/root/yoda_tg_sent.log", "a", encoding="utf-8") as fh:
        fh.write(f"{datetime.now():%Y-%m-%d %H:%M} | {a.chat} | {a.text[:200]}\n")
    _bot_copy(a.chat, a.text)
    print(f"OK ОТПРАВЛЕНО в [{a.chat}]: {a.text[:120]}")


def cmd_sendfile(a):
    """Отправка файла от имени доктора. Требует --confirm yes."""
    if a.confirm != "yes":
        sys.exit("ОТКАЗ: отправка требует --confirm yes.")
    if not os.path.exists(a.path):
        sys.exit(f"файл не найден: {a.path}")
    target = _resolve_strict(a.chat)
    gw("/sendfile", method="POST", json={"chat": target, "path": a.path, "caption": a.caption})
    with open("/root/yoda_tg_sent.log", "a", encoding="utf-8") as fh:
        fh.write(f"file | {a.chat} | {os.path.basename(a.path)}\n")
    _bot_copy(a.chat, f"[файл] {a.path}")
    print(f"OK ОТПРАВЛЕН ФАЙЛ в [{a.chat}]: {os.path.basename(a.path)}")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("dialogs"); d.add_argument("--n", type=int, default=25)
    r = sub.add_parser("read"); r.add_argument("chat"); r.add_argument("--n", type=int, default=30)
    s = sub.add_parser("search"); s.add_argument("query"); s.add_argument("--chat")
    s.add_argument("--n", type=int, default=20)
    md = sub.add_parser("media"); md.add_argument("chat"); md.add_argument("--n", type=int, default=5)
    dg = sub.add_parser("digest"); dg.add_argument("--folder", action="append")
    dg.add_argument("--hours", type=int, default=24)
    sn = sub.add_parser("send"); sn.add_argument("chat"); sn.add_argument("text")
    sn.add_argument("--confirm", default="no")
    sf = sub.add_parser("sendfile"); sf.add_argument("chat"); sf.add_argument("path")
    sf.add_argument("--caption", default=""); sf.add_argument("--confirm", default="no")
    a = ap.parse_args()

    {"dialogs": cmd_dialogs, "read": cmd_read, "search": cmd_search, "media": cmd_media,
     "digest": cmd_digest, "send": cmd_send, "sendfile": cmd_sendfile}[a.cmd](a)


if __name__ == "__main__":
    main()
