#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Apple Календарь доктора через iCloud CalDAV.

  cal.py calendars                       — список календарей
  cal.py list [--days 7] [--cal "Имя"]   — события на ближайшие N дней
  cal.py today                           — события на сегодня
  cal.py search "текст" [--days 90]      — поиск событий
  cal.py add "Название" --start "2026-07-30 15:00" [--dur 60] [--loc "..."] [--note "..."] [--cal "Имя"] --confirm yes
  cal.py del <uid> --confirm yes         — удалить событие

Время — московское. Создание/удаление требует --confirm yes.
"""
import argparse, os, sys
from datetime import datetime, timedelta, timezone

MSK = timezone(timedelta(hours=3))
ENV = {}
_p = os.path.expanduser("~/.openclaw/.env")
if os.path.exists(_p):
    for line in open(_p, encoding="utf-8"):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            ENV[k.strip()] = v.strip().strip('"').strip("'")

APPLE_ID = ENV.get("APPLE_ID", "")
APPLE_PW = (ENV.get("APPLE_APP_PASSWORD", "") or "").replace(" ", "")
URL = ENV.get("CALDAV_URL", "https://caldav.icloud.com")

def client():
    if not APPLE_ID or not APPLE_PW:
        sys.exit("Не настроен доступ к календарю: нужны APPLE_ID и APPLE_APP_PASSWORD в ~/.openclaw/.env")
    import caldav
    c = caldav.DAVClient(url=URL, username=APPLE_ID, password=APPLE_PW)
    return c.principal()

def _cname(c):
    try:
        return str(c.get_display_name() or "(без имени)")
    except Exception:
        return str(getattr(c, "name", None) or "(без имени)")


def all_cals(principal, name=None):
    """Все календари (или один по имени). Служебные пропускаем."""
    if name:
        return [pick(principal, name)]
    out = []
    for c in principal.calendars():
        n = _cname(c).lower()
        if "напомин" in n or "reminder" in n:
            continue
        out.append(c)
    return out


def _key(ev):
    try:
        d = ev.icalendar_component.get("dtstart").dt
        return d.astimezone(MSK) if isinstance(d, datetime) else datetime.combine(d, datetime.min.time(), MSK)
    except Exception:
        return datetime.now(MSK)


def pick(principal, name=None):
    cals = principal.calendars()
    if not cals:
        sys.exit("календарей не найдено")
    if name:
        for c in cals:
            if name.lower() in _cname(c).lower():
                return c
        sys.exit("календарь «%s» не найден. Есть: %s" % (name, ", ".join(_cname(c) for c in cals)))
    for c in cals:
        if _cname(c).lower() in ("календарь", "calendar", "home", "личное"):
            return c
    return cals[0]

def fmt(ev):
    try:
        v = ev.icalendar_component
    except Exception:
        return "(не разобрано)"
    summary = str(v.get("summary", "(без названия)"))
    dt = v.get("dtstart")
    when = ""
    if dt is not None:
        d = dt.dt
        if isinstance(d, datetime):
            when = d.astimezone(MSK).strftime("%d.%m %H:%M")
        else:
            when = d.strftime("%d.%m (весь день)")
    loc = str(v.get("location", "") or "")
    uid = str(v.get("uid", "") or "")
    line = f"{when} — {summary}"
    if loc:
        line += f" | {loc}"
    return line + f"   [uid:{uid[:24]}]"

def cmd_calendars(a):
    p = client()
    print("Календари:")
    for c in p.calendars():
        print(" -", _cname(c))

def cmd_list(a):
    p = client()
    rows = []
    for cal in all_cals(p, a.cal):
        try:
            for e in cal.search(start=datetime.now(MSK), end=datetime.now(MSK) + timedelta(days=a.days),
                                event=True, expand=True):
                rows.append((_key(e), _cname(cal), e))
        except Exception:
            pass
    rows.sort(key=lambda r: r[0])
    print(f"События на {a.days} дн. ({len(rows)}):")
    if not rows:
        print("  — пусто")
    for _, cname, e in rows:
        print(f"  {fmt(e)}  ({cname})")


def cmd_today(a):
    a.days = 1
    cmd_list(a)

def cmd_search(a):
    p = client()
    q = a.query.lower()
    rows = []
    for cal in all_cals(p, a.cal):
        try:
            for e in cal.search(start=datetime.now(MSK) - timedelta(days=a.days),
                                end=datetime.now(MSK) + timedelta(days=a.days), event=True, expand=True):
                v = e.icalendar_component
                if q in str(v.get("summary", "")).lower() or q in str(v.get("location", "") or "").lower():
                    rows.append((_key(e), _cname(cal), e))
        except Exception:
            pass
    rows.sort(key=lambda r: r[0])
    print(f"Найдено {len(rows)} по «{a.query}»:")
    for _, cname, e in rows[:40]:
        print(f"  {fmt(e)}  ({cname})")


def cmd_add(a):
    if a.confirm != "yes":
        sys.exit("ОТКАЗ: создание события требует --confirm yes (сначала покажи доктору детали).")
    try:
        start = datetime.strptime(a.start, "%Y-%m-%d %H:%M").replace(tzinfo=MSK)
    except ValueError:
        sys.exit("формат времени: --start \"ГГГГ-ММ-ДД ЧЧ:ММ\"")
    end = start + timedelta(minutes=a.dur)
    p = client()
    cal = pick(p, a.cal)
    ev = cal.save_event(dtstart=start, dtend=end, summary=a.title,
                        location=a.loc or None, description=a.note or None)
    print(f"✓ СОЗДАНО в [{_cname(cal)}]: {start.strftime('%d.%m %H:%M')}–{end.strftime('%H:%M')} — {a.title}")

def cmd_del(a):
    if a.confirm != "yes":
        sys.exit("ОТКАЗ: удаление требует --confirm yes.")
    p = client()
    cal = pick(p, a.cal)
    for e in cal.search(start=datetime.now(MSK) - timedelta(days=365),
                        end=datetime.now(MSK) + timedelta(days=365), event=True):
        if str(e.icalendar_component.get("uid", "")).startswith(a.uid):
            title = str(e.icalendar_component.get("summary", ""))
            e.delete()
            print(f"✓ УДАЛЕНО: {title}")
            return
    sys.exit("событие с таким uid не найдено")

def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("calendars")
    l = sub.add_parser("list"); l.add_argument("--days", type=int, default=7); l.add_argument("--cal")
    t = sub.add_parser("today"); t.add_argument("--cal"); t.add_argument("--days", type=int, default=1)
    s = sub.add_parser("search"); s.add_argument("query"); s.add_argument("--days", type=int, default=90); s.add_argument("--cal")
    ad = sub.add_parser("add"); ad.add_argument("title"); ad.add_argument("--start", required=True)
    ad.add_argument("--dur", type=int, default=60); ad.add_argument("--loc"); ad.add_argument("--note")
    ad.add_argument("--cal"); ad.add_argument("--confirm", default="no")
    dl = sub.add_parser("del"); dl.add_argument("uid"); dl.add_argument("--cal"); dl.add_argument("--confirm", default="no")
    a = ap.parse_args()
    {"calendars": cmd_calendars, "list": cmd_list, "today": cmd_today,
     "search": cmd_search, "add": cmd_add, "del": cmd_del}[a.cmd](a)

main()
