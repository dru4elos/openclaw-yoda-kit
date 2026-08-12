#!/bin/bash
# yoda_backup.sh — ежедневный бэкап Йоды (vps) на этот Мак.
# Тянет конфиг, workspace (память/скиллы/правила), скрипты и сессию tg-шлюза.
set -u
BASE="$HOME/YodaBackups"
CUR="$BASE/current"
STAMP=$(date +%F)
SNAP="$BASE/snap-$STAMP"
LOG="$BASE/backup.log"
mkdir -p "$CUR"

{
echo "== yoda_backup $(date '+%F %T') =="

# 1. Конфиг и мозги OpenClaw (без тяжёлых сессий агента)
rsync -az --timeout=60 \
  --exclude 'workspace/media/' \
  --exclude '*.bak.*' \
  vps:/home/openclaw/.openclaw/openclaw.json \
  vps:/home/openclaw/.openclaw/.env \
  vps:/home/openclaw/.openclaw/research_mcp.py \
  vps:/home/openclaw/.openclaw/vision.py \
  vps:/home/openclaw/.openclaw/transcribe.py \
  "$CUR/openclaw/" || echo "WARN: конфиги"

rsync -az --timeout=120 --delete \
  --exclude 'media/' --exclude '*.bak.*' --exclude '__pycache__/' \
  vps:/home/openclaw/.openclaw/workspace/ "$CUR/workspace/" || echo "WARN: workspace"

rsync -az --timeout=60 --delete \
  vps:/home/openclaw/.openclaw/workspace-bg/ "$CUR/workspace-bg/" || echo "WARN: workspace-bg"

# 2. Root-хелперы и шлюз (включая живую сессию telethon)
rsync -az --timeout=60 \
  vps:/root/yoda_tg.py vps:/root/yoda_tg.sh \
  vps:/root/yoda_mgz.py vps:/root/yoda_mgz.sh \
  vps:/root/tg_gateway.py vps:/root/tg_gateway_watch.py \
  "$CUR/root-scripts/" || echo "WARN: root-скрипты"

rsync -az --timeout=60 --delete \
  vps:/root/tg_gateway_data/ "$CUR/tg_gateway_data/" || echo "WARN: сессия шлюза"

rsync -az --timeout=60 \
  vps:/usr/local/bin/yoda-smoke.sh vps:/usr/local/bin/sync_yoda_keys.sh \
  "$CUR/bin/" || echo "WARN: bin"

rsync -az --timeout=60 \
  vps:/home/knee_bot/gmail_science_monitor.py "$CUR/science/" || echo "WARN: science"

# 3. Датированный снапшот жёсткими ссылками (дёшево по месту)
if [ ! -d "$SNAP" ]; then
  cp -al "$CUR" "$SNAP" 2>/dev/null || cp -a "$CUR" "$SNAP"
  echo "снапшот: $SNAP"
fi

# 4. Ротация: держим 10 последних снапшотов
ls -dt "$BASE"/snap-* 2>/dev/null | tail -n +11 | xargs rm -rf 2>/dev/null

echo "OK: $(du -sh "$CUR" | cut -f1) в current, снапшотов: $(ls -d "$BASE"/snap-* 2>/dev/null | wc -l | tr -d " ")"
} >> "$LOG" 2>&1
