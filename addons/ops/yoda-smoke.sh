#!/bin/bash
# yoda-smoke.sh — смоук-проверка всего контура Йоды. Запуск: root.
# Код выхода 0 = всё зелёное. Любой FAIL → код 1 + алерт доктору ботом.
# ↓ подредактируй под свой набор
MCP_LIST="research"
SVC_LIST="tg-gateway"
OWNER_TG_ID="123456789"
FAILS=""
ok()   { printf "  OK   %s\n" "$1"; }
fail() { printf "  FAIL %s\n" "$1"; FAILS="$FAILS\n- $1"; }

echo "== yoda-smoke $(date '+%F %T') =="

# 1. gateway OpenClaw
if su - openclaw -c 'export XDG_RUNTIME_DIR=/run/user/$(id -u); systemctl --user is-active openclaw-gateway' 2>/dev/null | grep -q '^active'; then
  ok "openclaw-gateway active"
else fail "openclaw-gateway НЕ active"; fi

# 2. единый шлюз Telegram
H=$(curl -s -m 10 http://127.0.0.1:8099/health 2>/dev/null)
echo "$H" | grep -q '"authorized":true' && ok "tg-gateway authorized" || fail "tg-gateway: $H"

# 3. все MCP отвечают
PROBE=$(su - openclaw -c 'source ~/.nvm/nvm.sh 2>/dev/null; timeout 180 openclaw mcp probe 2>&1')
for m in $MCP_LIST; do
  echo "$PROBE" | grep -q "^- $m:" && ok "MCP $m" || fail "MCP $m не отвечает"
done

# 4. веб-поиск реально работает
R=$(timeout 90 /home/openclaw/mailvenv/bin/python - 2>/dev/null <<'PYEOF'
import importlib.util
spec = importlib.util.spec_from_file_location("rm", "/home/openclaw/.openclaw/research_mcp.py")
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
print(m.research_status())
PYEOF
)
echo "$R" | grep -q 'DuckDuckGo: OK' && ok "веб-поиск (DDG)" || fail "веб-поиск сломан: $(echo $R | head -c 100)"

# 5. сервисы-спутники
for s in $SVC_LIST; do
  [ "$(systemctl is-active $s 2>/dev/null)" = "active" ] && ok "svc $s" || fail "svc $s не active"
done

# 6. диск
USE=$(df / --output=pcent | tail -1 | tr -dc '0-9')
[ "$USE" -lt 90 ] && ok "диск ${USE}%" || fail "диск заполнен на ${USE}%"

# 7. конфиг валиден
su - openclaw -c 'source ~/.nvm/nvm.sh 2>/dev/null; openclaw config validate 2>&1' | grep -q 'Config valid' \
  && ok "openclaw.json валиден" || fail "openclaw.json НЕ валиден"

# 8. кроны привязаны к background
CRON=$(su - openclaw -c 'source ~/.nvm/nvm.sh 2>/dev/null; timeout 60 openclaw cron list 2>/dev/null')
TOTAL=$(echo "$CRON" | tail -n +3 | grep -c .)
ON_MAIN=$(echo "$CRON" | tail -n +3 | grep -c " main ")
if [ "$ON_MAIN" -eq 0 ]; then ok "все кроны на background (всего: $TOTAL)"
else fail "$ON_MAIN крон(ов) висят на main — фон получит право отправки!"; fi

# 9. автокоммит workspace (история изменений)
su - openclaw -c 'cd ~/.openclaw/workspace && git add -A >/dev/null 2>&1 && git -c user.name=smoke -c user.email=smoke@vps commit -q -m "auto: смоук-снапшот $(date +%F)" 2>/dev/null; true'
ok "git-снапшот workspace"

if [ -n "$FAILS" ]; then
  echo "== ИТОГ: ЕСТЬ ПРОВАЛЫ =="
  TOKEN=$(grep -o '"botToken"[[:space:]]*:[[:space:]]*"[^"]*"' /home/openclaw/.openclaw/openclaw.json | head -1 | sed 's/.*"\([0-9][^"]*\)"$/\1/')
  if [ -n "$TOKEN" ]; then
    curl -s -m 10 "https://api.telegram.org/bot${TOKEN}/sendMessage" \
      -d chat_id=$OWNER_TG_ID --data-urlencode "text=🚨 yoda-smoke: провалы:$(echo -e $FAILS)" >/dev/null
  fi
  exit 1
fi
echo "== ИТОГ: ВСЁ ЗЕЛЁНОЕ =="
