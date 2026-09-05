#!/bin/bash
# yoda-smoke.sh — смоук-проверка всего контура Йоды. Запуск: root.
# Код выхода 0 = всё зелёное. Любой FAIL → код 1 + алерт доктору ботом.
FAILS=""
ok()   { printf "  OK   %s\n" "$1"; }
fail() { printf "  FAIL %s\n" "$1"; FAILS="$FAILS\n- $1"; }
warn(){ echo "  WARN $1"; }

echo "== yoda-smoke $(date '+%F %T') =="

# 1. gateway OpenClaw
if su - openclaw -c 'export XDG_RUNTIME_DIR=/run/user/$(id -u); systemctl --user is-active openclaw-gateway' 2>/dev/null | grep -q '^active'; then
  ok "openclaw-gateway active"
else fail "openclaw-gateway НЕ active"; fi


# 1b. гейтвей не только active, но и СЛУШАЕТ: после апгрейда 2.0 процесс падал
# с кодом 1 (миграции/согласие плагинов), а systemd в рестарт-петле показывал active
ss -ltn 2>/dev/null | grep -q ":18789 " && ok "gateway слушает :18789" || fail "gateway НЕ слушает :18789 (active, но порт молчит — смотри journalctl)"

# 2. единый шлюз Telegram
H=$(curl -s -m 10 http://127.0.0.1:8099/health 2>/dev/null)
echo "$H" | grep -q '"authorized":true' && ok "tg-gateway authorized" || fail "tg-gateway: $H"

# 3. все MCP отвечают
PROBE=$(su - openclaw -c 'source ~/.nvm/nvm.sh 2>/dev/null; timeout 180 openclaw mcp probe 2>&1')
for m in research arxiv biomcp excel files medtools scitools studio; do
  echo "$PROBE" | grep -q "^- $m:" && ok "MCP $m" || fail "MCP $m не отвечает"
done


# 3b. виртуальный экран и звук для записи эфиров
for svc in xvfb-webrec pulse-webrec; do
  st=$(sudo -u openclaw XDG_RUNTIME_DIR=/run/user/$(id -u openclaw) DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/$(id -u openclaw)/bus systemctl --user is-active $svc 2>/dev/null)
  [ "$st" = "active" ] && ok "svc $svc" || fail "svc $svc: $st"
done

# 3c. сторож excash: без него молчание вместо фолбэка моделей
st=$(sudo -u openclaw XDG_RUNTIME_DIR=/run/user/$(id -u openclaw) DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/$(id -u openclaw)/bus systemctl --user is-active excash-guard 2>/dev/null)
[ "$st" = "active" ] && ok "svc excash-guard" || fail "svc excash-guard: $st"
curl -s -m 8 http://127.0.0.1:8788/_guard/health | grep -q ok && ok "excash-guard отвечает" || fail "excash-guard не отвечает"
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
for s in tg-gateway content-studio science_monitor antispam-guard; do
  [ "$(systemctl is-active $s 2>/dev/null)" = "active" ] && ok "svc $s" || fail "svc $s не active"
done

# 6. диск
USE=$(df / --output=pcent | tail -1 | tr -dc '0-9')
[ "$USE" -lt 90 ] && ok "диск ${USE}%" || fail "диск заполнен на ${USE}%"

# 7. конфиг валиден
su - openclaw -c 'source ~/.nvm/nvm.sh 2>/dev/null; openclaw config validate 2>&1' | grep -q 'Config valid' \
  && ok "openclaw.json валиден" || fail "openclaw.json НЕ валиден"

# 8. кроны — только на background (у main право отправки). Чужие сторож перевешивает сам.
CG=$(su - openclaw -c 'source ~/.nvm/nvm.sh 2>/dev/null; timeout 150 python3 /usr/local/bin/yoda-cron-guard.py 2>&1'); RC=$?
case $RC in 0) ok "$CG";; 1) warn "$CG";; *) fail "$CG";; esac

# 9. автокоммит workspace (история изменений)
su - openclaw -c 'cd ~/.openclaw/workspace && git add -A >/dev/null 2>&1 && git -c user.name=smoke -c user.email=smoke@vps commit -q -m "auto: смоук-снапшот $(date +%F)" 2>/dev/null; true'
ok "git-снапшот workspace"

if [ -n "$FAILS" ]; then
  echo "== ИТОГ: ЕСТЬ ПРОВАЛЫ =="
  TOKEN=$(grep -o '"botToken"[[:space:]]*:[[:space:]]*"[^"]*"' /home/openclaw/.openclaw/openclaw.json | head -1 | sed 's/.*"\([0-9][^"]*\)"$/\1/')
  if [ -n "$TOKEN" ]; then
    curl -s -m 10 "https://api.telegram.org/bot${TOKEN}/sendMessage" \
      -d chat_id=123456789 --data-urlencode "text=🚨 yoda-smoke: провалы:$(echo -e $FAILS)" >/dev/null
  fi
  exit 1
fi
echo "== ИТОГ: ВСЁ ЗЕЛЁНОЕ =="
