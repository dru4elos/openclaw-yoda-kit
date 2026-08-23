#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Память по ВСЕМ прошлым диалогам — полнотекстовый поиск вместо раздувания контекста.

  recall.py index [--full]        — обновить индекс (инкрементально по mtime)
  recall.py search "<запрос>" [--n 8] [--days N] [--chars 700]
  recall.py stats

Зачем: держать всю переписку в контексте нельзя — сессия пухнет и ответы
тормозят. Поэтому старое уезжает из контекста (сжатие + ротация), но остаётся
на диске и ищется здесь. Ассистент вспоминает точечно, а не носит всё с собой.
"""
import argparse
import json
import os
import re
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone

DB = os.path.expanduser("~/.openclaw/workspace/memory/recall.db")
ROOTS = [os.path.expanduser("~/.openclaw/agents")]
MIN_LEN = 40                       # реплики короче — служебный шум
SKIP = ("HEARTBEAT_OK", "NO_REPLY", "structuredContent:", "Command still running")


def _db():
    os.makedirs(os.path.dirname(DB), exist_ok=True)
    con = sqlite3.connect(DB)
    con.execute("""CREATE VIRTUAL TABLE IF NOT EXISTS msgs USING fts5(
        ts, session, role, text, tokenize='unicode61 remove_diacritics 2')""")
    con.execute("""CREATE TABLE IF NOT EXISTS files(
        path TEXT PRIMARY KEY, mtime REAL, msgs INTEGER)""")
    return con


def _files():
    out = []
    for root in ROOTS:
        for dirpath, _, names in os.walk(root):
            for n in names:
                if n.endswith(".jsonl") and "trajectory" not in n:
                    out.append(os.path.join(dirpath, n))
    return out


def _extract(path):
    """Реплики человека и ассистента: (ts, role, text)."""
    rows = []
    try:
        fh = open(path, encoding="utf-8", errors="replace")
    except Exception:
        return rows
    with fh:
        for line in fh:
            try:
                d = json.loads(line)
            except Exception:
                continue
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
            text = " ".join(parts).strip()
            text = re.sub(r"\s+", " ", text)
            if len(text) < MIN_LEN or any(s in text[:60] for s in SKIP):
                continue
            rows.append((ts, role, text[:4000]))
    return rows


def cmd_index(a):
    con = _db()
    known = {p: mt for p, mt in con.execute("SELECT path, mtime FROM files")}
    added = updated = 0
    for path in _files():
        mt = os.path.getmtime(path)
        if not a.full and known.get(path, 0) >= mt:
            continue
        sess = os.path.basename(path).replace(".jsonl", "")[:18]
        con.execute("DELETE FROM msgs WHERE session = ?", (sess,))
        rows = _extract(path)
        con.executemany("INSERT INTO msgs(ts, session, role, text) VALUES (?,?,?,?)",
                        [(ts, sess, role, text) for ts, role, text in rows])
        con.execute("INSERT OR REPLACE INTO files(path, mtime, msgs) VALUES (?,?,?)",
                    (path, mt, len(rows)))
        if path in known:
            updated += 1
        else:
            added += 1
    con.commit()
    total = con.execute("SELECT count(*) FROM msgs").fetchone()[0]
    print(f"Индекс обновлён: новых файлов {added}, обновлено {updated}. "
          f"Всего реплик в памяти: {total}")
    con.close()


def cmd_search(a):
    if not os.path.exists(DB):
        sys.exit("индекса ещё нет — сначала: recall.py index")
    con = _db()
    q = a.query.strip()
    # FTS5: слова через OR, чтобы находить по смыслу, а не по точной фразе
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
        who = "ВЛАДЕЛЕЦ" if role == "user" else "ТЫ"
        print(f"\n[{when}] {who} (сессия {sess}):")
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
    files = con.execute("SELECT count(*) FROM files").fetchone()[0]
    rng = con.execute("SELECT min(ts), max(ts) FROM msgs WHERE ts != ''").fetchone()
    size = os.path.getsize(DB) // 1024
    print(f"Реплик в памяти: {total} из {files} файлов сессий")
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
