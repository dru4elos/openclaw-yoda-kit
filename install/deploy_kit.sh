#!/bin/bash
# Раскладывает скиллы и конфиг второму экземпляру из GitHub-кита.
set -euo pipefail
U="${1:-yoda2}"
PORT="${2:-18790}"
KIT=/tmp/yoda-kit

rm -rf "$KIT"
git clone -q --depth 1 https://github.com/dru4elos/openclaw-yoda-kit "$KIT"
echo "кит склонирован: $(ls "$KIT/addons/skills" | tr '\n' ' ')"

W="/home/$U/.openclaw/workspace"
# базовый набор — без почты/календаря/телеграма/слака (подключим позже)
for S in images docs weather say tome recall scinews; do
  cp -r "$KIT/addons/skills/$S" "$W/skills/" 2>/dev/null && echo "  + скилл $S"
done
cp "$KIT/addons/research-mcp/research_mcp.py" "/home/$U/.openclaw/"
cp "$KIT/addons/multimodal/vision.py" "$KIT/addons/multimodal/transcribe.py" "/home/$U/.openclaw/"
cp "$KIT/config/AGENTS.example.md"            "$W/AGENTS.md"
cp "$KIT/config/AGENTS-background.example.md" "/home/$U/.openclaw/workspace-bg/AGENTS.md"
cp "$KIT/.env.example" "/home/$U/.openclaw/.env.example"

# пути под нового пользователя: у него venv в ~/venv, а в ките ~/mailvenv
grep -rl "mailvenv" "$W" "/home/$U/.openclaw" 2>/dev/null | while read -r f; do
  sed -i "s#~/mailvenv#\$HOME/venv#g; s#/home/openclaw/mailvenv#/home/$U/venv#g" "$f"
done
sed -i "s#/home/openclaw#/home/$U#g" "/home/$U/.openclaw/vision.py" \
  "/home/$U/.openclaw/transcribe.py" "/home/$U/.openclaw/research_mcp.py" 2>/dev/null || true

chown -R "$U:$U" "/home/$U/.openclaw"
rm -rf "$KIT"
echo "готово: скиллы и правила разложены, пути переписаны под $U"
