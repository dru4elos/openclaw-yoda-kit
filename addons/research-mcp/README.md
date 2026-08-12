# research-mcp — настоящий веб-поиск без ключей

Главная обвязка кита. Без неё агент, которого просят «найди и проанализируй»,
**сочиняет** — красиво и уверенно. С ней — ищет DuckDuckGo'м и читает страницы.

## Инструменты
- `search_and_read` — поиск + чтение верхних страниц (основной для исследований)
- `web_search` / `web_news` — выдача с URL и фрагментами
- `search_multi` — пачка запросов за один вызов (список строк!)
- `read_url` — вытащить текст страницы или PDF
- `research_status` — самопроверка: жив ли поиск

Каждый результат несёт URL; пустая выдача возвращается как явный маркер
`НИЧЕГО_НЕ_НАЙДЕНО` — модель не сможет принять тишину за подтверждение.

## Установка
```bash
pip install ddgs trafilatura pypdf "mcp[cli]" httpx
cp research_mcp.py ~/.openclaw/
openclaw mcp add research --command $(which python3) --arg ~/.openclaw/research_mcp.py \
  --env RUST_LOG=error --env PYTHONWARNINGS=ignore
openclaw mcp probe research   # должно показать 6 tools
```

## Важно
- Логи ddgs идут в stderr — stdout чист, stdio-протокол MCP не ломается.
- DDG банит частые запросы: встроен троттлинг ~1.2с между вызовами.
- Обязательно дополните AGENTS.md протоколом честности (см. config/ в корне кита) —
  инструмент без правил помогает вполсилы.
