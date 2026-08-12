# tg-gateway — единый шлюз к личному Telegram

Telethon-сервис (FastAPI, 127.0.0.1:8099), держащий **единственное** подключение
к личному аккаунту. Всё остальное ходит в него по HTTP.

**Зачем:** два клиента одной сессией с разных путей = Telegram убивает ключ
(AuthKeyDuplicatedError), сессия умирает безвозвратно. Пройдено.

## Состав
- `tg_gateway.py` — сервис: /health, /me, /dialogs, /read, /search, /send, /media
- `tg_gateway_watch.py` — сторож: сам рестартует зависший шлюз (кроме смерти ключа)
- `tg_login.py` — создание сессии (координаты только из одного env-файла)
- `yoda_tg.py` + `yoda_tg.sh` — клиент для агента: dialogs/read/search/digest/send
- `tg-gateway.service` — systemd-юнит
- `sudoers-openclaw-tg.example` — доступ юзеру агента только к этому скрипту

## Безопасность отправки (выстрадано)
- `send` требует `--confirm yes` и **строгого** резолва чата (@username/id/полное
  имя; частичное совпадение = отказ — «me» однажды совпало с чужим чатом).
- Каждая отправка с личного аккаунта **автоматически дублируется владельцу ботом**
  («📤 С твоего аккаунта отправлено…») — неотключаемо.
- Фоновому агенту (heartbeat/cron) отправку запретите на уровне правил И
  не давайте ему инструментов с send (см. config/ в корне кита).

## Установка
```bash
pip install telethon fastapi uvicorn
# 1) координаты в /root/telethon.env: TG_API_ID, TG_API_HASH (+прокси при нужде)
# 2) создать сессию: python tg_login.py gateway  (введёте телефон/код)
# 3) юнит: cp tg-gateway.service /etc/systemd/system/ && systemctl enable --now tg-gateway
# 4) проверка: curl 127.0.0.1:8099/health  -> {"ok":true,"connected":true,"authorized":true}
# 5) sudoers: cp sudoers-openclaw-tg.example /etc/sudoers.d/openclaw-tg (поправьте пути)
```
