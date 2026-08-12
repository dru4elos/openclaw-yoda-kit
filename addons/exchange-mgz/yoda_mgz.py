#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Мосгорздрав для Йоды через рабочее окружение моста (openclaw venv ломается на EWS).
  unread N | recent N | search "текст" | send --to A --subject S --text T [--cc X]"""
import argparse, logging, re, sys
sys.path.insert(0, "/root/mosgorzdrav_bridge")
logging.getLogger("exchangelib").setLevel(logging.CRITICAL)
import config
from exchangelib import Account, Configuration, Credentials, DELEGATE, NTLM, Message, Mailbox, HTMLBody

def acct():
    cfg = Configuration(service_endpoint=config.MGZ_EWS,
                        credentials=Credentials(config.MGZ_USER, config.MGZ_PASSWORD), auth_type=NTLM)
    return Account(primary_smtp_address=config.MGZ_EMAIL, config=cfg,
                   autodiscover=False, access_type=DELEGATE)

def strip_html(h):
    h = re.sub(r"(?is)<(script|style|head|title)[^>]*>.*?</\1>", " ", h or "")
    h = re.sub(r"(?is)<br\s*/?>", "\n", h)
    h = re.sub(r"(?s)<[^>]+>", " ", h)
    return re.sub(r"\s+", " ", h).strip()

def show(items, label):
    print(f"[mgz] {label} ({len(items)}):")
    if not items:
        print("  — пусто")
    for i, m in enumerate(items, 1):
        who = ""
        try:
            who = f"{m.sender.name} <{m.sender.email_address}>" if m.sender else ""
        except Exception:
            pass
        when = m.datetime_received.strftime("%d.%m %H:%M") if m.datetime_received else ""
        body = strip_html(str(m.body or ""))[:500]
        print(f"  {i}. {when} | От: {who}")
        print(f"     Тема: {m.subject or ''}")
        if body:
            print(f"     Текст: {body}")

def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    u = sub.add_parser("unread"); u.add_argument("n", type=int, nargs="?", default=5)
    r = sub.add_parser("recent"); r.add_argument("n", type=int, nargs="?", default=10)
    s = sub.add_parser("search"); s.add_argument("query"); s.add_argument("--n", type=int, default=10)
    sn = sub.add_parser("send"); sn.add_argument("--to", required=True); sn.add_argument("--cc")
    sn.add_argument("--subject", required=True); sn.add_argument("--text", required=True)
    sn.add_argument("--no-signature", action="store_true"); sn.add_argument("--confirm", default="no")
    a = ap.parse_args()
    A = acct()
    if a.cmd == "unread":
        show(list(A.inbox.filter(is_read=False).order_by("-datetime_received")[:a.n]),
             f"непрочитанные, свежие {a.n}, всего непроч. {A.inbox.unread_count}")
    elif a.cmd == "recent":
        show(list(A.inbox.all().order_by("-datetime_received")[:a.n]), f"последние {a.n}")
    elif a.cmd == "search":
        show(list(A.inbox.filter(subject__contains=a.query).order_by("-datetime_received")[:a.n]),
             f"поиск «{a.query}»")
    else:
        if a.confirm != "yes":
            sys.exit("ОТКАЗ: отправка требует --confirm yes (сначала покажи доктору письмо).")
        sig = "" if a.no_signature else (getattr(config, "MGZ_SIGNATURE", "") or "").replace("\\n", "<br>")
        html = "<div style='font-family:Georgia,serif;font-size:15px'>" + \
               a.text.replace("\n", "<br>") + (("<br><br>" + sig) if sig else "") + "</div>"
        m = Message(account=A, subject=a.subject, body=HTMLBody(html),
                    to_recipients=[Mailbox(email_address=x.strip()) for x in a.to.split(",")],
                    cc_recipients=[Mailbox(email_address=x.strip()) for x in a.cc.split(",")] if a.cc else None)
        m.send_and_save()
        print(f"ОТПРАВЛЕНО (Мосгорздрав) → {a.to} | тема: {a.subject}")

main()
