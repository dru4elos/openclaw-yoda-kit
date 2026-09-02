# webrec — запись онлайн-эфиров со звуком с headless-сервера

Позволяет агенту записать вебинар, конференцию или стрим **целиком, с видео и
звуком**, даже если площадка вещает через WebRTC и ссылки на поток нет
(Pruffme, Zoom Web, Webinar.ru, YouTube Live). Проверено на боевом эфире.

Идея: браузер агента рисует не в headless-буфер, а в виртуальный экран Xvfb и
играет звук в виртуальную карту PulseAudio (null-sink). ffmpeg пишет экран и
монитор этой карты. Агент управляет эфиром своим обычным инструментом `browser`.

## Установка (Ubuntu, от root)

```bash
apt-get install -y pulseaudio pulseaudio-utils xvfb ffmpeg
# полный Chrome for Testing той же версии, что headless-shell у OpenClaw
V=143.0.7499.4; mkdir -p /opt/chrome-full && cd /opt/chrome-full && \
  curl -sLo c.zip https://storage.googleapis.com/chrome-for-testing-public/$V/linux64/chrome-linux64.zip && unzip -q c.zip && rm c.zip
```

От пользователя агента (`openclaw`):
```bash
install -m644 xvfb-webrec.service pulse-webrec.service ~/.config/systemd/user/
install -m644 webrec.pa ~/.config/pulse/webrec.pa
install -m755 chrome-rec ~/bin/chrome-rec
mkdir -p ~/.openclaw/workspace/skills/webrec && cp webrec.py webrec_test.html SKILL.md ~/.openclaw/workspace/skills/webrec/
systemctl --user daemon-reload && systemctl --user enable --now xvfb-webrec pulse-webrec
python3 ~/.openclaw/workspace/skills/webrec/webrec.py selftest   # ожидаем ИТОГ: OK
```

Подключение к OpenClaw — `openclaw.json`:
```json5
browser: { enabled: true, headless: false, noSandbox: true,
           executablePath: "/home/openclaw/bin/chrome-rec" }
```
и в `~/.openclaw/gateway.systemd.env` (иначе плагин откажется поднимать headful-браузер):
```
DISPLAY=:99
XDG_RUNTIME_DIR=/run/user/<uid>
PULSE_SERVER=unix:/run/user/<uid>/pulse/native
PULSE_SINK=webrec
```
Затем `systemctl --user restart openclaw-gateway`.

## Грабли, которые мы собрали за один вечер

- **Плагин браузера проверяет `$DISPLAY` в окружении гейтвея**, а не обёртки:
  без переменной в systemd env он говорит «no Linux display server detected» и
  Chrome не запускает вовсе. Обёртка с `export DISPLAY` этого не лечит.
- **Обёртка должна вырезать `--headless`**: плагин может добавить флаг, и
  тогда Chrome не рисует в Xvfb — запись чёрная.
- **`file://` и локальные хосты заблокированы политикой навигации плагина**
  (`allowPrivateNetwork`). Проверяйте запись публичным https-звуком, а не
  локальной страницей; селфтест запускает свой Chrome в обход плагина.
- **Записывается весь экран**, не вкладка: вкладка эфира должна быть активной и
  единственной. `mean_volume < −55 dB` в `webrec check` = тишина — это брак.
- `systemctl --user` от `sudo -u` не видит шину: нужны `XDG_RUNTIME_DIR` и
  `DBUS_SESSION_BUS_ADDRESS`, либо `su - user`.
- Сегменты по 10 минут: упавший ffmpeg теряет кусок, а не эфир. `stop` склеивает
  и меряет громкость сам.
- Нагрузка: 720p @ 15 fps, x264 veryfast — около одного ядра, ~150–250 МБ/час.
