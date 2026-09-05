"""Ручной импорт legacy workspace-state в SQLite 2.0 (doctor этот шаг у нас не выполняет)."""
import sqlite3, json, hashlib, os, shutil, time, glob
H = "/home/<user>/.openclaw"; DB = H + "/state/openclaw.sqlite"
ts = int(time.time()); bak = "%s/legacy-backup-%d" % (H, ts); os.makedirs(bak, exist_ok=True)
shutil.copy(DB, "%s/openclaw.sqlite.before-ws-import" % bak)
c = sqlite3.connect(DB); now_ms = int(time.time() * 1000)
for ws in ("workspace", "workspace-bg"):
    path = "%s/%s" % (H, ws); key = hashlib.sha256(path.encode()).hexdigest()
    src = "%s/openclaw-workspace-state.json" % path
    st = json.load(open(src)) if os.path.exists(src) else {}
    att = "%s/workspace-attestations/%s.attested" % (H, key)
    att_ms = int(os.path.getmtime(att) * 1000) if os.path.exists(att) else None
    c.execute("INSERT OR REPLACE INTO workspace_setup_state (workspace_key, workspace_path, version, bootstrap_seeded_at, setup_completed_at, updated_at, attested_at_ms, attestation_updated_at_ms) VALUES (?,?,?,?,?,?,?,?)",
              (key, path, st.get("version", 1), st.get("bootstrapSeededAt"), st.get("setupCompletedAt"), now_ms, att_ms, att_ms))
    print("  импортирован %s: key=%s… setupCompletedAt=%s" % (ws, key[:12], st.get("setupCompletedAt")))
    if os.path.exists(src): shutil.move(src, "%s/%s.openclaw-workspace-state.json" % (bak, ws)); print("    legacy json -> бэкап")
    if os.path.exists(att): shutil.move(att, "%s/%s.attested" % (bak, key[:16])); print("    .attested -> бэкап")
c.commit()
for r in c.execute("select workspace_key, workspace_path, setup_completed_at from workspace_setup_state"): print("  в базе:", r[0][:12], r[1].split("/")[-1], r[2])
os.system("chown -R openclaw:openclaw %s %s" % (bak, DB)); print("  бэкап:", bak)
