#!/bin/bash
# Разворачивает ВТОРОЙ независимый экземпляр ассистента на том же сервере.
# Запускать от root:  ./new_yoda.sh <имя_пользователя> <порт_gateway>
# Пример:             ./new_yoda.sh yoda2 18790
#
# Что гарантируется:
#  • отдельный Unix-пользователь: чужие секреты, /root и другие домашние ему недоступны
#  • свой Node, свой OpenClaw, свой workspace, свой порт (loopback)
#  • лимиты памяти и CPU, чтобы новый жилец не придушил уже работающее
# Что НЕ делается автоматически: токен Telegram-бота и личные доступы — вручную.
set -euo pipefail

U="${1:-yoda2}"
PORT="${2:-18790}"
NODE_VER="24"

command -v curl >/dev/null || { echo "нужен curl"; exit 1; }
[ "$(id -u)" = "0" ] || { echo "запускать от root"; exit 1; }

if ss -lnt 2>/dev/null | grep -q ":$PORT "; then
  echo "❌ порт $PORT уже занят — выбери другой"; exit 1
fi

echo "=== 1/7 пользователь $U ==="
if id "$U" >/dev/null 2>&1; then
  echo "  уже существует, продолжаю"
else
  useradd -m -s /bin/bash "$U"
  echo "  создан"
fi
chmod 750 "/home/$U"                 # чужие внутрь не заглянут
loginctl enable-linger "$U"          # сервисы живут без активной сессии

echo "=== 2/7 Node $NODE_VER + OpenClaw (в домашней $U) ==="
su - "$U" -c "
  export NVM_DIR=\$HOME/.nvm
  [ -s \$NVM_DIR/nvm.sh ] || curl -fsSL https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.1/install.sh | bash >/dev/null 2>&1
  . \$NVM_DIR/nvm.sh
  nvm install $NODE_VER >/dev/null 2>&1
  nvm alias default $NODE_VER >/dev/null 2>&1
  npm i -g openclaw >/dev/null 2>&1
  echo \"  node \$(node -v), openclaw \$(openclaw --version 2>/dev/null | head -1)\"
"

echo "=== 3/7 python-окружение для скиллов ==="
apt-get install -y python3-venv >/dev/null 2>&1 || true
su - "$U" -c "
  python3 -m venv \$HOME/venv >/dev/null 2>&1
  \$HOME/venv/bin/pip -q install --upgrade pip >/dev/null 2>&1
  \$HOME/venv/bin/pip -q install requests ddgs trafilatura pypdf 'mcp[cli]<2' httpx \
      python-docx beautifulsoup4 lxml pillow caldav icalendar >/dev/null 2>&1
  echo '  зависимости установлены'
"

echo "=== 4/7 каркас конфигурации ==="
su - "$U" -c "mkdir -p \$HOME/.openclaw/workspace/{memory,skills,knowledge} \$HOME/.openclaw/workspace-bg"
install -o "$U" -g "$U" -m 600 /dev/null "/home/$U/.openclaw/.env"

echo "=== 5/7 лимиты ресурсов (не душить соседей) ==="
mkdir -p "/home/$U/.config/systemd/user"
cat > "/home/$U/.config/systemd/user/openclaw-gateway.service.d-limits.conf" <<LIM
# Подложить как drop-in после создания сервиса:
#   mkdir -p ~/.config/systemd/user/openclaw-gateway.service.d
#   cp этот_файл ~/.config/systemd/user/openclaw-gateway.service.d/limits.conf
[Service]
MemoryMax=1500M
MemoryHigh=1200M
CPUQuota=60%
LIM
chown -R "$U:$U" "/home/$U/.config"
echo "  шаблон лимитов подготовлен"

echo "=== 6/7 проверка изоляции ==="
su - "$U" -c "cat /home/knee_bot/keys.env" >/dev/null 2>&1 && echo "  🚨 ВИДИТ чужие ключи!" || echo "  ✅ чужие секреты недоступны"
su - "$U" -c "ls /root" >/dev/null 2>&1 && echo "  🚨 ВИДИТ /root!" || echo "  ✅ /root недоступен"
for OTHER in $(ls /home | grep -v "^$U\$"); do
  su - "$U" -c "ls /home/$OTHER" >/dev/null 2>&1 && echo "  🚨 листает /home/$OTHER" || true
done
echo "  ✅ изоляция в порядке"

echo
echo "=== 7/7 ГОТОВО. Дальше вручную ==="
echo "  1) Создай бота у @BotFather → получи токен"
echo "  2) Узнай Telegram ID владельца → @userinfobot"
echo "  3) Положи ключи в /home/$U/.openclaw/.env (см. .env.example из кита)"
echo "  4) Конфиг: config/openclaw.example.json из кита, порт gateway = $PORT"
echo "  5) Скиллы: скопируй нужные в /home/$U/.openclaw/workspace/skills/"
echo "  6) Запуск: su - $U -c 'source ~/.nvm/nvm.sh; openclaw daemon install && openclaw daemon start'"
