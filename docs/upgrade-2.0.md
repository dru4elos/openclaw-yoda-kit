# Обновление на OpenClaw 2.0 (2026.8.1) — как прошло у нас и что ломается

Прошли 05.09.2026 на двух экземплярах (боевой с 13 кронами и гость без интеграций).
Гость обновился за 10 минут. Боевой — за полтора часа, из них час на пять ловушек,
про которые доктор молчит. Ниже — порядок, который работает.

## Порядок

```bash
cp ~/.openclaw/openclaw.json ~/.openclaw/openclaw.json.pre20.$(date +%s)   # снимок
npm install -g openclaw@2026.8.1
openclaw doctor --fix --yes
python3 install/clean_legacy.py ~          # устаревшие ключи, которые доктор не убрал
python3 install/fix_bindings.py ~          # каналы -> агент (без этого канал не стартует)
python3 install/apply20.py ~               # безопасные умолчания 2.0 + таблицы в Telegram
openclaw plugins registry --refresh
openclaw plugins enable <каждый npm-плагин> --accept-capabilities
openclaw doctor --session-sqlite import --session-sqlite-all-agents --yes
openclaw config validate                   # должно быть «Config valid»
systemctl --user restart openclaw-gateway
ss -ltn | grep 18789                       # ГЕЙТВЕЙ ДОЛЖЕН СЛУШАТЬ, «active» — недостаточно
```

## Пять ловушек (все — у нас случились)

**1. `agents.list` → `agents.entries` мигрирует ПУСТЫМ, если доктор оборвался.**
Конфиг невалиден, гейтвей не стартует. `fix_bindings.py` восстанавливает агентов
из снимка. Доктор обрывается на FsSafeError (см. п. 5).

**2. Плагины из npm требуют согласия на capabilities.** Гейтвей пишет
`Plugin "X" requires capability consent` и выходит с кодом 1, а systemd показывает
`active` в петле рестартов. `plugins enable X --accept-capabilities`; если «Plugin
not found» — `plugins install <npm-spec> --accept-capabilities` поверх.
После 10 неудачных стартов срабатывает предохранитель: каналы не автостартуют
ещё 5 минут после первого чистого старта — просто подождите и перезапустите.

**3. Каналу нужен явный владелец.** «Multiple agents are configured, but telegram
account default routing has no explicit owner» — нужны `bindings` и `talk.agentId`.
Доктор пишет их сам, если не оборвался; иначе `fix_bindings.py`.

**4. Legacy-хранилища требуют миграции, и `doctor --fix` её НЕ делает.**
Сессии: `doctor --session-sqlite import --session-sqlite-all-agents --yes`
(режим именно `import`). Состояние workspace (`openclaw-workspace-state.json` и
`workspace-attestations/*.attested`): доктор шаг пропускает молча, каждое входящее
падает за 20 мс с «Legacy workspace setup state requires migration». Импорт
руками — `install/import_ws_state.py` при ОСТАНОВЛЕННОМ гейтвее
(`workspace_key = sha256(путь)`). Пустой `exec-approvals.json` v1 — просто в бэкап.

**5. Симлинки внутри workspace = «path alias escape blocked».** Если у фонового
агента `skills`/`memory` — симлинки в основной workspace, FsSafe 2.0 обрывает
миграции и глушит memory-core для этого workspace. На работу агента после
миграции не влияет, но доктор из-за этого не доделывает шаги 1, 3 и 4.

## Что выключить сразу (делает `apply20.py`)

- `skills.workshop.autonomous.mode: off` — агент не меняет свои скиллы без человека
- `plugins.entries.memory-core.config.dreaming.enabled: false` — фоновые вызовы
  модели без сметы
- Active Memory оставлена, но на дешёвой модели (`plugins.entries.active-memory.
  config.model`); её надо добавить в `plugins.allow`, если allowlist есть
- `agents.defaults.compaction.memoryFlush.model` — тоже дешёвая
- `channels.telegram.richMessages: true` — настоящие таблицы (Bot API 10.x).
  Старые Telethon-клиенты покажут такое сообщение пустым — это не бага бота.

## Проверка после

`openclaw mcp probe` (все серверы), `openclaw cron list` (все задания и их
`--model`), `channels status` (running, connected), таблица в чат
(`operation=sendRichMessage` в журнале), и — обязательно — порт слушает.
