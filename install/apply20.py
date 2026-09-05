"""Безопасные умолчания и полезные фичи OpenClaw 2.0. Идемпотентно."""
import json, shutil, time, os, sys
HOME = sys.argv[1]                       # /home/yoda2 или /home/openclaw
FLASH = "excash/gemini-3.8-flash"
P = HOME + "/.openclaw/openclaw.json"
shutil.copy(P, P + ".bak.apply20.%d" % int(time.time()))
d = json.load(open(P, encoding="utf-8"))
def setp(path, value):
    o = d
    for k in path[:-1]:
        o = o.setdefault(k, {})
    old = o.get(path[-1], "<нет>")
    o[path[-1]] = value
    print("  %-62s %s -> %s" % ("/".join(path), json.dumps(old, ensure_ascii=False)[:28], json.dumps(value, ensure_ascii=False)[:28]))
# 1. самообучение: агент НЕ меняет себя без человека (инцидент 07.08 помним)
setp(["skills", "workshop", "autonomous", "mode"], "off")
# 2. dreaming: фоновая консолидация памяти моделью — фоновые вызовы без сметы
setp(["plugins", "entries", "memory-core", "enabled"], True)
setp(["plugins", "entries", "memory-core", "config", "dreaming", "enabled"], False)
# 3. Active Memory (Hermes-подобная память между чатами) — оставляем, но на дешёвой
setp(["plugins", "entries", "active-memory", "enabled"], True)
setp(["plugins", "entries", "active-memory", "config", "model"], FLASH)
setp(["plugins", "entries", "active-memory", "config", "agents"], ["main"])
# 4. выжимка памяти перед сжатием — тоже на дешёвой
setp(["agents", "defaults", "compaction", "memoryFlush", "enabled"], True)
setp(["agents", "defaults", "compaction", "memoryFlush", "model"], FLASH)
# 5. Telegram: настоящие таблицы/чек-листы (Bot API 10.x)
setp(["channels", "telegram", "richMessages"], True)
json.dump(d, open(P, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
u = os.path.basename(HOME); os.system("chown %s:%s %s" % (u, u, P))
