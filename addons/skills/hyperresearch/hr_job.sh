#!/bin/bash
# Тело фонового исследования: Codex + hyperresearch в ~/projects/research, потом отчёт доктору.
# Запускается только из hr.sh start (таймер systemd пользователя).
set -uo pipefail
ID=$1
R=$HOME/projects/research
JOB=$R/.jobs/$ID.json
LOG=$R/.jobs/$ID.log
source ~/.nvm/nvm.sh >/dev/null 2>&1
export PATH="$HOME/hr-venv/bin:$PATH"
EXCASH_API_KEY=$(grep '^EXCASH_API_KEY=' "$HOME/.openclaw/.env" | cut -d= -f2- | tr -d "\"'")
export EXCASH_API_KEY
TOME="$HOME/mailvenv/bin/python $HOME/.openclaw/workspace/skills/tome/tome.py"

jget() { python3 -c "import json,sys; print(json.load(open(sys.argv[1], encoding='utf-8'))[sys.argv[2]])" "$JOB" "$1"; }
Q=$(jget question); TIER=$(jget tier); MODEL=$(jget model); START=$(jget started)

case $TIER in
  light) DIRECTIVE="Tier: light (bounded survey, fast path).";;
  full)  DIRECTIVE="Tier: full (deep argumentative analysis).";;
  *)     DIRECTIVE="";;
esac
PROMPT="\$hyperresearch $Q

$DIRECTIVE Write the final report in Russian. Keep source titles and quotes in their original language."

cd "$R"
timeout 21600 codex exec -p "$MODEL" --skip-git-repo-check -s workspace-write \
  -c sandbox_workspace_write.network_access=true -c 'web_search="live"' \
  --dangerously-bypass-hook-trust "$PROMPT" < /dev/null > "$LOG" 2>&1
CODE=$?

REPORT=$(find "$R/research/notes" -maxdepth 1 -name 'final_report_*.md' -newermt "@${START%.*}" 2>/dev/null | head -1)
python3 - "$JOB" "$CODE" "$REPORT" <<'PY'
import json, sys, time
p, code, rep = sys.argv[1], int(sys.argv[2]), sys.argv[3]
j = json.load(open(p, encoding="utf-8"))
j.update(finished=time.time(), exit_code=code, status="done" if rep else "failed")
if rep:
    j["report"] = rep
json.dump(j, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
PY

MIN=$(( ( $(date +%s) - ${START%.*} ) / 60 ))
if [ -n "$REPORT" ]; then
  OUT="$R/.jobs/Исследование_$ID.md"
  cp "$REPORT" "$OUT"
  if command -v pandoc >/dev/null 2>&1; then
    pandoc "$REPORT" -o "$R/.jobs/Исследование_$ID.docx" 2>/dev/null && OUT="$R/.jobs/Исследование_$ID.docx"
  fi
  $TOME file "$OUT" --caption "📚 Глубокое исследование готово за $MIN мин: ${Q:0:300}" >/dev/null 2>&1
else
  $TOME msg "⚠️ Глубокое исследование не дошло до отчёта ($MIN мин, код $CODE): ${Q:0:200}. Журнал: $LOG" >/dev/null 2>&1
fi
