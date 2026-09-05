#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Сторож кронов для смоука: фоновые задания живут только на агенте background.
У main есть право отправлять сообщения от имени владельца — фоновой задаче оно
не положено. Чужие кроны перевешиваем сами (--agent background --session isolated),
без доставки — добавляем announce в Telegram владельцу.
Код выхода: 0 — всё чисто, 1 — перевесили (WARN), 2 — не смогли (FAIL)."""
import json, os, subprocess, sys

OWNER = os.environ.get("YODA_OWNER_TG", "123456789")
OK_AGENT = "background"


def run(*args):
    return subprocess.run(["openclaw", *args], capture_output=True, text=True, timeout=90)


r = run("cron", "list", "--json")
try:
    d = json.loads(r.stdout)
except Exception:
    print(f"cron list --json не распарсился: {r.stdout[:200]!r} {r.stderr[:200]!r}")
    sys.exit(2)
jobs = d if isinstance(d, list) else (d.get("jobs") or d.get("result") or [])
total = len(jobs)
bad = []
for j in jobs:
    agent = j.get("agentId") or j.get("agent") or ""
    decl = j.get("declarationKey") or j.get("declaration") or ""
    name = j.get("name") or j.get("displayName") or j.get("id", "")[:8]
    if str(decl).startswith("heartbeat") or str(name).startswith("Heartbeat"):
        continue                                  # heartbeat — законно на main
    if agent and agent != OK_AGENT:
        bad.append(j)
if not bad:
    print(f"все кроны на {OK_AGENT} (всего: {total})")
    sys.exit(0)

moved, stuck = [], []
for j in bad:
    name = j.get("name") or j.get("id", "")[:8]
    args = ["cron", "edit", j["id"], "--agent", OK_AGENT, "--session", "isolated"]
    pl = j.get("payload") or {}
    if pl.get("kind") == "systemEvent":          # событие для main-сессии → обычное задание агенту
        args += ["--message", pl.get("text") or name]
    dl = j.get("delivery") or {}
    mode = (dl.get("mode") if isinstance(dl, dict) else dl) or "none"
    if mode in ("none", "", None):
        args += ["--announce", "--channel", "telegram", "--to", OWNER]
    r = run(*args)
    if r.returncode == 0:
        moved.append(name)
    else:
        stuck.append(f"{name}: {(r.stderr or r.stdout).strip()[:120]}")
if stuck:
    print(f"не удалось перевесить на {OK_AGENT}: " + "; ".join(stuck))
    sys.exit(2)
print(f"перевешено на {OK_AGENT} (были на main): " + "; ".join(moved))
sys.exit(1)
