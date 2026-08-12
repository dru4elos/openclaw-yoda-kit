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

**Капкан №2: без фильтра он отдаст агенту 2604 инструмента** и забьёт весь
контекст — `--categories` каталог НЕ ограничивает. Обязателен include-фильтр:

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
