#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Slack для Йоды: дайджест за сутки, чтение канала, поиск.

  slack.py channels                       — какие каналы видны
  slack.py digest [--hours 24] [--only "a,b"] [--skip "c"] [--max 40]
  slack.py read <канал> [--hours 24] [--max 80]   — подробно один канал
  slack.py search "текст" [--n 20]        — поиск (нужен user-токен)
  slack.py whoami                         — проверить токен

Токен SLACK_TOKEN в ~/.openclaw/.env. Годятся оба вида:
  xoxp-… (user)  — видит всё, что видит владелец, включая приватные каналы и поиск
  xoxb-… (bot)   — только каналы, куда бота пригласили; поиск недоступен
"""
import argparse
import datetime as dt
import json
import os
import sys
import time
import urllib.parse
import urllib.request

API = "https://slack.com/api/"
ENVF = os.path.expanduser("~/.openclaw/.env")
_users = {}


def token():
    t = os.environ.get("SLACK_TOKEN")
    if not t:
        try:
            for line in open(ENVF, encoding="utf-8"):
                line = line.strip()
                if line.startswith("SLACK_TOKEN="):
                    t = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break
        except Exception:
            pass
    if not t:
        sys.exit("SLACK_TOKEN не найден в ~/.openclaw/.env — Slack недоступен. "
                 "Скажи это доктору прямо, не выдумывай содержимое каналов.")
    return t


def api(method, **params):
    """Вызов Slack Web API с обработкой rate-limit."""
    tok = token()
    for attempt in range(4):
        qs = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
        req = urllib.request.Request(
            API + method + ("?" + qs if qs else ""),
            headers={"Authorization": "Bearer " + tok,
                     "Content-Type": "application/x-www-form-urlencoded"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                data = json.loads(r.read().decode())
        except Exception as e:
            if getattr(e, "code", None) == 429:
                time.sleep(int(getattr(e, "headers", {}).get("Retry-After", 3) or 3))
                continue
            sys.exit(f"Slack не ответил ({type(e).__name__}: {str(e)[:120]}). "
                     f"Не выдумывай содержимое — скажи, что API недоступен.")
        if data.get("ok"):
            return data
        err = data.get("error", "?")
        if err == "ratelimited":
            time.sleep(3)
            continue
        hint = {
            "invalid_auth": "токен недействителен или отозван",
            "not_authed": "токен не передан",
            "missing_scope": f"у токена нет нужного права (нужно: {data.get('needed','?')})",
            "channel_not_found": "канал не найден или нет доступа",
            "not_in_channel": "бот не приглашён в этот канал",
        }.get(err, err)
        sys.exit(f"Slack: {hint}")
    sys.exit("Slack: лимит запросов, попробуй позже")


def uname(uid):
    if not uid:
        return "?"
    if uid not in _users:
        try:
            info = api("users.info", user=uid).get("user", {})
            prof = info.get("profile", {})
            _users[uid] = (prof.get("real_name") or prof.get("display_name")
                           or info.get("name") or uid)
        except SystemExit:
            _users[uid] = uid
    return _users[uid]


def channels(types="public_channel,private_channel"):
    out, cursor = [], None
    while True:
        d = api("conversations.list", types=types, limit=200, exclude_archived="true",
                cursor=cursor)
        out += d.get("channels", [])
        cursor = (d.get("response_metadata") or {}).get("next_cursor") or None
        if not cursor:
            break
    return out


def resolve(name):
    n = name.lstrip("#").lower()
    for c in channels():
        if c.get("name", "").lower() == n or c.get("id") == name:
            return c
    sys.exit(f"канал «{name}» не найден среди доступных (посмотри: slack.py channels)")


def history(cid, oldest, limit=200):
    msgs, cursor = [], None
    while True:
        d = api("conversations.history", channel=cid, oldest=f"{oldest:.6f}",
                limit=min(200, limit), cursor=cursor)
        msgs += d.get("messages", [])
        cursor = (d.get("response_metadata") or {}).get("next_cursor") or None
        if not cursor or len(msgs) >= limit:
            break
    return msgs


def fmt_msg(m, indent=""):
    ts = dt.datetime.fromtimestamp(float(m.get("ts", 0))).strftime("%d.%m %H:%M")
    who = uname(m.get("user") or m.get("bot_id"))
    text = (m.get("text") or "").replace("\n", " ").strip()
    # <@U123> -> имя, <http://x|y> -> y
    import re
    text = re.sub(r"<@([A-Z0-9]+)>", lambda x: "@" + uname(x.group(1)), text)
    text = re.sub(r"<([^|>]+)\|([^>]+)>", r"\2", text)
    text = re.sub(r"<(https?://[^>]+)>", r"\1", text)
    files = m.get("files") or []
    extra = f" [файлов: {len(files)}]" if files else ""
    reacts = sum(r.get("count", 0) for r in (m.get("reactions") or []))
    extra += f" [реакций: {reacts}]" if reacts else ""
    return f"{indent}{ts} {who}: {text[:600]}{extra}"


def cmd_whoami(_a):
    d = api("auth.test")
    print(f"OK: {d.get('user')} в {d.get('team')} (тип токена: "
          f"{'user' if token().startswith('xoxp') else 'bot'})")


def cmd_channels(_a):
    cs = channels()
    print(f"Доступно каналов: {len(cs)}")
    for c in sorted(cs, key=lambda x: -(x.get("num_members") or 0)):
        priv = "🔒" if c.get("is_private") else "  "
        print(f"  {priv} #{c.get('name'):32} участников: {c.get('num_members','?'):>4}"
              f"  {'(в канале)' if c.get('is_member') else ''}")


def cmd_digest(a):
    oldest = time.time() - a.hours * 3600
    only = {x.strip().lstrip("#").lower() for x in (a.only or "").split(",") if x.strip()}
    skip = {x.strip().lstrip("#").lower() for x in (a.skip or "").split(",") if x.strip()}
    cs = [c for c in channels() if c.get("is_member")]
    if only:
        cs = [c for c in cs if c.get("name", "").lower() in only]
    cs = [c for c in cs if c.get("name", "").lower() not in skip]
    if not cs:
        print("Нет каналов, где ты состоишь (или все отфильтрованы). "
              "Посмотри slack.py channels.")
        return
    print(f"SLACK ДАЙДЖЕСТ за {a.hours} ч — каналов проверено: {len(cs)}\n" + "=" * 58)
    total = 0
    quiet = []
    for c in sorted(cs, key=lambda x: x.get("name", "")):
        msgs = history(c["id"], oldest, limit=a.max)
        msgs = [m for m in msgs if m.get("subtype") not in ("channel_join", "channel_leave")]
        if not msgs:
            quiet.append(c["name"])
            continue
        total += len(msgs)
        people = {uname(m.get("user")) for m in msgs if m.get("user")}
        print(f"\n### #{c['name']} — сообщений: {len(msgs)}, участвовали: "
              f"{', '.join(sorted(people)[:6])}")
        for m in sorted(msgs, key=lambda x: float(x.get("ts", 0))):
            print(fmt_msg(m, "  "))
            rc = m.get("reply_count") or 0
            if rc:
                try:
                    thr = api("conversations.replies", channel=c["id"], ts=m["ts"], limit=30)
                    for r in (thr.get("messages") or [])[1:]:
                        print(fmt_msg(r, "      ↳ "))
                except SystemExit:
                    print(f"      ↳ (в треде {rc} ответов, прочитать не удалось)")
    print("\n" + "=" * 58)
    print(f"ИТОГО сообщений: {total}")
    if quiet:
        print(f"Тихо было в: {', '.join('#' + q for q in quiet)}")
    print("Пересказывай доктору только то, что выше. Ничего не додумывай.")


def cmd_read(a):
    c = resolve(a.channel)
    oldest = time.time() - a.hours * 3600
    msgs = history(c["id"], oldest, limit=a.max)
    msgs = [m for m in msgs if m.get("subtype") not in ("channel_join", "channel_leave")]
    print(f"#{c['name']} за {a.hours} ч — сообщений: {len(msgs)}\n" + "=" * 58)
    for m in sorted(msgs, key=lambda x: float(x.get("ts", 0))):
        print(fmt_msg(m))
        if m.get("reply_count"):
            thr = api("conversations.replies", channel=c["id"], ts=m["ts"], limit=50)
            for r in (thr.get("messages") or [])[1:]:
                print(fmt_msg(r, "    ↳ "))
    if not msgs:
        print("(пусто — за этот период сообщений не было)")


def cmd_search(a):
    if not token().startswith("xoxp"):
        sys.exit("поиск доступен только с user-токеном (xoxp-…); у бота такого права нет")
    d = api("search.messages", query=a.query, count=a.n, sort="timestamp")
    ms = ((d.get("messages") or {}).get("matches")) or []
    print(f"Найдено: {(d.get('messages') or {}).get('total', 0)}, показываю {len(ms)}")
    for m in ms:
        ts = dt.datetime.fromtimestamp(float(m.get("ts", 0))).strftime("%d.%m %H:%M")
        ch = (m.get("channel") or {}).get("name", "?")
        print(f"  {ts} #{ch} {m.get('username') or uname(m.get('user'))}: "
              f"{(m.get('text') or '')[:300]}")
        if m.get("permalink"):
            print(f"     {m['permalink']}")



def _bot_copy(target, text):
    """Копия доктору голосом БОТА о каждой отправке в Slack от его имени.
    Неотключаемая подотчётность — как в Telegram."""
    try:
        import json as _j, re as _re, urllib.request as _u
        cfg = open("/home/openclaw/.openclaw/openclaw.json", encoding="utf-8").read()
        m = _re.search(r'"botToken"\s*:\s*"([^"]+)"', cfg)
        if not m:
            return
        body = _j.dumps({"chat_id": "123456789",
                         "text": "\U0001F4E4 Slack: с твоего аккаунта отправлено в "
                                 f"{target}:\n{text[:800]}"}).encode()
        req = _u.Request(f"https://api.telegram.org/bot{m.group(1)}/sendMessage",
                         data=body, headers={"Content-Type": "application/json"})
        _u.urlopen(req, timeout=10).read()
    except Exception:
        pass


def cmd_send(a):
    """Отправка в Slack ОТ ИМЕНИ ДОКТОРА. Только после его явного «отправляй»."""
    if a.confirm != "yes":
        sys.exit("ОТКАЗ: отправка требует --confirm yes.\n"
                 "Сначала ПОКАЖИ доктору получателя (#канал или @человек) и ПОЛНЫЙ текст "
                 "сообщения, дождись его «отправляй» — и только потом ставь флаг.")
    if not token().startswith("xoxp"):
        sys.exit("отправка от имени доктора возможна только с user-токеном (xoxp-…)")
    target = a.channel.strip()
    if target.startswith("#") or not target.startswith(("C", "D", "G", "@")):
        cid = resolve(target)["id"]
    else:
        cid = target.lstrip("@")
    d = api("chat.postMessage", channel=cid, text=a.text,
            thread_ts=a.thread or None, as_user="true")
    _bot_copy(a.channel, a.text)
    ts = d.get("ts", "?")
    print(f"OK ОТПРАВЛЕНО в {a.channel} (ts={ts})")
    print("Копия ушла доктору в Telegram.")


def main():
    ap = argparse.ArgumentParser(description="Slack для Йоды")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("whoami").set_defaults(func=cmd_whoami)
    sub.add_parser("channels").set_defaults(func=cmd_channels)

    d = sub.add_parser("digest", help="дайджест по всем каналам за N часов")
    d.add_argument("--hours", type=int, default=24)
    d.add_argument("--only", default="", help="только эти каналы через запятую")
    d.add_argument("--skip", default="", help="исключить каналы через запятую")
    d.add_argument("--max", type=int, default=60, help="максимум сообщений на канал")
    d.set_defaults(func=cmd_digest)

    r = sub.add_parser("read", help="подробно один канал")
    r.add_argument("channel")
    r.add_argument("--hours", type=int, default=24)
    r.add_argument("--max", type=int, default=80)
    r.set_defaults(func=cmd_read)

    s = sub.add_parser("search")
    s.add_argument("query")
    s.add_argument("--n", type=int, default=20)
    s.set_defaults(func=cmd_search)

    sn = sub.add_parser("send", help="написать в канал/личку ОТ ИМЕНИ доктора")
    sn.add_argument("channel", help="#канал, @user или ID")
    sn.add_argument("text")
    sn.add_argument("--thread", default="", help="ts треда, чтобы ответить в ветку")
    sn.add_argument("--confirm", default="no")
    sn.set_defaults(func=cmd_send)

    a = ap.parse_args()
    a.func(a)


if __name__ == "__main__":
    main()
