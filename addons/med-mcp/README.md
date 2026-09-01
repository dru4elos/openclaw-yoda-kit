# med-mcp — медицинский и научный поиск (biomcp, medtools, arxiv)

Это НЕ наш код — три отличных открытых MCP-сервера, которые ставятся через pip.
Ценность этой странички — рабочая конфигурация и грабли, на которые вы иначе
наступите сами. В связке со скиллом `scinews` (Europe PMC + полнотекст) получается
полный научно-медицинский контур агента.

## Что получает агент

| Сервер | Инструментов | Что умеет |
|---|---|---|
| **biomcp** | 36 | PubMed/PubTator (статьи с аннотациями), ClinicalTrials.gov (реальные NCT-номера), генетические варианты |
| **medtools** (ToolUniverse) | 16 из 2604 (!) | FDA-этикетки лекарств: противопоказания, дозировки, взаимодействия, педиатрия; побочки FAERS; RxNorm; DailyMed; MedlinePlus |
| **arxiv** | 14 | поиск/скачивание/чтение препринтов arXiv |

## Установка

### 1. biomcp — PubMed + клинические испытания
```bash
pip install biomcp-python
openclaw mcp add biomcp --command $(which biomcp) --arg run --arg --mode --arg stdio
openclaw mcp probe biomcp   # -> 36 tools
```

### 2. arxiv
```bash
pip install arxiv-mcp-server
openclaw mcp add arxiv --command $(which arxiv-mcp-server)
openclaw mcp probe arxiv    # -> 14 tools
```

### 3. medtools (ToolUniverse) — ⚠️ читайте, тут два капкана

**Капкан №1: ставьте в ОТДЕЛЬНЫЙ venv.** ToolUniverse тянет тяжёлые зависимости
(openai, fastapi, numpy) — не засоряйте ими venv агента:
```bash
python3 -m venv ~/tuvenv
~/tuvenv/bin/pip install tooluniverse==1.4.0
```

**Капкан №2: без фильтра он отдаст агенту 2600 инструментов** и забьёт весь
контекст — `--categories` каталог НЕ ограничивает (проверено: с пятью категориями
всё равно отдаёт 2600 и стартует 10 секунд).

**Лучшее решение — compact mode.** Вместо ручного отбора имён сервер выставляет
5 инструментов-искателей, за которыми доступны все 2599 API:

```bash
openclaw mcp add scitools \
  --command ~/tuvenv/bin/tooluniverse-smcp-stdio --arg --compact-mode
openclaw mcp probe scitools   # -> 5 tools
```

Инструменты: `find_tools` (опиши задачу — вернёт подходящие с параметрами),
`grep_tools`, `get_tool_info`, `execute_tool`, `list_tools`. Агент сначала ищет,
потом вызывает — контекст платится только за нужное. Старт 4.6 с против 10.

Если нужен именно быстрый доступ к этикеткам FDA без промежуточного поиска —
оставьте вторым сервером вариант с include-фильтром:

```bash
openclaw mcp add medtools \
  --command ~/tuvenv/bin/tooluniverse-smcp-stdio \
  --arg --categories --arg fda_drug_label --arg fda_drug_adverse_event \
  --arg rxnorm --arg dailymed --arg medlineplus

openclaw mcp tools medtools --include "DailyMed_parse_adverse_reactions,DailyMed_search_spls,FAERS_search_adverse_event_reports,FDA_get_adverse_reactions_by_drug_name,FDA_get_contraindications_by_drug_name,FDA_get_dosage_info_by_drug_name,FDA_get_drug_interactions_by_drug_name,FDA_get_drug_label,FDA_get_pediatric_use_by_drug_name,FDA_get_warnings_by_drug_name,FDA_search_drug_labels,MedlinePlus_connect_lookup_by_code,MedlinePlus_search_topics,RxNorm_find_rxcui,RxNorm_get_drug_info,RxNorm_get_drug_names"

openclaw mcp probe medtools   # -> 16 tools (не 2604!)
```

Список из 16 — проверенный рабочий минимум для вопросов «что по этикетке FDA,
побочки, взаимодействия, детская дозировка». Расширяйте осознанно.

## Что значит «SMCP Server 4.0.0»
Это НЕ версия ToolUniverse. Так FastMCP печатает баннер «доступно обновление
до 4.0.0» — на момент написания в PyPI максимум `tooluniverse 1.4.1`, а в ответе
на `initialize` сервер сообщает версию FastMCP (`3.4.5`), не свою.
Не гонитесь за 4.0.0: обновление FastMCP до 4.x ломает ToolUniverse 1.4.x.

## Правило гигиены
После добавления ЛЮБОГО MCP-сервера — `openclaw mcp probe`: смотрите, сколько
инструментов он реально выставил. Больше ~40 на сервер — режьте include-фильтром,
иначе платите контекстом каждого хода.

## Что не взлетело (чтобы вы не тратили вечер)
- `semantic-scholar-fastmcp` — поднимает свой HTTP-мост на :8000 и падает, если
  порт занят. Избыточен: Semantic Scholar API уже используется в скилле `scinews`.
- «medical-mcp» от случайных авторов — проверяйте репозитории перед установкой:
  живость коммитов, звёзды, соответствие кода описанию. Мы отсеяли 2 из 12 кандидатов.

## Связка со скиллом scinews
MCP дают быстрый структурированный доступ (метаданные, испытания, этикетки),
а `addons/skills/scinews` — полные тексты (Europe PMC → OpenAlex → Unpaywall →
Semantic Scholar → препринты) и Word-разборы статей. Вместе — полный цикл:
нашёл → прочитал целиком → разобрал письменно.
