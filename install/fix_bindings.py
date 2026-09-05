"""2.0: при нескольких агентах каждому каналу нужен явный владелец (bindings)."""
import json, shutil, time, os, sys
H = sys.argv[1]; P = H + "/.openclaw/openclaw.json"
shutil.copy(P, P + ".bak.bind.%d" % int(time.time()))
d = json.load(open(P, encoding="utf-8"))
chans = [c for c in (d.get("channels") or {}) if c in ("telegram", "whatsapp", "slack")]
b = d.get("bindings") or []
have = {(x.get("match") or {}).get("channel") for x in b}
for c in chans:
    if c not in have:
        b.append({"agentId": "main", "match": {"channel": c, "accountId": "*"}})
d["bindings"] = b
d.setdefault("talk", {})["agentId"] = "main"
d.setdefault("agents", {})["ownership"] = "explicit"
json.dump(d, open(P, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
u = os.path.basename(H); os.system("chown %s:%s %s" % (u, u, P))
print("  bindings:", json.dumps(d["bindings"], ensure_ascii=False)); print("  talk:", d["talk"])
