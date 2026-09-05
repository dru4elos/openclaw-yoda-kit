#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Память по ВСЕМ прошлым диалогам — полнотекстовый поиск вместо раздувания контекста.

  recall.py index [--full]        — обновить индекс (инкрементально)
  recall.py search "<запрос>" [--n 8] [--days N] [--chars 700]
  recall.py stats

Откуда берёт переписку:
  1. SQLite агентов (~/.openclaw/agents/*/agent/openclaw-agent.sqlite, таблица
     transcript_events) — так OpenClaw 2.0 хранит живые сессии, JSONL больше не растут;
  2. JSONL: архив импорта 2.0 (session-sqlite-import-archive/*.jsonl.imported-*),
     архивы до сброса контекста *.jsonl.reset.* и оставшиеся файлы сессий —
     история до 2.0 и всё, что в SQLite не переехало (сессии кронов).
Сессия, которая есть в SQLite, из JSONL не читается: там та же история, но
SQLite полнее и продолжает расти.

Зачем: держать всю переписку в контексте нельзя — сессия пухнет и ответы
тормозят. Старое уезжает из контекста, но остаётся на диске и ищется здесь.
"""
import argparse
import glob
import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

DB = os.path.expanduser("~/.openclaw/workspace/memory/recall.db")
ROOTS = [os.path.expanduser("~/.openclaw/agents")]
AGENT_DBS = os.path.expanduser("~/.openclaw/agents/*/agent/openclaw-agent.sqlite")
SCHEMA = "2"                       # смена схемы → полная переиндексация сама
MIN_LEN = 40                       # реплики короче — служебный шум
SKIP = ("HEARTBEAT_OK", "NO_REPLY", "structuredContent:", "Command still running")


def _db():
    os.makedirs(os.path.dirname(DB), exist_ok=True)
    con = sqlite3.connect(DB)
    con.execute("""CREATE VIRTUAL TABLE IF NOT EXISTS msgs USING fts5(
        ts, session, role, text, tokenize='unicode61 remove_diacritics 2')""")
    con.execute("""CREATE TABLE IF NOT EXISTS files(
        path TEXT PRIMARY KEY, mtime REAL, msgs INTEGER)""")
    con.execute("CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT)")
    return con


def _rows(events):
    """Реплики человека и ассистента из потока событий: (ts, role, text)."""
    rows = []
    for d in events:
        m = d.get("message") or {}
        role = m.get("role")
        if role not in ("user", "assistant"):
            continue
        ts = (d.get("timestamp") or "")[:19]
        content = m.get("content")
        parts = []
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            for c in content:
                if isinstance(c, dict) and c.get("type") == "text":
                    parts.append(c.get("text") or "")
        text = re.sub(r"\s+", " ", " ".join(parts)).strip()
        if len(text) < MIN_LEN or any(s in text[:60] for s in SKIP):
            continue
        rows.append((ts, role, text[:4000]))
    return rows


def _jsonl_events(path):
    try:
        fh = open(path, encoding="utf-8", errors="replace")
    except Exception:
        return
    with fh:
        for line in fh:
            try:
                yield json.loads(line)
            except Exception:
                continue


def _files():
    out = []
    for root in ROOTS:
        for dirpath, _, names in os.walk(root):
            for n in names:
                # id.jsonl | id.jsonl.reset.<когда> | ключ.id.jsonl.imported-<мс> (архив импорта 2.0)
                if ".jsonl" in n and "trajectory" not in n and ".deleted." not in n:
                    out.append(os.path.join(dirpath, n))
    return out


_UUID = re.compile(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$")


def _file_key(path):
    """Ключ сессии по имени файла — тот же id, что в SQLite, чтобы не дублировать.
    id.jsonl / ключ.id.jsonl.imported-<мс>  -> id
    id.jsonl.reset.<когда>                  -> id.reset.<когда>  (архив до сброса — своя сессия)"""
    base = os.path.basename(path)
    stem, suffix = base.split(".jsonl", 1)
    m = _UUID.search(stem)
    sid = m.group(1) if m else stem
    return sid + suffix if ".reset." in suffix else sid


def _sqlite_sessions():
    """{session_id: (mtime, путь к базе, агент)} по всем агентам."""
    out = {}
    for dbp in glob.glob(AGENT_DBS):
        agent = dbp.split("/agents/")[1].split("/")[0]
        try:
            con = sqlite3.connect(f"file:{dbp}?mode=ro", uri=True)
            for sid, mx in con.execute(
                    "SELECT session_id, max(created_at) FROM transcript_events GROUP BY session_id"):
                if sid and mx:
                    out[sid] = (mx / 1000.0, dbp, agent)
            con.close()
        except sqlite3.Error as e:
            print(f"(база {dbp} не прочиталась: {e})", file=sys.stderr)
    return out


def _sqlite_events(dbp, sid):
    con = sqlite3.connect(f"file:{dbp}?mode=ro", uri=True)
    try:
        for (ej,) in con.execute(
                "SELECT event_json FROM transcript_events WHERE session_id = ? ORDER BY seq", (sid,)):
            try:
                yield json.loads(ej)
            except Exception:
                continue
    finally:
        con.close()


def _store(con, key, path, mtime, rows):
    con.execute("DELETE FROM msgs WHERE session = ?", (key,))
    con.executemany("INSERT INTO msgs(ts, session, role, text) VALUES (?,?,?,?)",
                    [(ts, key, role, text) for ts, role, text in rows])
    con.execute("INSERT OR REPLACE INTO files(path, mtime, msgs) VALUES (?,?,?)",
                (path, mtime, len(rows)))


def cmd_index(a):
    con = _db()
    ver = con.execute("SELECT v FROM meta WHERE k = 'schema'").fetchone()
    full = a.full or not ver or ver[0] != SCHEMA
    if full:
        con.execute("DELETE FROM msgs")
        con.execute("DELETE FROM files")
        con.execute("INSERT OR REPLACE INTO meta(k, v) VALUES ('schema', ?)", (SCHEMA,))
    known = dict(con.execute("SELECT path, mtime FROM files"))
    added = updated = 0

    live = _sqlite_sessions()
    for sid, (mt, dbp, agent) in live.items():
        path = f"sqlite:{agent}:{sid}"
        if known.get(path, 0) >= mt:
            continue
        _store(con, sid, path, mt, _rows(_sqlite_events(dbp, sid)))
        updated += path in known
        added += path not in known

    for path in _files():
        key = _file_key(path)
        if key in live:                       # живая сессия переехала в SQLite
            con.execute("DELETE FROM files WHERE path = ?", (path,))
            continue
        mt = os.path.getmtime(path)
        if known.get(path, 0) >= mt:
            continue
        _store(con, key, path, mt, _rows(_jsonl_events(path)))
        updated += path in known
        added += path not in known

    con.commit()
    total = con.execute("SELECT count(*) FROM msgs").fetchone()[0]
    print(f"Индекс {'перестроен' if full else 'обновлён'}: новых сессий {added}, "
          f"обновлено {updated}. Всего реплик в памяти: {total}")
    con.close()


def cmd_search(a):
    if not os.path.exists(DB):
        sys.exit("индекса ещё нет — сначала: recall.py index")
    con = _db()
    q = a.query.strip()
    words = [w for w in re.findall(r"[\w\-]{3,}", q.lower())][:8]
    if not words:
        sys.exit("слишком короткий запрос")

    def _query_ladder(ws):
        """От строгого к мягкому: все слова -> без последнего -> любое.
        Чистый OR выдаёт мусор: одно частое слово тянет брифинги и сводки."""
        out = [" AND ".join(f'"{w}"' for w in ws)]
        if len(ws) > 2:
            out.append(" AND ".join(f'"{w}"' for w in ws[:-1]))
        if len(ws) > 3:
            out.append(" AND ".join(f'"{w}"' for w in ws[:2]))
        out.append(" OR ".join(f'"{w}"' for w in ws))
        return out

    cutoff = None
    if a.days:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=a.days)).strftime("%Y-%m-%dT%H:%M:%S")

    rows, used = [], ""
    for fts in _query_ladder(words):
        where, params = "msgs MATCH ?", [fts]
        if cutoff:
            where += " AND ts >= ?"
            params.append(cutoff)
        try:
            rows = con.execute(
                f"SELECT ts, session, role, text, bm25(msgs) AS rank FROM msgs "
                f"WHERE {where} ORDER BY rank LIMIT ?", params + [a.n]).fetchall()
        except sqlite3.OperationalError as e:
            sys.exit(f"поиск не удался: {e}")
        if rows:
            used = fts
            break
    if used and " OR " in used:
        print("(строгого совпадения не нашлось — показываю по отдельным словам, "
              "среди них может быть не по теме)")
    if not rows:
        print(f"По запросу «{q}» в прошлых диалогах ничего не нашлось.")
        print("Это НЕ значит, что разговора не было — попробуй другие слова. "
              "И не выдумывай, чего не нашёл.")
        con.close()
        return
    print(f"НАЙДЕНО В ПРОШЛЫХ ДИАЛОГАХ: {len(rows)} фрагм. по запросу «{q}»")
    print("=" * 60)
    for ts, sess, role, text, _ in rows:
        when = ts.replace("T", " ")[:16] or "?"
        who = "ДОКТОР" if role == "user" else "ТЫ"
        print(f"\n[{when}] {who} (сессия {sess[:18]}):")
        print(f"  {text[:a.chars]}")
    print("\n" + "=" * 60)
    print("Это выдержки из РЕАЛЬНОЙ переписки. Ссылайся только на них, "
          "детали, которых тут нет, не додумывай.")
    con.close()


