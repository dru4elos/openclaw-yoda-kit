#!/usr/bin/python3
# -*- coding: utf-8 -*-
"""Поставить скилл diagram-design Йоде (только папку скилла, коммит закреплён) в палитре docsemenov.ru."""
import os
import pwd
import re
import shutil
import subprocess

SRC = "/tmp/dd_src/skills/diagram-design"
DST = "/home/openclaw/.openclaw/workspace/skills/diagram-design"
commit = subprocess.run(["git", "-C", "/tmp/dd_src", "rev-parse", "--short", "HEAD"],
                        capture_output=True, text=True).stdout.strip()

if os.path.exists(DST):
    shutil.rmtree(DST)
shutil.copytree(SRC, DST)

# Палитра сайта доктора (docsemenov.ru): кремовый фон, коралловый акцент, бирюзовые ссылки.
LIGHT = {
    "paper": "#fff9f2", "paper-2": "#f6eee4", "ink": "#2b2330", "ink-strong": "#111111",
    "muted": "#5e5566", "soft": "#8a8190", "rule": "rgba(43,35,48,0.12)", "rule-solid": "#e4d9cc",
    "accent": "#e85d3c", "accent-tint": "rgba(232,93,60,0.08)", "link": "#0b8570",
}
DARK = {
    "paper": "#221c25", "paper-2": "#2d2630", "ink": "#fff9f2", "ink-strong": "#111111",
    "muted": "#cfc5bb", "soft": "#9d93a3", "rule": "rgba(255,249,242,0.12)", "rule-solid": "rgba(228,217,204,0.25)",
    "accent": "#f07a5e", "accent-tint": "rgba(240,122,94,0.10)", "link": "#3fb39b",
}
p = os.path.join(DST, "references", "style-guide.md")
s = open(p, encoding="utf-8").read()
for role in LIGHT:
    rx = re.compile(r"^(\| `" + re.escape(role) + r"` \| [^|]+\| )`[^`]+`(?: \([^)]*\))? \| `[^`]+`(?: \([^)]*\))? \|", re.M)
    s, n = rx.subn(lambda m: f"{m.group(1)}`{LIGHT[role]}` | `{DARK[role]}` |", s, count=1)
    assert n == 1, f"не нашёл строку токена {role}"
s = s.replace("> **Brand palette source:**",
              "> **Бренд доктора Семёнова (docsemenov.ru), настроено 28.09.2026:** кремовый `#fff9f2`, коралловый "
              "`#e85d3c` (акцент — 1–2 элемента), бирюзовый `#0b8570` (связи), тёмно-сливовый текст `#2b2330`.\n"
              "> Шрифты — штатные скилла: Geist и Noto Serif несут кириллицу.\n\n> **Исходная палитра пакета:**", 1)
open(p, "w", encoding="utf-8").write(s)

open(os.path.join(DST, "SOURCE.md"), "w", encoding="utf-8").write(
    "# Откуда скилл\n\n"
    f"github.com/cathrynlavery/diagram-design, MIT, коммит `{commit}` (27.09.2026). Поставлена только папка\n"
    "`skills/diagram-design`; скрипты сборки и тестов репозитория не ставились. Скрипты скилла проверены:\n"
    "сети не трогают. Палитра заменена на бренд docsemenov.ru — поэтому первая схема идёт без вопросов.\n"
    "Обновлять только осознанно: клонировать заново, сверить скрипты, вернуть палитру.\n")

pw = pwd.getpwnam("openclaw")
for root, dirs, files in os.walk(DST):
    os.chown(root, pw.pw_uid, pw.pw_gid)
    for f in files:
        os.chown(os.path.join(root, f), pw.pw_uid, pw.pw_gid)
print(f"diagram-design {commit} → {DST}, палитра docsemenov.ru")
