#!/bin/bash
# Глубокое исследование hyperresearch (github.com/jordan-gibbs/hyperresearch, MIT) поверх Codex Йоды.
# Работает в фоне (таймер systemd пользователя), прогресс виден в живой полоске yoda-progress,
# готовый отчёт уходит доктору файлом в Telegram.
#
#   hr.sh start "<вопрос>" [--light|--full] [--model sol|astra]   запустить
#   hr.sh status                                                   что идёт и что готово
#   hr.sh stop <id>                                                остановить
set -uo pipefail
R=$HOME/projects/research
J=$R/.jobs
SELF_DIR=$(cd "$(dirname "$0")" && pwd)
mkdir -p "$J"
export XDG_RUNTIME_DIR=/run/user/$(id -u)

case "${1:-}" in
start)
  Q=${2:-}; [ -n "$Q" ] || { echo "нужен вопрос: hr.sh start \"<вопрос>\""; exit 2; }
  shift 2
  TIER=auto; MODEL=sol
  while [ $# -gt 0 ]; do case $1 in --light) TIER=light; shift;; --full) TIER=full; shift;;
    --model) MODEL=$2; shift 2;; *) echo "неизвестный ключ $1"; exit 2;; esac; done
  case $MODEL in sol|astra|luna) ;; *) echo "модель: sol | astra | luna"; exit 2;; esac
  if ls "$J"/*.json >/dev/null 2>&1 && grep -l '"status": "running"' "$J"/*.json >/dev/null 2>&1; then
    echo "уже идёт исследование — дождись или останови: hr.sh status"; exit 3
  fi
  ID=$(date +%m%d-%H%M%S)
  python3 - "$J/$ID.json" "$ID" "$Q" "$TIER" "$MODEL" <<'PY'
import json, sys, time
p, i, q, t, m = sys.argv[1:6]
json.dump({"id": i, "question": q, "tier": t, "model": m, "started": time.time(), "status": "running",
           "unit": f"hr-{i}"}, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
PY
  systemd-run --user --unit "hr-$ID" --collect --quiet /bin/bash "$SELF_DIR/hr_job.sh" "$ID" \
    || { echo "не удалось запустить фоновую задачу"; exit 1; }
  case $TIER in light) T="~30–40 мин";; full) T="~1,5–2,5 ч";; *) T="30 мин – 2,5 ч, зависит от вопроса";; esac
  echo "Запустил глубокое исследование (id $ID, модель $MODEL, $T). Прогресс — в полоске, отчёт пришлю файлом."
  ;;
status)
  python3 - "$J" <<'PY'
import glob, json, os, sys, time
rows = sorted(glob.glob(os.path.join(sys.argv[1], "*.json")), reverse=True)[:6]
if not rows:
    print("исследований ещё не было")
for p in rows:
    j = json.load(open(p, encoding="utf-8"))
    age = int((j.get("finished") or time.time()) - j["started"]) // 60
    print(f"{j['id']} · {j['status']} · {age} мин · {j['question'][:80]}" + (f" → {j['report']}" if j.get("report") else ""))
PY
  ;;
stop)
  ID=${2:-}; [ -n "$ID" ] || { echo "нужен id: hr.sh status"; exit 2; }
  systemctl --user stop "hr-$ID" 2>/dev/null
  python3 - "$J/$ID.json" <<'PY'
import json, sys, time
p = sys.argv[1]; j = json.load(open(p, encoding="utf-8"))
j.update(status="stopped", finished=time.time()); json.dump(j, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("остановлено:", j["question"][:80])
PY
  ;;
*) sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//'; exit 2;;
esac
