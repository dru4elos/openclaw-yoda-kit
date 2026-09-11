# ops — эксплуатация: смоук, секреты, бэкап

## yoda-smoke.sh — смоук-тест всего контура
Проверяет: gateway агента, tg-шлюз, каждый MCP по имени, живость веб-поиска,
ваши systemd-сервисы, диск, валидность конфига, привязку кронов к фоновому агенту —
и делает git-снапшот workspace. Любой провал → алерт в Telegram ботом.

```bash
# отредактируйте шапку: MCP_LIST, SVC_LIST, OWNER_TG_ID
cp yoda-smoke.sh /usr/local/bin/ && chmod +x /usr/local/bin/yoda-smoke.sh
/usr/local/bin/yoda-smoke.sh          # первый прогон руками
# ночной: crontab -e ->  15 3 * * * /usr/local/bin/yoda-smoke.sh >> /var/log/yoda-smoke.log 2>&1
```
Правило: гонять после КАЖДОГО изменения конфига/скиллов. Ломается всегда не там, где менял.

## sync_yoda_keys.sh — один источник секретов
Если ключи LLM живут в каноническом env-файле другого проекта — этот скрипт
синкает в `~/.openclaw/.env` ТОЛЬКО белый список переменных (изоляция агента
от остальных секретов сохраняется). Ночной cron.

## backup-mac/ — бэкап агента на ваш Mac
`yoda_backup.sh` тянет rsync'ом конфиг, workspace (память!), скрипты и сессию
tg-шлюза; хранит датированные hardlink-снапшоты с ротацией. `*.plist` — launchd
на каждый день (поправьте пути /Users/YOURNAME и ssh-алиас сервера).

## yoda-models и excash_watch — когда провайдер отвергает ключ
`yoda-models excash|deepseek` переключает модели обоих экземпляров (основная/резервы, subagents, compaction, memoryFlush, active-memory, `thinkingDefault`) и пины кронов, валидирует и перезапускает гейтвеи (~1 мин, обратимо). Профиль `deepseek` = вся Йода на DeepSeek V4.1 Flash (id `deepseek-flash`: 1M контекста, 384k вывода, картинки, ~240 ток/с без рассуждений; кэш префикса делает повторную историю почти бесплатной) — режим экономии или запасной, когда агрегатор лежит. Рассуждения в этом профиле выключены (`thinkingDefault: off` + `compat.thinkingFormat: deepseek` в записи модели, иначе OpenClaw не шлёт `thinking:{type:disabled}` и модель по умолчанию думает 5–11 тыс. токенов на простую реплику). В скриптах (tg-gateway, scinews, webinar, vision, дайджест) DeepSeek стоит последним резервом всегда с `thinking: disabled`. Урок 07.09: агрегатор ответил 401 на ключ, OpenClaw и остальные сервисы продолжали пробовать → провайдер забанил IP (429 auth_fail_throttled) даже для валидных запросов; пока провайдер лежит, в цепочке не должно быть ни одной его модели. `excash_watch.sh` (root cron */30) сообщает владельцу через tome, когда `/models` снова 200.
Ещё урок: SKILL.md без frontmatter (`name`, `description`) гейтвей 2.0 молча пропускает («Skipping invalid skill») — скилл невидим агенту.
