#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Почта доктора для Йоды: чтение и отправка. Три ящика:
  yandex  — IMAP/SMTP (imap.yandex.ru / smtp.yandex.ru)
  gmail   — IMAP/SMTP (imap.gmail.com / smtp.gmail.com)
  mgz     — Мосгорздрав, Exchange EWS (owa.mos.ru), NTLM

Команды:
  read  --account yandex|gmail|mgz|all [--unread N | --recent N | --search TXT | --full UID]
  send  --account yandex|gmail|mgz --to A --subject S --text BODY [--cc A] [--no-signature] --confirm yes

Отправка требует --confirm yes (защита от случайной отправки) и пишет аудит в sent.log.
Подпись доктора добавляется автоматически (кроме --no-signature).
"""
import argparse, email, imaplib, os, re, smtplib, ssl, sys, datetime
import datetime as _dt
import email.utils as _eu
_re_uid = re.compile(r'UID (\d+)')
from email.header import decode_header, make_header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

HERE = os.path.dirname(os.path.abspath(__file__))
SENT_LOG = os.path.join(HERE, 'sent.log')

# ---------- env ----------
ENV = {}
for _p in (os.path.expanduser('~/.openclaw/.env'),):
    if os.path.exists(_p):
        for line in open(_p, encoding='utf-8'):
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                k, v = line.split('=', 1)
                ENV[k.strip()] = v.strip().strip('"').strip("'")

def env(*names, default=None):
    for n in names:
        if ENV.get(n):
            return ENV[n]
    return default

YA_EMAIL = env('YA_EMAIL')
YA_PW    = env('YA_APP_PASSWORD')
YA_SIG   = env('YA_SIGNATURE', default='').replace('\\n', chr(10))
GM_EMAIL = env('GMAIL_EMAIL', 'SCIENCE_GMAIL_USER', default='you@example.com')
GM_PW    = env('GMAIL_APP_PASSWORD', 'GMAIL_PW', 'SCIENCE_GMAIL_PASSWORD')
MGZ_EMAIL= env('MGZ_EMAIL')
MGZ_USER = env('MGZ_USER')
MGZ_PW   = env('MGZ_PASSWORD')
MGZ_EWS  = env('MGZ_EWS')
MGZ_SIG  = env('MGZ_SIGNATURE', default='').replace('\\n', chr(10))

IMAP = {
    'yandex': ('imap.yandex.ru', YA_EMAIL, YA_PW),
    'gmail':  ('imap.gmail.com', GM_EMAIL, (GM_PW or '').replace(' ', '')),
}
SMTP = {
    'yandex': ('smtp.yandex.ru', 465, YA_EMAIL, YA_PW, YA_SIG),
    'gmail':  ('smtp.gmail.com', 465, GM_EMAIL, (GM_PW or '').replace(' ', ''), YA_SIG),
}
ALL_ACCOUNTS = ['yandex', 'gmail', 'mgz']

def configured(acc):
    if acc == 'mgz':
        return all([MGZ_EMAIL, MGZ_USER, MGZ_PW, MGZ_EWS])
    if acc == 'yandex':
        return bool(YA_EMAIL and YA_PW)
    if acc == 'gmail':
        return bool(GM_EMAIL and GM_PW)
    return False

# ---------- helpers ----------
def dec(s):
    try:
        return str(make_header(decode_header(s or '')))
    except Exception:
        return s or ''

def strip_html(h):
    h = re.sub(r'(?is)<(script|style|head|title)[^>]*>.*?</\1>', ' ', h)
    h = re.sub(r'(?is)<br\s*/?>', '\n', h)
    h = re.sub(r'(?is)</(p|div|tr|li|h[1-6]|table|ul|ol|blockquote)\s*>', '\n', h)
    h = re.sub(r'(?s)<[^>]+>', '', h)
    import html as _h
    return re.sub(r'[ \t]{2,}', ' ', _h.unescape(h)).strip()

def body_text(msg, limit=800):
    part = None
    if msg.is_multipart():
        for pt in msg.walk():
            if pt.get_content_type() == 'text/plain':
                part = pt; break
        if part is None:
            for pt in msg.walk():
                if pt.get_content_type() == 'text/html':
                    part = pt; break
    else:
        part = msg
    if part is None:
        return ''
    try:
        raw = part.get_payload(decode=True) or b''
        txt = raw.decode(part.get_content_charset() or 'utf-8', 'replace')
    except Exception:
        return ''
    if part.get_content_type() == 'text/html':
        txt = strip_html(txt)
    return re.sub(r'\s+', ' ', txt).strip()[:limit]

def prn(acc, items, total=None, label=''):
    head = f'[{acc}] {label}'
    if total is not None:
        head += f' (всего: {total})'
    print(head)
    if not items:
        print('  — пусто')
    for i, it in enumerate(items, 1):
        print(f"  {i}. UID={it.get('uid','-')} | {it['date']} | От: {it['from']}")
        print(f"     Тема: {it['subj']}")
        if it.get('body'):
            print(f"     Текст: {it['body']}")
    print()

# ---------- IMAP read (yandex/gmail) ----------
def imap_read(acc, a):
    host, user, pwd = IMAP[acc]
    M = imaplib.IMAP4_SSL(host, 993)
    M.login(user, pwd)
    M.select('INBOX', readonly=True)
    def fetch(uids, body=False, limit=800):
        """body=False -> только заголовки ОДНИМ запросом (быстро, для списков).
        body=True -> тело письма, по одному (медленно, только для --full)."""
        if not uids:
            return []
        if not body:
            uid_set = b','.join(u if isinstance(u, bytes) else str(u).encode() for u in uids)
            typ, data = M.uid('fetch', uid_set,
                              '(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])')
            if typ != 'OK':
                return []
            out, order = [], [u.decode() if isinstance(u, bytes) else str(u) for u in uids]
            for part in data:
                if not isinstance(part, tuple) or len(part) < 2:
                    continue
                head = part[0].decode(errors='replace') if isinstance(part[0], bytes) else str(part[0])
                mm = _re_uid.search(head)
                m = email.message_from_bytes(part[1])
                out.append({'uid': mm.group(1) if mm else '?',
                            'from': dec(m.get('From')),
                            'date': (m.get('Date') or '')[:22],
                            'subj': dec(m.get('Subject'))})
            pos = {u: i for i, u in enumerate(order)}
            out.sort(key=lambda it: pos.get(it['uid'], 10**6))
            return out
        out = []
        for uid in uids:
            typ, data = M.uid('fetch', uid, '(BODY.PEEK[])')
            if typ != 'OK' or not data or data[0] is None:
                continue
            m = email.message_from_bytes(data[0][1])
            out.append({'uid': uid.decode() if isinstance(uid, bytes) else str(uid),
                        'from': dec(m.get('From')), 'date': (m.get('Date') or '')[:22],
                        'subj': dec(m.get('Subject')), 'body': body_text(m, limit)})
        return out
    try:
        if a.full:
            prn(acc, fetch([a.full.encode()], body=True, limit=6000), label=f'письмо UID={a.full}')
        elif a.search:
            _, d = M.uid('search', None, 'ALL')
            uids = d[0].split()[-120:]
            hits = [it for it in fetch(uids) if a.search.lower() in (it['from'] + ' ' + it['subj']).lower()]
            prn(acc, hits[-12:], total=len(hits), label=f'поиск «{a.search}» в последних 120')
        elif getattr(a, 'hours', None):
            # SINCE фильтрует НА СЕРВЕРЕ (гранулярность — сутки), точную отсечку
            # по часам делаем уже по заголовкам
            since = (_dt.datetime.now() - _dt.timedelta(hours=a.hours + 24)).strftime('%d-%b-%Y')
            _, d = M.uid('search', None, f'(SINCE {since})')
            uids = d[0].split()[-200:]
            items = fetch(uids[::-1])
            cutoff = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(hours=a.hours)
            fresh = []
            for it in items:
                try:
                    dtv = _eu.parsedate_to_datetime(it['date'])
                    if dtv.tzinfo is None:
                        dtv = dtv.replace(tzinfo=_dt.timezone.utc)
                    if dtv >= cutoff:
                        fresh.append(it)
                except Exception:
                    fresh.append(it)
            prn(acc, fresh, total=len(fresh), label=f'за последние {a.hours} ч')
        elif a.recent:
            _, d = M.uid('search', None, 'ALL')
            uids = d[0].split()
            prn(acc, fetch(uids[-a.recent:][::-1]), total=len(uids), label=f'последние {a.recent}')
        else:
            n = a.unread or 5
            _, d = M.uid('search', None, 'UNSEEN')
            uids = d[0].split()
            prn(acc, fetch(uids[-n:][::-1]), total=len(uids), label=f'непрочитанные, свежие {n}')
    finally:
        try: M.logout()
        except Exception: pass

# ---------- EWS (mgz) ----------
def ews_account():
    import logging
    logging.getLogger('exchangelib').setLevel(logging.ERROR)
    from exchangelib import Account, Configuration, Credentials, DELEGATE, NTLM
    cfg = Configuration(service_endpoint=MGZ_EWS,
                        credentials=Credentials(MGZ_USER, MGZ_PW), auth_type=NTLM)
    return Account(primary_smtp_address=MGZ_EMAIL, config=cfg,
                   autodiscover=False, access_type=DELEGATE)

def ews_read(a):
    acc = ews_account()
    def to_item(it):
        try:
            body = strip_html(str(it.body or ''))[:800]
        except Exception:
            body = ''
        frm = ''
        try:
            frm = f"{it.sender.name} <{it.sender.email_address}>" if it.sender else ''
        except Exception:
            pass
        d = it.datetime_received.strftime('%d %b %Y %H:%M') if it.datetime_received else ''
        return {'uid': str(getattr(it, 'id', '') or '')[:12], 'from': frm, 'date': d,
                'subj': it.subject or '', 'body': re.sub(r'\s+', ' ', body).strip()}
    if a.search:
        qs = acc.inbox.filter(subject__contains=a.search).order_by('-datetime_received')[:12]
        items = [to_item(x) for x in qs]
        prn('mgz', items, label=f'поиск «{a.search}» по теме')
    elif a.recent:
        qs = acc.inbox.all().order_by('-datetime_received')[:a.recent]
        prn('mgz', [to_item(x) for x in qs], total=acc.inbox.total_count, label=f'последние {a.recent}')
    else:
        n = a.unread or 5
        qs = acc.inbox.filter(is_read=False).order_by('-datetime_received')[:n]
        items = [to_item(x) for x in qs]
        prn('mgz', items, total=acc.inbox.unread_count, label=f'непрочитанные, свежие {n}')

# ---------- send ----------
def as_html(text, sig):
    import html as _h
    body = _h.escape(text).replace('\n', '<br>')
    block = f'<div style="font-family:Georgia,serif;font-size:15px;line-height:1.5;color:#1a1a1a">{body}'
    if sig:
        sig_html = sig if '<' in sig else _h.escape(sig).replace('\n', '<br>')
        block += f'<br><br>{sig_html}'
    return block + '</div>'

def audit(acc, to, cc, subj):
    ts = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    with open(SENT_LOG, 'a', encoding='utf-8') as f:
        f.write(f"{ts} | {acc} | to={to} | cc={cc or '-'} | subj={subj}\n")

def _bot_copy_mail(acc, to, subject):
    """Копия доктору голосом бота о каждом отправленном письме (подотчётность 07.08)."""
    try:
        import json as _j, re as _re, urllib.request as _u
        cfg = open("/home/openclaw/.openclaw/openclaw.json", encoding="utf-8").read()
        m = _re.search(r'"botToken"\s*:\s*"([^"]+)"', cfg)
        if not m:
            return
        body = _j.dumps({"chat_id": "123456789",
                         "text": f"\U0001F4E4 Отправлено письмо ({acc}) \u2192 {to}\nТема: {subject[:200]}"}).encode()
        req = _u.Request(f"https://api.telegram.org/bot{m.group(1)}/sendMessage",
                         data=body, headers={"Content-Type": "application/json"})
        _u.urlopen(req, timeout=10).read()
    except Exception:
        pass


def send(acc, a):
    if a.confirm != 'yes':
        sys.exit('ОТКАЗ: отправка требует --confirm yes (сначала покажи доктору письмо и получи явное «отправляй»).')
    if not a.to or not a.subject:
        sys.exit('Нужны --to и --subject.')
    text = a.text or ''
    if a.text_file and os.path.exists(a.text_file):
        text = open(a.text_file, encoding='utf-8').read()
    cc = a.cc

    if acc == 'mgz':
        import subprocess as _sp
        _cmd = ["sudo", "-n", "/root/yoda_mgz.sh", "send", "--to", a.to,
                "--subject", a.subject, "--text", text, "--confirm", "yes"]
        if cc:
            _cmd += ["--cc", cc]
        if a.no_signature:
            _cmd += ["--no-signature"]
        _r = _sp.run(_cmd, capture_output=True, text=True, timeout=300)
        print((_r.stdout or "").strip() or (_r.stderr or "").strip()[:300])
        if _r.returncode == 0:
            audit(acc, a.to, cc, a.subject); _bot_copy_mail(acc, a.to, a.subject)
        print(f"ОТПРАВЛЕНО (Мосгорздрав) → {a.to} | тема: {a.subject}")
        return

    host, port, user, pwd, sig = SMTP[acc]
    sig = '' if a.no_signature else sig
    msg = MIMEMultipart('alternative')
    msg['Subject'] = a.subject
    msg['From'] = user
    msg['To'] = a.to
    if cc:
        msg['Cc'] = cc
    plain = text + (('\n\n' + strip_html(sig)) if sig else '')
    msg.attach(MIMEText(plain, 'plain', 'utf-8'))
    msg.attach(MIMEText(as_html(text, sig), 'html', 'utf-8'))
    rcpt = [x.strip() for x in a.to.split(',')] + ([x.strip() for x in cc.split(',')] if cc else [])
    ctx = ssl.create_default_context()
    with smtplib.SMTP_SSL(host, port, context=ctx, timeout=30) as s:
        s.login(user, pwd)
        s.sendmail(user, rcpt, msg.as_string())
    audit(acc, a.to, cc, a.subject); _bot_copy_mail(acc, a.to, a.subject)
    print(f"ОТПРАВЛЕНО ({acc}) → {a.to} | тема: {a.subject}")

# ---------- main ----------
def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest='cmd', required=True)
    r = sub.add_parser('read')
    r.add_argument('--account', choices=ALL_ACCOUNTS + ['all'], default='all')
    r.add_argument('--unread', type=int, nargs='?', const=5)
    r.add_argument('--recent', type=int, nargs='?', const=10)
    r.add_argument('--hours', type=int, help='письма за последние N часов (быстро)')
    r.add_argument('--search')
    r.add_argument('--full')
    s = sub.add_parser('send')
    s.add_argument('--account', choices=ALL_ACCOUNTS, required=True)
    s.add_argument('--to', required=True)
    s.add_argument('--cc')
    s.add_argument('--subject', required=True)
    s.add_argument('--text', default='')
    s.add_argument('--text-file')
    s.add_argument('--no-signature', action='store_true')
    s.add_argument('--confirm', default='no')
    a = ap.parse_args()

    if a.cmd == 'read':
        accs = ALL_ACCOUNTS if a.account == 'all' else [a.account]
        for acc in accs:
            if not configured(acc):
                print(f'[{acc}] не настроен (нет ключей в ~/.openclaw/.env)\n'); continue
            try:
                if acc == 'mgz':
                    import subprocess as _sp
                    if a.search:
                        _cmd = ["sudo", "-n", "/root/yoda_mgz.sh", "search", a.search]
                    elif a.recent or a.full or getattr(a, 'hours', None):
                        _cmd = ["sudo", "-n", "/root/yoda_mgz.sh", "recent", str(a.recent or (15 if getattr(a, "hours", None) else 5))]
                    else:
                        _cmd = ["sudo", "-n", "/root/yoda_mgz.sh", "unread", str(a.unread or 5)]
                    _r = _sp.run(_cmd, capture_output=True, text=True, timeout=300)
                    print((_r.stdout or "").strip() or (_r.stderr or "").strip()[:300])
                else:
                    imap_read(acc, a)
            except Exception as e:
                print(f'[{acc}] ошибка: {type(e).__name__}: {e}\n')
    else:
        if not configured(a.account):
            sys.exit(f'[{a.account}] не настроен.')
        send(a.account, a)

main()
