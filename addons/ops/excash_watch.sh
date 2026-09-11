#!/bin/bash
# Дозор за excash: ключ/баланс (401) или провайдер лежит (5xx/529) — сообщить владельцу через tome, когда состояние меняется. Крон root каждые 30 мин.
ENV=/home/openclaw/.openclaw/.env; ST=/var/tmp/excash_watch.state
K=$(python3 -c "import re;print(re.search(r'^EXCASH_API_KEY=(.*)$',open('$ENV').read(),re.M).group(1).strip().strip('\"'))")
U=$(python3 -c "import re;print(re.search(r'^EXCASH_API_URL=(.*)$',open('$ENV').read(),re.M).group(1).strip().strip('\"'))")
# /models отвечает 200 даже когда бэкенд моделей лежит — проверяем реальный chat через страж
code=$(curl -s -m 40 -o /tmp/excash_watch.json -w "%{http_code}" -H "Authorization: Bearer $K" -H "Content-Type: application/json" -d '{"model":"gemini-3.8-flash","messages":[{"role":"user","content":"ok?"}],"max_tokens":5}' http://127.0.0.1:8788/chat/completions)
prev=$(cat $ST 2>/dev/null || echo "?")
echo "$code" > $ST
say() { su - openclaw -c "~/mailvenv/bin/python ~/.openclaw/workspace/skills/tome/tome.py msg \"$1\"" >/dev/null 2>&1; }
case "$code" in
  200) [ "$prev" != "200" ] && say "✅ excash снова отвечает (HTTP 200). Если Йода был переведён на DeepSeek — вернуть: sudo yoda-models excash";;
  401|402|403) [ "$prev" != "$code" ] && say "⚠️ excash отвергает КЛЮЧ (HTTP $code) — обычно это нулевой баланс. Пополнить, затем при необходимости sudo yoda-models excash. Пока: sudo yoda-models deepseek";;
  5*|529|000) [ "$prev" != "$code" ] && say "⚠️ excash ЛЕЖИТ на стороне провайдера (HTTP $code, «backend restarting»). Ключ и баланс ни при чём. Йода сам уходит на резервы; если молчит долго — sudo yoda-models deepseek";;
esac

# Дозор за балансом DeepSeek (резерв всей Йоды и поисковых субагентов): ниже $2 — сказать один раз, после пополнения — отбой.
DK=$(python3 -c "import re;m=re.search(r'^DEEPSEEK_API_KEY=(.*)$',open('$ENV').read(),re.M);print(m.group(1).strip().strip('\"') if m else '')")
if [ -n "$DK" ]; then
  DST=/var/tmp/deepseek_watch.state
  bal=$(curl -s -m 20 -H "Authorization: Bearer $DK" https://api.deepseek.com/user/balance | python3 -c "import sys,json;d=json.load(sys.stdin);print(d['balance_infos'][0]['total_balance'])" 2>/dev/null)
  if [ -n "$bal" ]; then
    st=$(python3 -c "print('low' if float('$bal')<2 else 'ok')")
    dprev=$(cat $DST 2>/dev/null || echo "?")
    echo "$st" > $DST
    [ "$st" = "low" ] && [ "$dprev" != "low" ] && say "⚠️ Баланс DeepSeek \$$bal — резерв Йоды и поисковые субагенты на deepseek-flash скоро встанут (402). Пополнить: platform.deepseek.com"
    [ "$st" = "ok" ] && [ "$dprev" = "low" ] && say "✅ Баланс DeepSeek пополнен: \$$bal"
  fi
fi

exit 0
