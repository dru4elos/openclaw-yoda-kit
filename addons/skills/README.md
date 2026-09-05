# skills — набор скиллов для workspace

Кладутся в `~/.openclaw/workspace/skills/<имя>/`. У каждого — SKILL.md (агент читает
его сам) и скрипт. Зависимости ставьте в отдельный venv (в примерах — `~/mailvenv`).

| скилл | что делает | зависимости |
|---|---|---|
| mail | 3 ящика: Яндекс/Gmail (IMAP+SMTP, подписи), Exchange через хелпер; отправка только с --confirm + автокопия владельцу | stdlib |
| calendar | iCloud CalDAV: today/list/search/add/del | caldav, icalendar |
| images | поиск картинок (DDG+Commons+Openverse), парсинг страниц, Word с картинками; **фильтр зрением** (безопасность+релевантность) и отсев стоков с водяными знаками | ddgs, requests, bs4, python-docx, PIL |
| docs | read/check/preview документов: текст + классификация каждой картинки + визуальный рендер страниц | python-docx, pypdf, libreoffice |
| scinews | Europe PMC поиск + полный Word-разбор статьи + добыча полнотекста (OpenAlex/Unpaywall/S2/препринты) | requests, python-docx |
| weather | Open-Meteo без ключей, с геокодером | requests |
| say | TTS через piper (локально, бесплатно) → голосовое в Telegram | piper, ffmpeg |
| tome | отправка владельцу файлов/фото/сообщений от бота | requests |
| telegram | обёртка над tg-gateway (см. addons/tg-gateway) | — |
| whatsapp | отправка через канал OpenClaw + правила подотчётности | плагин whatsapp |
| events | пример интеграции внешнего афиша-скрипта через sudo-хелпер | — |

После копирования пройдитесь по файлам: `123456789` → ваш Telegram ID,
`you@example.com` → ваш email, пути venv — под ваши.

## webinar — эфир под ключ
`plan` одним вызовом ставит кроны: регистрация → вход и запись (webrec, профиль `rec`) → контроль звука → обработка командным кроном без агента: GigaAM v3 по кускам (контейнер из addons/med-mcp… см. addons/webrec/README) → чистка огрехов ASR (gemini-3.8-flash) → подробный конспект (gpt-6-astra-1m) → файлы владельцу через Bot API. Нужны в `.env`: `OWNER_TG_ID`, `GIGAAM_ASR_URL`, `EXCASH_API_URL/KEY`, по желанию `WEBINAR_READER` (кто читатель конспекта). `selftest` гоняет весь контур на куске прошлой записи и сам отбраковывает немые куски и тон вместо речи.

## say — голос (piper, локально)
Русский `ru_RU-dmitri-medium` и итальянский `it_IT-paola-medium` в `/opt/piper-voices` (скачать с huggingface.co/rhasspy/piper-voices; с сервера HF может не отдавать — качайте на рабочей машине и scp). `pip install piper-tts==1.6.0` в venv бота. `--lang ru|it|auto`, резерв edge-tts. Кому слать — `OWNER_TG_ID` в `.env`. Встроенный `tts` OpenClaw при этом лучше запретить в `tools.deny`.
