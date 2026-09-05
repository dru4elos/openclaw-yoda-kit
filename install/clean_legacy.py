"""Ключи, которые doctor --fix 2026.8.1 убирает/переносит — делаем то же руками."""
import json, shutil, time, os, sys
H = sys.argv[1]; P = H + "/.openclaw/openclaw.json"
shutil.copy(P, P + ".bak.legacy.%d" % int(time.time()))
d = json.load(open(P, encoding="utf-8"))
log = []
def pop(path):
    o = d
    for k in path[:-1]:
        if not isinstance(o, dict) or k not in o: return None
        o = o[k]
    if isinstance(o, dict) and path[-1] in o:
        v = o.pop(path[-1]); log.append("- " + "/".join(path)); return v
pop(["meta", "lastTouchedAt"])
for k in ("reserveTokens", "truncateAfterCompaction", "maxHistoryShare"):
    pop(["agents", "defaults", "compaction", k])
ms = pop(["agents", "defaults", "memorySearch"])
if ms is not None:
    d.setdefault("memory", {}).setdefault("search", {})["enabled"] = bool(ms.get("enabled", False)); log.append("+ memory/search/enabled=%s" % d["memory"]["search"]["enabled"])
pop(["agents", "entries", "background", "runRetries"])
pop(["agents", "defaults", "runRetries"])
img = pop(["tools", "media", "image", "models"])
aud = pop(["tools", "media", "audio", "models"])
if img: d.setdefault("tools", {}).setdefault("media", {})["models"] = img; log.append("+ tools/media/models (из image)")
for k in ("image", "audio"):
    if isinstance(d.get("tools", {}).get("media", {}).get(k), dict) and not d["tools"]["media"][k]: d["tools"]["media"].pop(k)
pop(["mcp", "servers", "research", "connectTimeout"])
pop(["audio"])
json.dump(d, open(P, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
u = os.path.basename(H); os.system("chown %s:%s %s" % (u, u, P))
print("\n".join("  " + l for l in log))
