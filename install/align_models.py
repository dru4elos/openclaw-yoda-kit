"""Маше — те же модели, что у доктора. Только модели: личные интеграции не трогаем."""
import json, shutil, time, os
SRC = "/home/<user>/.openclaw/openclaw.json"; DST = "/home/<guest>/.openclaw/openclaw.json"
shutil.copy(DST, DST + ".bak.align.%d" % int(time.time()))
a = json.load(open(SRC, encoding="utf-8")); b = json.load(open(DST, encoding="utf-8"))
src_models = {m["id"]: m for m in a["models"]["providers"]["excash"]["models"]}
prov = b["models"]["providers"].setdefault("excash", {})
dst_models = {m["id"]: m for m in prov.get("models", [])}
for mid in ("gpt-6-astra-1m", "gpt-6-astra", "gpt-5.6-sol-1m", "gpt-5.6-sol-1m-fast", "gemini-3.8-flash", "gemini-3.7-flash-tiered", "gpt-5.6-sol"):
    if mid in src_models:
        dst_models[mid] = src_models[mid]
prov["models"] = list(dst_models.values())
bd = b["agents"]["defaults"]; ad = a["agents"]["defaults"]
bd["model"] = json.loads(json.dumps(ad["model"]))
bd.setdefault("subagents", {})["model"] = ad["subagents"]["model"]
bd.setdefault("compaction", {})["model"] = ad["compaction"]["model"]
json.dump(b, open(DST, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
os.system("chown <guest>:<guest> " + DST)
print("  primary:", bd["model"]["primary"]); print("  fallbacks:", " -> ".join(x.split("/")[-1] for x in bd["model"]["fallbacks"]))
print("  subagents:", bd["subagents"]["model"], "| compaction:", bd["compaction"]["model"])
print("  channels у Маши (личных интеграций нет):", list(b.get("channels", {}).keys()), "| mcp:", list((b.get("mcp") or {}).get("servers", {}).keys()))
