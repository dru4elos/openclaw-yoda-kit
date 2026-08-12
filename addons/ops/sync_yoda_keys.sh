#!/bin/bash
# sync_yoda_keys.sh — синк ОБЩИХ ключей из канонического /home/knee_bot/keys.env
# в /home/openclaw/.openclaw/.env. Только белый список — изоляция openclaw сохраняется
# (токены ботов, банковские и пациентские ключи НЕ копируются).
set -u
SRC=/home/knee_bot/keys.env
DST=/home/openclaw/.openclaw/.env
WHITELIST="EXCASH_API_KEY EXCASH_API_URL DEEPSEEK_API_KEY GROQ_API_KEY"

[ -f "$SRC" ] || { echo "нет $SRC"; exit 1; }
[ -f "$DST" ] || { echo "нет $DST"; exit 1; }

changed=0
for K in $WHITELIST; do
  V=$(grep -E "^${K}=" "$SRC" | tail -1 | cut -d= -f2-)
  [ -n "$V" ] || continue
  CUR=$(grep -E "^${K}=" "$DST" | tail -1 | cut -d= -f2-)
  if [ "$V" != "$CUR" ]; then
    if grep -qE "^${K}=" "$DST"; then
      # sed с | как разделителем — в ключах бывают / и =
      sed -i "s|^${K}=.*|${K}=${V}|" "$DST"
    else
      echo "${K}=${V}" >> "$DST"
    fi
    echo "обновлён: $K"
    changed=1
  fi
done
chown openclaw:openclaw "$DST"
chmod 600 "$DST"
[ "$changed" = "0" ] && echo "все ключи актуальны"
