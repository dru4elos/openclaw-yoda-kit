#!/bin/bash
# Дозор за excash: когда ключ снова принимают — сказать доктору и напомнить вернуть модели. Крон root каждые 30 мин.
ENV=/home/openclaw/.openclaw/.env; ST=/var/tmp/excash_watch.state
K=$(python3 -c "import re;print(re.search(r'^EXCASH_API_KEY=(.*)$',open('$ENV').read(),re.M).group(1).strip().strip('\"'))")
U=$(python3 -c "import re;print(re.search(r'^EXCASH_API_URL=(.*)$',open('$ENV').read(),re.M).group(1).strip().strip('\"'))")
code=$(curl -s -m 20 -o /tmp/excash_watch.json -w "%{http_code}" -H "Authorization: Bearer $K" "$U/models")
prev=$(cat $ST 2>/dev/null || echo "?")
echo "$code" > $ST
if [ "$code" = "200" ] && [ "$prev" != "200" ]; then
  su - openclaw -c "~/mailvenv/bin/python ~/.openclaw/workspace/skills/tome/tome.py msg '✅ excash снова принимает ключ (HTTP 200). Йода пока на DeepSeek — вернуть цепочку: sudo yoda-models excash'" >/dev/null 2>&1
elif [ "$code" != "200" ] && [ "$prev" = "200" ]; then
  su - openclaw -c "~/mailvenv/bin/python ~/.openclaw/workspace/skills/tome/tome.py msg '⚠️ excash перестал принимать ключ (HTTP $code): $(head -c 120 /tmp/excash_watch.json). Если Йода замолчит — sudo yoda-models deepseek'" >/dev/null 2>&1
fi