def cmd_stats(_a):
    if not os.path.exists(DB):
        sys.exit("индекса ещё нет — сначала: recall.py index")
    con = _db()
    total = con.execute("SELECT count(*) FROM msgs").fetchone()[0]
    n_db = con.execute("SELECT count(*) FROM files WHERE path LIKE 'sqlite:%'").fetchone()[0]
    n_f = con.execute("SELECT count(*) FROM files WHERE path NOT LIKE 'sqlite:%'").fetchone()[0]
    rng = con.execute("SELECT min(ts), max(ts) FROM msgs WHERE ts != ''").fetchone()
    size = os.path.getsize(DB) // 1024
    print(f"Реплик в памяти: {total} из {n_db + n_f} сессий (SQLite 2.0: {n_db}, файлов JSONL/архивов: {n_f})")
    print(f"Период: {(rng[0] or '?')[:10]} … {(rng[1] or '?')[:10]}")
    print(f"Размер индекса: {size} КБ")
    con.close()


def main():
    ap = argparse.ArgumentParser(description="Память по прошлым диалогам")
    sub = ap.add_subparsers(dest="cmd", required=True)
    i = sub.add_parser("index", help="обновить индекс")
    i.add_argument("--full", action="store_true", help="переиндексировать всё заново")
    i.set_defaults(func=cmd_index)
    s = sub.add_parser("search", help="искать в прошлых разговорах")
    s.add_argument("query")
    s.add_argument("--n", type=int, default=8)
    s.add_argument("--days", type=int, default=0, help="только за последние N дней")
    s.add_argument("--chars", type=int, default=700)
    s.set_defaults(func=cmd_search)
    st = sub.add_parser("stats")
    st.set_defaults(func=cmd_stats)
    a = ap.parse_args()
    a.func(a)


if __name__ == "__main__":
    main()
