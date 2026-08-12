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
