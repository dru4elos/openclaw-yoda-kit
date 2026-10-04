#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""it.py — память занятий итальянским: ситуации, фразы, правила, интервальный повтор (FSRS).

Йода зовёт его из скилла italiano, Claude и люди — из терминала. Данные — data/*.json,
сводки для чтения (PROGRESSO.md, FRASI.md, REGOLE.md, situazioni/*.md) пересобираются сами.
Коммит и push — в конце урока, повтора и по `save`.

  status                                     что сейчас: уровень, к повтору, открытый урок/повтор
  situ list | situ show ID | situ next       ситуации: каталог, одна целиком, что взять дальше
  situ found ID --it .. --ru .. [--chi io|loro] [--sub S] [--nota ..]   фраза, найденная на занятии
  lesson start ID [--sub S] | lesson end [--nota ..] | lesson status
  card add --it .. --ru .. [--alt "a|b"] [--kind dire|capire] [--situ ID] [--sub S] [--nota ..] [--rule R]
  card list [--due] [--situ ID] | card show ID | card edit ID [--it ..] [--ru ..] [--alt ..] [--nota ..] | card drop ID
  rule add --titolo .. --spieg .. [--es "a|b"] [--detto ..] [--corretto ..] [--situ ID]
  rule hit ID --detto .. --corretto ..       снова ошибка на это правило
  rule drill ID ok|fail                      итог проверочного задания (закреплено = 2 верно подряд)
  rule list | rule show ID
  review start [--n N] [--source manual|cron] | review next | review status
  review answer CARD --grade again|hard|good|easy [--said ..] | review close [--skip]
  profile | profile set KEY VALUE            livello A1|A2|B1, voce on|off, ora HH:MM, quante N, tz Europe/Moscow
  pause DAYS | resume                        вечерний повтор на паузу и обратно
  kick                                       для таймера: в окно вечернего повтора открыть его и позвать Йоду
  render | save [--msg ..]
"""
import argparse
import contextlib
import fcntl
import json
import os
import re
import subprocess
import sys
import unicodedata
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(os.environ.get("ITALIANO_DIR") or Path(__file__).resolve().parents[1])
DATA = ROOT / "data"
CATALOG = ROOT / "situazioni" / "catalogo.json"
F = {k: DATA / f"{k}.json" for k in ("profilo", "cards", "rules", "trovate", "progress", "review", "lesson")}
SESSIONS = DATA / "sessions.jsonl"

PROFILE_DEFAULT = {
    "livello": None,              # A1 / A2 / B1 — уточняется на первом уроке
    "registro": "Lei",            # с незнакомыми — на «вы»
    "voce": False,                # озвучивать ключевые фразы голосом (скилл say --lang it)
    "sera": {"ora": "20:00", "quante": 5, "tz": "Europe/Moscow", "pausa_fino": None},
    "ultimo_kick": None,
    "consegna": {"session_key": "agent:main:telegram:default:direct:123456789", "chat": "123456789"},
}
GRADES = ("again", "hard", "good", "easy")
GRADE_RU = {"again": "не вспомнил", "hard": "с трудом", "good": "верно", "easy": "легко"}
KIND_RU = {"dire": "сказать", "capire": "понять", "regola": "правило"}
LEARNED_DAYS = 21                 # устойчивость ≥ 3 недель — фраза считается выученной

try:
    from fsrs import Card as FCard, Rating, Scheduler
    # Без минутных шагов: занятия раз в день, первая оценка сразу ставит срок в днях
    # (верно → 2 дня, легко → 8, трудно/ошибка → завтра). Разброс ±, чтобы карточки не слипались в один вечер.
    SCHED = Scheduler(learning_steps=(), relearning_steps=(), desired_retention=0.9, enable_fuzzing=True)
except Exception:                 # без библиотеки — простые коробки Лейтнера
    SCHED = None
LEITNER = [1, 2, 4, 8, 16, 32, 64]


# ───────────────────────── хранилище ─────────────────────────

def load(name, default):
    p = F[name]
    if not p.exists():
        return json.loads(json.dumps(default))
    return json.loads(p.read_text(encoding="utf-8"))


def store(name, obj):
    DATA.mkdir(parents=True, exist_ok=True)
    tmp = F[name].with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    os.replace(tmp, F[name])


def drop(name):
    with contextlib.suppress(FileNotFoundError):
        F[name].unlink()


@contextlib.contextmanager
def locked():
    DATA.mkdir(parents=True, exist_ok=True)
    with open(DATA / ".lock", "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def profile():
    p = load("profilo", PROFILE_DEFAULT)
    for k, v in PROFILE_DEFAULT.items():
        if isinstance(v, dict):
            p[k] = {**v, **(p.get(k) or {})}
        else:
            p.setdefault(k, v)
    return p


def catalog():
    return json.loads(CATALOG.read_text(encoding="utf-8"))["situazioni"]


def situ_by_id(sid):
    for s in catalog():
        if s["id"] == sid:
            return s
    sys.exit(f"нет ситуации «{sid}». Список: it.py situ list")


def log_session(entry):
    DATA.mkdir(parents=True, exist_ok=True)
    with open(SESSIONS, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def sessions():
    if not SESSIONS.exists():
        return []
    return [json.loads(x) for x in SESSIONS.read_text(encoding="utf-8").splitlines() if x.strip()]


# ───────────────────────── время ─────────────────────────

def tz():
    return ZoneInfo(profile()["sera"]["tz"] or "Europe/Moscow")


def now_utc():
    return datetime.now(timezone.utc)


def now_local():
    return now_utc().astimezone(tz())


def parse_dt(s):
    return datetime.fromisoformat(s) if s else None


def review_time(day):
    hh, mm = (int(x) for x in profile()["sera"]["ora"].split(":"))
    return datetime(day.year, day.month, day.day, hh, mm, tzinfo=tz())


def first_due():
    """Новая фраза приходит на ближайший вечерний повтор, до которого есть хотя бы 4 часа."""
    loc = now_local()
    t = review_time(loc.date())
    if loc + timedelta(hours=4) > t:
        t = review_time(loc.date() + timedelta(days=1))
    return t.astimezone(timezone.utc).isoformat()


def end_of_today_utc():
    loc = now_local()
    end = datetime(loc.year, loc.month, loc.day, 23, 59, 59, tzinfo=tz())
    return end.astimezone(timezone.utc)


def plural(n, one, few, many):
    n10, n100 = n % 10, n % 100
    w = one if n10 == 1 and n100 != 11 else few if 2 <= n10 <= 4 and not 12 <= n100 <= 14 else many
    return f"{n} {w}"


def human_due(iso):
    d = parse_dt(iso).astimezone(tz()).date()
    delta = (d - now_local().date()).days
    if delta < 0:
        return f"{d:%d.%m} (пора повторить)"
    when = {0: "сегодня", 1: "завтра", 2: "послезавтра"}.get(delta, f"через {delta} дн")
    return f"{d:%d.%m} ({when})"


# ───────────────────────── карточки ─────────────────────────

def norm(s):
    s = unicodedata.normalize("NFC", (s or "").lower())
    s = re.sub(r"[.,!?;:«»\"“”()…—–-]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def next_id(items, prefix, width):
    nums = [int(x["id"][len(prefix):]) for x in items if x["id"].startswith(prefix) and x["id"][len(prefix):].isdigit()]
    return f"{prefix}{(max(nums) + 1 if nums else 1):0{width}d}"


def card_stage(c):
    if c.get("archived"):
        return "в архиве"
    if not c.get("reps"):
        return "новая"
    stab = (c.get("fsrs") or {}).get("stability") or 0
    box = c.get("box", 0)
    return "выучена" if stab >= LEARNED_DAYS or (SCHED is None and box >= 5) else "учу"


def schedule(c, grade, when):
    """Обновляет срок карточки по оценке. Возвращает новый срок (iso, UTC)."""
    if SCHED is not None:
        fc = FCard.from_dict(c["fsrs"]) if c.get("fsrs") else FCard()
        fc, _ = SCHED.review_card(fc, getattr(Rating, grade.capitalize()), when)
        c["fsrs"] = fc.to_dict()
        c["due"] = fc.due.isoformat()
    else:
        box = c.get("box", 0)
        box = 0 if grade == "again" else min(box + (2 if grade == "easy" else 1), len(LEITNER) - 1)
        c["box"] = box
        c["due"] = (when + timedelta(days=LEITNER[box])).isoformat()
    return c["due"]


def lesson_link(kind, value):
    les = load("lesson", None)
    if les:
        les.setdefault(kind, [])
        if value not in les[kind]:
            les[kind].append(value)
        les["updated"] = now_utc().isoformat()
        store("lesson", les)
    return les


def new_card(cards, *, it, ru, kind="dire", alt=None, situ=None, sub=None, nota="", rule=None):
    key = (kind, norm(it))
    for c in cards:
        if (c["kind"], norm(c["it"])) == key and not c.get("archived"):
            return c, False
    c = {"id": next_id(cards, "c", 4), "kind": kind, "it": it.strip(), "ru": ru.strip(),
         "alt": [a.strip() for a in (alt or []) if a.strip()], "situ": situ, "sub": sub,
         "nota": nota or "", "rule": rule, "created": now_utc().isoformat(),
         "due": first_due(), "reps": 0, "lapses": 0, "fsrs": None, "history": []}
    cards.append(c)
    return c, True


# ───────────────────────── git ─────────────────────────

def commit(msg):
    def git(*a, timeout=60):
        return subprocess.run(["git", "-C", str(ROOT), *a], capture_output=True, text=True, timeout=timeout)
    git("add", "-A")
    if git("diff", "--cached", "--quiet").returncode == 0:
        return "изменений для коммита нет"
    r = git("commit", "-q", "-m", msg)
    if r.returncode != 0:
        return "⚠️ коммит не прошёл: " + (r.stderr or r.stdout).strip()[-300:]
    try:
        p = git("push", "-q", timeout=40)
        ok = p.returncode == 0
    except subprocess.TimeoutExpired:
        ok = False
    return f"записано в репозиторий: {msg}" + ("" if ok else " (push не прошёл — уйдёт со следующим)")


# ───────────────────────── сводки ─────────────────────────

def render():
    cards = load("cards", [])
    rules = load("rules", [])
    trov = load("trovate", {})
    prog = load("progress", {})
    prof = profile()
    sess = sessions()
    cat = catalog()
    active = [c for c in cards if not c.get("archived")]
    stages = {}
    for c in active:
        if c["kind"] != "regola":
            stages[card_stage(c)] = stages.get(card_stage(c), 0) + 1

    # PROGRESSO.md
    L = ["# Прогресс", "",
         f"Уровень: **{prof['livello'] or 'не определён'}** · обращение по умолчанию: {prof['registro']} · "
         f"вечерний повтор: {prof['sera']['ora']} ({prof['sera']['tz']}), до {prof['sera']['quante']} карточек"
         + (f" · ⏸ пауза до {prof['sera']['pausa_fino']}" if prof["sera"].get("pausa_fino") else ""), "",
         "| Фразы | новые | учу | выучены | Правила | в работе | закреплены |", "|---|---|---|---|---|---|---|",
         f"| {sum(1 for c in active if c['kind'] != 'regola')} | {stages.get('новая', 0)} | {stages.get('учу', 0)} | "
         f"{stages.get('выучена', 0)} | {len(rules)} | {sum(1 for r in rules if r['stato'] == 'в работе')} | "
         f"{sum(1 for r in rules if r['stato'] != 'в работе')} |", ""]
    upcoming = {}
    for c in active:
        d = parse_dt(c["due"]).astimezone(tz()).date()
        d = max(d, now_local().date())
        upcoming[d] = upcoming.get(d, 0) + 1
    if upcoming:
        L += ["## Ближайшие повторы", ""] + [f"- {d:%d.%m}: {n}" for d, n in sorted(upcoming.items())[:7]] + [""]
    L += ["## Ситуации", "", "| Ситуация | Уровень | Подтипы пройдено | Фраз выучено/в работе |", "|---|---|---|---|"]
    for s in cat:
        done = len(prog.get(s["id"], {}))
        n = sum(1 for c in active if c.get("situ") == s["id"] and c["kind"] != "regola")
        L.append(f"| [{s['ru']}](situazioni/{s['id']}.md) | {s['livello']} | {done}/{len(s['sottotipi'])} | {n} |")
    L += ["", "## Последние занятия", ""]
    for e in sess[-15:][::-1]:
        when = parse_dt(e["at"]).astimezone(tz()).strftime("%d.%m %H:%M")
        if e["type"] == "lesson":
            L.append(f"- {when} · урок «{e.get('situ_ru', e['situ'])}»" + (f" / {e['sub']}" if e.get("sub") else "")
                     + f": +{plural(len(e.get('cards', [])), 'фраза', 'фразы', 'фраз')}, правил: {len(e.get('rules', []))}"
                     + (f" — {e['nota']}" if e.get("nota") else ""))
        else:
            L.append(f"- {when} · повтор: верно с первого раза {e.get('ok', 0)} из {e.get('total', 0)}"
                     + (" (пропущен)" if e.get("skipped") else ""))
    (ROOT / "PROGRESSO.md").write_text("\n".join(L) + "\n", encoding="utf-8")

    # FRASI.md
    L = ["# Фразы", "", "Выученное и то, что в повторе. «сказать» — с русского на итальянский, "
         "«понять» — услышать итальянское и понять.", ""]
    for s in cat + [{"id": None, "ru": "Без ситуации"}]:
        cs = [c for c in active if c.get("situ") == s["id"] and c["kind"] != "regola"]
        if not cs:
            continue
        L += [f"## {s['ru']}", "", "| Итальянский | Русский | Вид | Статус | Повтор |", "|---|---|---|---|---|"]
        for c in cs:
            L.append(f"| {c['it']} | {c['ru']} | {KIND_RU[c['kind']]} | {card_stage(c)} | {human_due(c['due'])} |")
        L.append("")
    (ROOT / "FRASI.md").write_text("\n".join(L) + "\n", encoding="utf-8")

    # REGOLE.md
    L = ["# Правила", "", "Каждое правило появилось из настоящей ошибки на занятии. «Закреплено» — "
         "два проверочных задания подряд без ошибки; потом правило ещё приходит в вечерний повтор.", ""]
    for r in rules:
        L += [f"## {r['id']} · {r['titolo']}", "", f"**Статус:** {r['stato']} · заданий верно {r['drill']['ok']}, "
              f"неверно {r['drill']['fail']}", "", r["spieg"], ""]
        if r.get("esempi"):
            L += ["Примеры:"] + [f"- {e}" for e in r["esempi"]] + [""]
        if r.get("errori"):
            L += ["Ошибки:"] + [f"- {parse_dt(e['at']).astimezone(tz()):%d.%m}: ~~{e['detto']}~~ → **{e['corretto']}**"
                                for e in r["errori"]] + [""]
    (ROOT / "REGOLE.md").write_text("\n".join(L) + "\n", encoding="utf-8")

    # situazioni/*.md
    idx = ["# Ситуации", "", "| # | Ситуация | По-итальянски | Уровень |", "|---|---|---|---|"]
    for i, s in enumerate(cat, 1):
        idx.append(f"| {i} | [{s['ru']}]({s['id']}.md) | {s['it']} | {s['livello']} |")
        p = prog.get(s["id"], {})
        M = [f"# {s['ru']} — *{s['it']}* ({s['livello']})", "", s["contesto"], "",
             f"**Роли:** ты — {s['ruoli']['tu']}; Йода — {s['ruoli']['yoda']}.  ", f"**Обращение:** {s['registro']}", "",
             "## Подтипы", ""]
        for st in s["sottotipi"]:
            mark = f"✅ {p[st['id']]['volte']}× (последний {p[st['id']]['ultima']})" if st["id"] in p else "—"
            M.append(f"- **{st['ru']}** (`{st['id']}`): {mark}")
        M += ["", "## Грамматика", ""] + [f"- {g}" for g in s["grammatica"]]
        M += ["", "## Полезно знать", ""] + [f"- {c}" for c in s["cultura"]]
        for who, title in (("io", "Говорю я"), ("loro", "Слышу я")):
            fr = [x for x in s["frasi"] if x["chi"] == who] + [x for x in trov.get(s["id"], []) if x.get("chi", "io") == who]
            if fr:
                M += ["", f"## {title}", "", "| Итальянский | Русский |", "|---|---|"]
                M += [f"| {x['it']} | {x['ru']}{' · найдено на занятии' if 'at' in x else ''} |" for x in fr]
        (ROOT / "situazioni" / f"{s['id']}.md").write_text("\n".join(M) + "\n", encoding="utf-8")
    (ROOT / "situazioni" / "README.md").write_text("\n".join(idx) + "\n", encoding="utf-8")


# ───────────────────────── команды ─────────────────────────

def cmd_status(a):
    prof = profile()
    cards = [c for c in load("cards", []) if not c.get("archived")]
    rules = load("rules", [])
    due = [c for c in cards if parse_dt(c["due"]) <= end_of_today_utc()]
    rev = load("review", None)
    les = load("lesson", None)
    print(f"Уровень: {prof['livello'] or 'не определён — спроси на первом уроке и сохрани: profile set livello A1'}")
    print(f"Фраз: {sum(1 for c in cards if c['kind'] != 'regola')}, правил: {len(rules)} "
          f"(в работе {sum(1 for r in rules if r['stato'] == 'в работе')}). К повтору сегодня: {len(due)}.")
    s = prof["sera"]
    print(f"Вечерний повтор: {s['ora']} ({s['tz']}), до {s['quante']} карточек"
          + (f", пауза до {s['pausa_fino']}" if s.get("pausa_fino") else ""))
    if rev and not rev.get("closed"):
        print(f"🔁 Открыт повтор: отвечено {len(rev['answers'])} из {len(rev['queue'])}. Дальше — review next.")
    if les:
        print(f"📖 Открыт урок: {les['situ']}" + (f" / {les['sub']}" if les.get("sub") else "")
              + f", фраз {len(les.get('cards', []))}, правил {len(les.get('rules', []))}.")
    weak = [r for r in rules if r["stato"] == "в работе"]
    if weak:
        print("Слабые правила (вплетай в урок):", "; ".join(f"{r['id']} {r['titolo']}" for r in weak[:5]))
    last = sessions()[-1:] or None
    if last:
        print("Последнее занятие:", parse_dt(last[0]["at"]).astimezone(tz()).strftime("%d.%m %H:%M"), last[0]["type"])


def cmd_situ(a):
    prog = load("progress", {})
    if a.action == "list":
        for s in catalog():
            p = prog.get(s["id"], {})
            print(f"{s['id']:12} {s['livello']}  {s['ru']} — {s['it']}  [подтипы {len(p)}/{len(s['sottotipi'])}]")
    elif a.action == "next":
        lvl = profile()["livello"] or "A1"
        order = ["A1", "A2", "B1"]
        cand = [s for s in catalog() if order.index(s["livello"]) <= order.index(lvl)] or catalog()
        for s in cand:
            left = [st for st in s["sottotipi"] if st["id"] not in prog.get(s["id"], {})]
            if left:
                print(f"Дальше: {s['id']} / {left[0]['id']} — {s['ru']}: {left[0]['ru']}")
                return
        oldest = min(((s["id"], st["id"], prog[s["id"]][st["id"]]["ultima"]) for s in cand for st in s["sottotipi"]),
                     key=lambda x: x[2])
        print(f"Всё на уровне {lvl} пройдено. Дольше всего не было: {oldest[0]} / {oldest[1]} ({oldest[2]}). "
              f"Можно поднять уровень: profile set livello {order[min(order.index(lvl) + 1, 2)]}")
    elif a.action == "show":
        show_situ(situ_by_id(a.id), sub=a.sub)
    elif a.action == "found":
        if not (a.it and a.ru):
            sys.exit("нужно --it и --ru")
        situ_by_id(a.id)
        with locked():
            trov = load("trovate", {})
            lst = trov.setdefault(a.id, [])
            if any(norm(x["it"]) == norm(a.it) for x in lst) or any(norm(x["it"]) == norm(a.it) for x in situ_by_id(a.id)["frasi"]):
                print("уже есть в ситуации")
                return
            lst.append({"it": a.it, "ru": a.ru, "chi": a.chi or "io", "sub": a.sub, "nota": a.nota or "",
                        "at": now_utc().isoformat()})
            store("trovate", trov)
        print(f"записал в «{a.id}»: {a.it}")


def show_situ(s, sub=None):
    prog = load("progress", {}).get(s["id"], {})
    trov = load("trovate", {}).get(s["id"], [])
    cards = [c for c in load("cards", []) if c.get("situ") == s["id"] and not c.get("archived")]
    print(f"{s['ru']} — {s['it']} ({s['livello']})\n{s['contesto']}")
    print(f"Роли: доктор — {s['ruoli']['tu']}; ты — {s['ruoli']['yoda']}. Обращение: {s['registro']}")
    print("Подтипы: " + "; ".join(f"{st['id']} «{st['ru']}»" + (" ✅" if st["id"] in prog else "") for st in s["sottotipi"]))
    print("Грамматика: " + " · ".join(s["grammatica"]))
    print("Полезно знать: " + " · ".join(s["cultura"]))
    pool = [x for x in s["frasi"] + trov if not sub or x.get("sub") in (sub, None)]
    print("Фразы" + (f" подтипа {sub}" if sub else "") + ":")
    for x in pool:
        print(f"  [{'говорю' if x['chi'] == 'io' else 'слышу'}] {x['it']} — {x['ru']}")
    if cards:
        print("Уже в повторе по этой ситуации (не учить заново, можно освежить):")
        for c in cards:
            print(f"  {c['id']} {c['it']} — {card_stage(c)}")


def cmd_lesson(a):
    if a.action == "status":
        les = load("lesson", None)
        print(json.dumps(les, ensure_ascii=False, indent=1) if les else "урока нет")
        return
    if a.action == "start":
        s = situ_by_id(a.id)
        if a.sub and a.sub not in [st["id"] for st in s["sottotipi"]]:
            sys.exit(f"у «{s['id']}» нет подтипа {a.sub}: " + ", ".join(st["id"] for st in s["sottotipi"]))
        with locked():
            old = load("lesson", None)
            if old:
                end_lesson(old, nota="закрыт автоматически при старте нового урока", quiet=True)
            store("lesson", {"situ": s["id"], "sub": a.sub, "started": now_utc().isoformat(),
                             "updated": now_utc().isoformat(), "cards": [], "rules": [], "found": []})
        print(f"📖 Урок открыт: {s['ru']}" + (f" / {a.sub}" if a.sub else ""))
        show_situ(s, sub=a.sub)
        weak = [r for r in load("rules", []) if r["stato"] == "в работе"]
        if weak:
            print("Слабые правила доктора — вплети в сцену:", "; ".join(f"{r['id']} {r['titolo']}" for r in weak[:3]))
        return
    with locked():
        les = load("lesson", None)
        if not les:
            sys.exit("урок не открыт")
        out = end_lesson(les, nota=a.nota)
    print(out)


def end_lesson(les, nota=None, quiet=False):
    s = situ_by_id(les["situ"])
    prog = load("progress", {})
    today = now_local().strftime("%Y-%m-%d")
    subs = [les["sub"]] if les.get("sub") else []
    cards = {c["id"]: c for c in load("cards", [])}
    subs += sorted({cards[i]["sub"] for i in les.get("cards", []) if i in cards and cards[i].get("sub")} - set(subs))
    for sub in subs:
        rec = prog.setdefault(s["id"], {}).setdefault(sub, {"volte": 0, "ultima": today})
        rec["volte"] += 1
        rec["ultima"] = today
    store("progress", prog)
    log_session({"type": "lesson", "at": now_utc().isoformat(), "started": les["started"], "situ": s["id"],
                 "situ_ru": s["ru"], "sub": les.get("sub"), "cards": les.get("cards", []),
                 "rules": les.get("rules", []), "nota": nota or ""})
    drop("lesson")
    render()
    msg = f"урок: {s['ru']}" + (f" / {les['sub']}" if les.get("sub") else "") + \
          f" — +{plural(len(les.get('cards', [])), 'фраза', 'фразы', 'фраз')}, правил: {len(les.get('rules', []))}"
    res = commit(msg)
    if quiet:
        return res
    nxt = [cards[i]["due"] for i in les.get("cards", []) if i in cards]
    tail = f"\nПервый повтор новых фраз: {human_due(min(nxt))} в {profile()['sera']['ora']}." if nxt else ""
    return f"✅ Урок закрыт: {msg}.{tail}\n{res}"


def cmd_card(a):
    if a.action == "list":
        cards = [c for c in load("cards", []) if not c.get("archived")]
        if a.due:
            cards = [c for c in cards if parse_dt(c["due"]) <= end_of_today_utc()]
        if a.situ:
            cards = [c for c in cards if c.get("situ") == a.situ]
        for c in cards:
            print(f"{c['id']} [{KIND_RU[c['kind']]}] {c['it']} — {c['ru']} · {card_stage(c)} · повтор {human_due(c['due'])}")
        print(f"всего: {len(cards)}")
        return
    if a.action == "add":
        if not (a.it and a.ru):
            sys.exit("нужно --it и --ru")
        with locked():
            cards = load("cards", [])
            les = load("lesson", None) or {}
            c, created = new_card(cards, it=a.it, ru=a.ru, kind=a.kind or "dire",
                                  alt=(a.alt or "").split("|") if a.alt else [],
                                  situ=a.situ or les.get("situ"), sub=a.sub or les.get("sub"),
                                  nota=a.nota, rule=a.rule)
            store("cards", cards)
            lesson_link("cards", c["id"])
        print((f"➕ {c['id']} добавлена" if created else f"уже есть {c['id']} ({card_stage(c)})")
              + f": {c['it']} — {c['ru']} · повтор {human_due(c['due'])}")
        return
    with locked():
        cards = load("cards", [])
        c = next((x for x in cards if x["id"] == a.id), None)
        if not c:
            sys.exit(f"нет карточки {a.id}")
        if a.action == "show":
            print(json.dumps(c, ensure_ascii=False, indent=1))
            return
        if a.action == "edit":
            for k in ("it", "ru", "nota"):
                if getattr(a, k):
                    c[k] = getattr(a, k)
            if a.alt is not None:
                c["alt"] = [x.strip() for x in a.alt.split("|") if x.strip()]
            print(f"исправил {c['id']}: {c['it']} — {c['ru']}")
        elif a.action == "drop":
            c["archived"] = True
            print(f"убрал из повтора {c['id']}: {c['it']}")
        store("cards", cards)
        render()


def cmd_rule(a):
    if a.action == "list":
        for r in load("rules", []):
            print(f"{r['id']} · {r['titolo']} · {r['stato']} · ошибок {len(r['errori'])}, заданий +{r['drill']['ok']}/−{r['drill']['fail']}")
        return
    with locked():
        rules = load("rules", [])
        cards = load("cards", [])
        les = load("lesson", None) or {}
        if a.action == "add":
            if not (a.titolo and a.spieg):
                sys.exit("нужно --titolo и --spieg")
            r = next((x for x in rules if norm(x["titolo"]) == norm(a.titolo)
                      or (len(norm(a.titolo)) > 8 and (norm(a.titolo) in norm(x["titolo"]) or norm(x["titolo"]) in norm(a.titolo)))), None)
            if r:
                a.id = r["id"]
                print(f"такое правило уже есть: {r['id']} «{r['titolo']}» — записываю как повторную ошибку")
                hit(r, cards, a.detto, a.corretto)
            else:
                r = {"id": next_id(rules, "r", 3), "titolo": a.titolo.strip(), "spieg": a.spieg.strip(),
                     "esempi": [e.strip() for e in (a.es or "").split("|") if e.strip()],
                     "situ": a.situ or les.get("situ"), "errori": [], "drill": {"ok": 0, "fail": 0, "serie": 0},
                     "stato": "в работе", "created": now_utc().isoformat()}
                if a.detto:
                    r["errori"].append({"at": now_utc().isoformat(), "detto": a.detto, "corretto": a.corretto or ""})
                c, _ = new_card(cards, it=r["titolo"], ru=r["spieg"], kind="regola", situ=r["situ"], rule=r["id"])
                r["card"] = c["id"]
                rules.append(r)
                print(f"📌 правило {r['id']} «{r['titolo']}» сохранено; в вечерний повтор — {human_due(c['due'])}.\n"
                      f"Теперь проверочные задания по одному: после каждого — rule drill {r['id']} ok|fail.")
            lesson_link("rules", r["id"])
        else:
            r = next((x for x in rules if x["id"] == a.id), None)
            if not r:
                sys.exit(f"нет правила {a.id}")
            if a.action == "show":
                print(json.dumps(r, ensure_ascii=False, indent=1))
                return
            if a.action == "hit":
                hit(r, cards, a.detto, a.corretto)
                lesson_link("rules", r["id"])
                print(f"записал ошибку к {r['id']}: серия сброшена, нужно 2 верных подряд.")
            elif a.action == "drill":
                d = r["drill"]
                if a.res == "ok":
                    d["ok"] += 1
                    d["serie"] += 1
                else:
                    d["fail"] += 1
                    d["serie"] = 0
                if d["serie"] >= 2:
                    r["stato"] = "закреплено"
                    print(f"✅ {r['id']}: два верно подряд — закреплено. Возвращайтесь в сцену.")
                else:
                    r["stato"] = "в работе"
                    print(f"{r['id']}: серия {d['serie']}/2 — "
                          + ("ещё одно задание." if a.res == "ok" else "объясни иначе, с новым примером, и дай ещё задание."))
        store("rules", rules)
        store("cards", cards)


def hit(r, cards, detto, corretto):
    r["errori"].append({"at": now_utc().isoformat(), "detto": detto or "", "corretto": corretto or ""})
    r["stato"] = "в работе"
    r["drill"]["serie"] = 0
    c = next((x for x in cards if x["id"] == r.get("card")), None)
    if c and c.get("reps"):                                 # правило уже повторялось — вернуть на завтра
        schedule(c, "again", now_utc())
        c["lapses"] = c.get("lapses", 0) + 1


def pick_due(n):
    cards = [c for c in load("cards", []) if not c.get("archived") and parse_dt(c["due"]) <= end_of_today_utc()]
    cards.sort(key=lambda c: (parse_dt(c["due"]), -c.get("lapses", 0)))
    out, kinds = [], {}
    for c in cards:                                         # не больше двух правил за вечер — это мягкий повтор
        if c["kind"] == "regola" and kinds.get("regola", 0) >= 2:
            continue
        kinds[c["kind"]] = kinds.get(c["kind"], 0) + 1
        out.append(c["id"])
        if len(out) >= n:
            break
    return out


def start_review(n, source):
    rev = load("review", None)
    if rev and not rev.get("closed"):
        if now_utc() - parse_dt(rev["started"]) < timedelta(hours=6):
            return rev, False
        close_review(rev, skip=True, quiet=True)
    q = pick_due(n)
    if not q:
        return None, False
    rev = {"started": now_utc().isoformat(), "source": source, "queue": q, "answers": [], "requeued": [], "closed": False}
    store("review", rev)
    return rev, True


def card_prompt(c, slot, total):
    r = None
    if c["kind"] == "regola":
        r = next((x for x in load("rules", []) if x["id"] == c.get("rule")), None)
    head = f"Карточка {slot + 1}/{total} · {c['id']} · {KIND_RU[c['kind']]}"
    if c.get("situ"):
        with contextlib.suppress(SystemExit):
            head += f" · ситуация «{situ_by_id(c['situ'])['ru']}»"
    if c["kind"] == "dire":
        q = f"Спроси доктора: как сказать по-итальянски «{c['ru'].rstrip('.')}»?"
        exp = c["it"]
    elif c["kind"] == "capire":
        q = f"Спроси доктора: что значит «{c['it']}»? (можно дать сценку: «тебе говорят…»)"
        exp = c["ru"]
    else:
        q = ("Придумай ОДНО новое короткое задание на это правило (перевод фразы с русского или вставить пропуск), "
             "не повторяя примеры, и задай его доктору.")
        exp = f"правило «{c['it']}»: {c['ru']}" + (f" Примеры: {' | '.join(r['esempi'])}" if r and r.get("esempi") else "")
    lines = [head, q, f"Ожидается (НЕ показывай до ответа): {exp}"]
    if c.get("alt"):
        lines.append("Тоже верно: " + " | ".join(c["alt"]))
    if c.get("nota"):
        lines.append("Заметка: " + c["nota"])
    if c.get("rule") and c["kind"] != "regola":
        lines.append(f"Связано с правилом {c['rule']}")
    lines.append(f"После ответа: review answer {c['id']} --grade again|hard|good|easy --said \"<что сказал доктор>\"")
    return "\n".join(lines)


def close_review(rev, skip=False, quiet=False):
    first = {}
    for ans in rev["answers"]:
        first.setdefault(ans["card"], ans["grade"])
    ok = sum(1 for g in first.values() if g != "again")
    rev["closed"] = True
    rev["closed_at"] = now_utc().isoformat()
    store("review", rev)
    log_session({"type": "review", "at": now_utc().isoformat(), "started": rev["started"], "source": rev["source"],
                 "total": len(set(rev["queue"])), "answered": len(first), "ok": ok,
                 "again": [c for c, g in first.items() if g == "again"], "skipped": skip and len(first) < len(set(rev["queue"]))})
    render()
    d = parse_dt(rev["started"]).astimezone(tz())
    res = commit(f"повтор {d:%d.%m}: верно {ok} из {len(first)}" + (" (не закончен)" if skip else ""))
    if quiet:
        return res
    cards = [c for c in load("cards", []) if not c.get("archived")]
    nxt = sorted(parse_dt(c["due"]) for c in cards if parse_dt(c["due"]) > end_of_today_utc())
    tail = f" Следующий повтор: {human_due(nxt[0].isoformat())}." if nxt else ""
    return f"🔁 Повтор закрыт: верно с первого раза {ok} из {len(first)}.{tail}\n{res}"


def cmd_review(a):
    if a.action == "status":
        rev = load("review", None)
        if not rev or rev.get("closed"):
            print("открытого повтора нет")
        else:
            print(f"открыт с {parse_dt(rev['started']).astimezone(tz()):%H:%M}: отвечено {len(rev['answers'])} из {len(rev['queue'])}")
        return
    with locked():
        if a.action == "start":
            rev, created = start_review(a.n or profile()["sera"]["quante"], a.source or "manual")
            if not rev:
                print("повторять сегодня нечего — всё выучено вовремя. Можно урок: it.py situ next")
                return
            print(("🔁 Повтор открыт" if created else "🔁 Повтор уже открыт") +
                  f": {plural(len(rev['queue']), 'карточка', 'карточки', 'карточек')}, отвечено {len(rev['answers'])}. "
                  "Дальше — review next.")
            return
        rev = load("review", None)
        if not rev or rev.get("closed"):
            sys.exit("открытого повтора нет — review start")
        if a.action == "next":
            slot = len(rev["answers"])
            if slot >= len(rev["queue"]):
                print("Очередь пройдена — закрой: review close")
                return
            cards = {c["id"]: c for c in load("cards", [])}
            print(card_prompt(cards[rev["queue"][slot]], slot, len(rev["queue"])))
        elif a.action == "answer":
            if a.grade not in GRADES:
                sys.exit("оценка: again | hard | good | easy")
            slot = len(rev["answers"])
            rest = rev["queue"][slot:]
            if a.card not in rest:
                sys.exit(f"{a.card} нет среди оставшихся: {', '.join(rest) or '—'}")
            i = slot + rest.index(a.card)                   # ответ не по порядку — переставить в текущую позицию
            rev["queue"][slot], rev["queue"][i] = rev["queue"][i], rev["queue"][slot]
            cards = load("cards", [])
            c = next(x for x in cards if x["id"] == a.card)
            second = any(x["card"] == a.card for x in rev["answers"])
            rev["answers"].append({"card": a.card, "grade": a.grade, "said": a.said or "", "at": now_utc().isoformat()})
            c["history"].append({"at": now_utc().isoformat(), "grade": a.grade, "said": a.said or "",
                                 "where": "повтор (вторая попытка)" if second else "повтор"})
            if second:
                msg = "вторая попытка записана, срок не меняю (уже стоит на завтра)."
            else:
                due = schedule(c, a.grade, now_utc())
                c["reps"] = c.get("reps", 0) + 1
                msg = f"{GRADE_RU[a.grade]} → следующий раз {human_due(due)}."
                if a.grade == "again":
                    c["lapses"] = c.get("lapses", 0) + 1
                    if a.card not in rev["requeued"]:
                        rev["queue"].append(a.card)
                        rev["requeued"].append(a.card)
                        msg += (" Карточка вернётся в конце этого повтора. Сейчас — разбор: правило (rule add/hit) "
                                "и проверочные задания до двух верных подряд.")
            store("cards", cards)
            store("review", rev)
            left = len(rev["queue"]) - len(rev["answers"])
            print(msg + (f" Осталось: {left}." if left else " Это была последняя — review close."))
        elif a.action == "close":
            print(close_review(rev, skip=a.skip))


def cmd_profile(a):
    with locked():
        p = profile()
        if a.action == "set":
            k, v = a.key, a.value
            if k == "livello":
                if v not in ("A1", "A2", "B1", "B2"):
                    sys.exit("уровень: A1 | A2 | B1 | B2")
                p["livello"] = v
            elif k == "voce":
                p["voce"] = v.lower() in ("on", "1", "да", "true", "si", "sì")
            elif k == "registro":
                p["registro"] = v
            elif k == "ora":
                if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", v):
                    sys.exit("время: ЧЧ:ММ, например 20:00")
                p["sera"]["ora"] = v
            elif k == "quante":
                p["sera"]["quante"] = max(1, min(15, int(v)))
            elif k == "tz":
                ZoneInfo(v)
                p["sera"]["tz"] = v
            else:
                sys.exit("ключи: livello, voce, registro, ora, quante, tz")
            store("profilo", p)
            render()
            print(commit(f"профиль: {k} = {v}"))
        print(json.dumps(p, ensure_ascii=False, indent=1))


def cmd_pause(a):
    with locked():
        p = profile()
        if a.cmd == "pause":
            until = now_local().date() + timedelta(days=max(1, a.days) - 1)
            p["sera"]["pausa_fino"] = until.isoformat()
            msg = f"вечерний повтор на паузе до {until:%d.%m} включительно"
        else:
            p["sera"]["pausa_fino"] = None
            msg = "вечерний повтор снова включён"
        store("profilo", p)
        render()
        print(msg + ". " + commit(msg))


def cmd_kick(a):
    """Таймер зовёт каждые 15 минут. Делает что-то только раз в день, в окне [ора; ора+3 ч)."""
    with locked():
        p = profile()
        loc = now_local()
        t = review_time(loc.date())
        today = loc.date().isoformat()
        if not (t <= loc < t + timedelta(hours=3)) and not a.force:
            return
        if p.get("ultimo_kick") == today and not a.force:
            return
        pf = p["sera"].get("pausa_fino")
        if pf and today <= pf and not a.force:
            return
        p["ultimo_kick"] = today                            # отметка до вызова Йоды: второй тик не позовёт повторно
        store("profilo", p)
        les = load("lesson", None)
        if les and now_utc() - parse_dt(les["updated"]) < timedelta(hours=1):
            print("идёт урок — вечерний повтор сегодня не зову")
            return
        rev, created = start_review(p["sera"]["quante"], "cron")
        if not rev:
            print("к повтору ничего — молчу")
            return
        n = len(rev["queue"])
    dest = p["consegna"]
    text = (f"[Планировщик · вечерний повтор итальянского, {p['sera']['ora']}] Это не сообщение доктора, а запуск "
            f"по расписанию. Открыт повтор: {plural(n, 'карточка', 'карточки', 'карточек')}. Веди его по скиллу "
            f"italiano, раздел «Вечерний повтор»: одна короткая строка приветствия и сразу первый вопрос "
            f"(it.py review next). Ответ не показывай, пока доктор не ответит.")
    if a.dry:
        print("dry-run, сообщение Йоде:\n" + text)
        return
    cmd = ("source ~/.nvm/nvm.sh >/dev/null 2>&1; openclaw agent --agent main "
           f"--session-key {dest['session_key']} --deliver --channel telegram --reply-to {dest['chat']} "
           "--timeout 600 --message-file " + str(DATA / ".kick.txt"))
    (DATA / ".kick.txt").write_text(text, encoding="utf-8")
    r = subprocess.run(["bash", "-lc", cmd], capture_output=True, text=True, timeout=900)
    if r.returncode == 0:
        print(f"позвал Йоду: повтор, {plural(n, 'карточка', 'карточки', 'карточек')}")
        return
    # запасной путь — короткое сообщение ботом; Йода начнёт, когда доктор ответит
    tome = os.path.expanduser("~/.openclaw/workspace/skills/tome/tome.py")
    py = os.path.expanduser("~/mailvenv/bin/python")
    subprocess.run([py, tome, "msg", f"🇮🇹 Вечерний повтор итальянского: ждут {plural(n, 'фраза', 'фразы', 'фраз')}. Напиши «повторим итальянский», "
                    "когда будешь готов, или «не сегодня»."], capture_output=True, timeout=120)
    print("Йода не ответил (" + (r.stderr or r.stdout).strip()[-200:] + ") — отправил напоминание ботом")


def main():
    ap = argparse.ArgumentParser(prog="it.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    sp.add_parser("status")
    s = sp.add_parser("situ")
    s.add_argument("action", choices=["list", "show", "next", "found"])
    s.add_argument("id", nargs="?")
    for k in ("--it", "--ru", "--chi", "--sub", "--nota"):
        s.add_argument(k)
    s = sp.add_parser("lesson")
    s.add_argument("action", choices=["start", "end", "status"])
    s.add_argument("id", nargs="?")
    s.add_argument("--sub")
    s.add_argument("--nota")
    s = sp.add_parser("card")
    s.add_argument("action", choices=["add", "list", "show", "edit", "drop"])
    s.add_argument("id", nargs="?")
    for k in ("--it", "--ru", "--alt", "--situ", "--sub", "--nota", "--rule"):
        s.add_argument(k)
    s.add_argument("--kind", choices=["dire", "capire"])
    s.add_argument("--due", action="store_true")
    s = sp.add_parser("rule")
    s.add_argument("action", choices=["add", "hit", "drill", "list", "show"])
    s.add_argument("id", nargs="?")
    s.add_argument("res", nargs="?", choices=["ok", "fail"])
    for k in ("--titolo", "--spieg", "--es", "--detto", "--corretto", "--situ"):
        s.add_argument(k)
    s = sp.add_parser("review")
    s.add_argument("action", choices=["start", "next", "answer", "close", "status"])
    s.add_argument("card", nargs="?")
    s.add_argument("--grade")
    s.add_argument("--said")
    s.add_argument("--n", type=int)
    s.add_argument("--source")
    s.add_argument("--skip", action="store_true")
    s = sp.add_parser("profile")
    s.add_argument("action", nargs="?", default="show", choices=["show", "set"])
    s.add_argument("key", nargs="?")
    s.add_argument("value", nargs="?")
    s = sp.add_parser("pause")
    s.add_argument("days", type=int)
    sp.add_parser("resume")
    s = sp.add_parser("kick")
    s.add_argument("--force", action="store_true")
    s.add_argument("--dry", action="store_true")
    sp.add_parser("render")
    s = sp.add_parser("save")
    s.add_argument("--msg")
    a = ap.parse_args()

    if a.cmd in ("situ",) and a.action in ("show", "found") and not a.id:
        sys.exit("нужен ID ситуации: it.py situ list")
    if a.cmd == "lesson" and a.action == "start" and not a.id:
        sys.exit("нужен ID ситуации: it.py situ list")
    if a.cmd == "card" and a.action in ("show", "edit", "drop") and not a.id:
        sys.exit("нужен ID карточки")
    if a.cmd == "rule" and a.action in ("hit", "drill", "show") and not a.id:
        sys.exit("нужен ID правила")
    if a.cmd == "rule" and a.action == "drill" and not a.res:
        sys.exit("rule drill ID ok|fail")
    if a.cmd == "review" and a.action == "answer" and not (a.card and a.grade):
        sys.exit("review answer CARD --grade again|hard|good|easy")
    if a.cmd == "profile" and a.action == "set" and not (a.key and a.value):
        sys.exit("profile set KEY VALUE")

    {"status": cmd_status, "situ": cmd_situ, "lesson": cmd_lesson, "card": cmd_card, "rule": cmd_rule,
     "review": cmd_review, "profile": cmd_profile, "pause": cmd_pause, "resume": cmd_pause, "kick": cmd_kick,
     "render": lambda _: (render(), print("сводки пересобраны")),
     "save": lambda x: (render(), print(commit(x.msg or "сохранение")))}[a.cmd](a)


if __name__ == "__main__":
    main()
