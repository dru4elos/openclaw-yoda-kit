# -*- coding: utf-8 -*-
"""Вычитка русского языка научного брифа.

Два слоя, намеренно разделённые:
  1) детерминированный — только то, что нельзя испортить: буквы-двойники, типографика,
     десятичная запятая. Ни одного правила, требующего согласования по падежу.
  2) модель — грамматика, падежи и медицинская терминология по глоссарию. Любая правка,
     в которой изменился хоть один числовой факт, ОТВЕРГАЕТСЯ и остаётся исходный текст.
Плюс библиографическая ссылка (Vancouver) для шапки карточки."""
import json, re, unicodedata

# ---------- 1. Детерминированный слой ----------
HOMO_LAT = {"a": "а", "c": "с", "e": "е", "o": "о", "p": "р", "x": "х", "y": "у", "A": "А", "B": "В",
            "C": "С", "E": "Е", "H": "Н", "K": "К", "M": "М", "O": "О", "P": "Р", "T": "Т", "X": "Х"}
HOMO_UA = {"і": "и", "І": "И", "ї": "и", "Ї": "И", "є": "е", "Є": "Е", "ґ": "г", "Ґ": "Г"}
CYR = re.compile(r"[а-яёА-ЯЁ]")

def fix_homoglyphs(t):
    """Латинская o в «мнoгоцентровое» и украинская i в «полiтравма» — невидимы глазом,
    ломают поиск и проверку орфографии. Правим ТОЛЬКО когда все латинские буквы слова есть
    в таблице двойников: в «backover» есть b/k/v — значит это настоящее английское слово."""
    def fix_token(m):
        w = "".join(HOMO_UA.get(ch, ch) for ch in m.group(0))
        lat = [ch for ch in w if ch.isascii() and ch.isalpha()]
        if lat and CYR.search(w) and all(ch in HOMO_LAT for ch in lat):
            w = "".join(HOMO_LAT.get(ch, ch) for ch in w)
        return w
    return re.sub(r"[A-Za-zА-Яа-яЁёІіЇїЄєҐґ]+", fix_token, t)

SAFE = [                                   # замены, не требующие согласования
    (r"полиполитравм", "политравм"),
    (r"\bпреф-пост\b", "«до и после»"),
    (r"\bpre-post\b", "«до и после»"),
    (r"\bпре-пост\b", "«до и после»"),
]

def fix_typography(t):
    t = re.sub(r"(?<=\d)\.(?=\d)", ",", t)                        # 11.6 → 11,6
    t = re.sub(r"(?<=\d)\s*[-‑]\s*(?=\d)", "–", t)                # 4-150 → 4–150
    t = re.sub(r"(?<=[а-яё])\s+-\s+(?=[а-яёА-ЯЁ])", " — ", t)     # дефис между словами → тире
    t = re.sub(r'"([^"\n]{1,160})"', r"«\1»", t)
    t = re.sub(r"\s*±\s*", "±", t)
    t = re.sub(r"\s+([,.;:])", r"\1", t)
    t = re.sub(r"[ \t]{2,}", " ", t)
    return t.strip()

def clean(t):
    if not isinstance(t, str) or not t.strip():
        return t
    t = fix_homoglyphs(unicodedata.normalize("NFC", t))
    for pat, rep in SAFE:
        t = re.sub(pat, rep, t, flags=re.I)
    return fix_typography(t)

# ---------- 2. Слой модели ----------
GLOSSARY = """АНАТОМИЯ И КЛИНИКА — русский термин основной, международный в скобках:
  medial epicondyle / медиальный эпикондил → внутренний надмыщелок (медиальный)
  lateral epicondyle / латеральный эпикондил → наружный надмыщелок (латеральный)
  condyle / кондил → мыщелок; supracondylar → надмыщелковый
  ossific nucleus / «ядерный центр эпифиза» → ядро окостенения
  distal radius / дистальный радиус → дистальный отдел лучевой кости
  tibia → большеберцовая кость; femur → бедренная кость; humerus → плечевая кость
  reducible / редуцируемый → вправимый; irreducible → невправимый
  bracing / «браслетная терапия» → лечение отводящим ортезом (брейсом)
  alpha angle / «угол Альфа» → угол α по Графу
  acetabular index (AI) → ацетабулярный индекс (в статьях про ИИ AI НЕ трогать — там это искусственный интеллект)
  Barlow-positive → с положительной пробой Барлоу
ДИЗАЙН ИССЛЕДОВАНИЯ:
  single-arm / «одноканальное» → одногрупповое
  multicentre / «многопрофильная когорта» → многоцентровое
  single-center / «однокомплексный», «однократный когортный» → одноцентровое
  pre-post → исследование «до и после»; crude → грубый (нестандартизованный)
  follow-up → срок наблюдения; baseline → исходный уровень; outcome → исход
  incidence → частота (заболеваемость); backover → наезд при движении задним ходом
СТАТИСТИКА — при ПЕРВОМ появлении расшифровать по-русски, дальше сокращённо:
  RR → ОР (относительный риск); OR → ОШ (отношение шансов); IRR → ОЧ (отношение частот)
  95% CI → 95% ДИ (доверительный интервал); IQR → МКИ (межквартильный интервал)
  HR → отношение рисков (hazard ratio); ISS, AIS, GCS — оставить, расшифровав один раз
  p-значение → значение p"""

PROMPT = """Ты — научный редактор русского медицинского текста. Перед тобой поля выжимки статьи
для брифа детского травматолога-ортопеда; текст сделан машинным переводом и местами кривой.

ЗАДАЧА: вычитать русский язык, НИЧЕГО не добавляя, не выбрасывая и не переосмысливая.

ЖЁСТКИЕ ЗАПРЕТЫ
1. Все числа, проценты, единицы, доверительные интервалы, значения p, дозы, возраста, размеры
   выборок — переносить символ в символ. Ни одной новой цифры, ни одной потерянной.
2. Не добавлять выводов, оценок и фактов, которых нет в исходном тексте.
3. Где написано «не указано», «не приведено» — так и оставить.
4. Не менять смысл на противоположный и не усиливать формулировки («может» не превращать в «доказано»).

ЧТО ИСПРАВЛЯТЬ
• Падежи, согласование, порядок слов: «интенсивность backover-травм» → «частота травм при наезде
  задним ходом»; «44 бедер» → «44 бедра»; «с динамической УЗ-контролем» → «под динамическим УЗ-контролем».
• Непереведённые английские куски и кальки — по глоссарию ниже.
• Канцелярит и тавтологию убрать, оставив факт: «Интенсивность incidence снизилась» → «Частота снизилась».
• Согласование существительного с числительным — по ПОСЛЕДНЕЙ цифре: 1, 21, 91 день; 2–4, 22, 134 дня;
  5–20, 11–14, 25 дней. То же для «бедро/бедра/бедер», «пациент/пациента/пациентов». Если в исходнике
  форма уже правильная — НЕ ТРОГАТЬ (это частая ошибка: «91±21 день» менять на «дней» нельзя).
• Термины — как говорит и пишет русский врач в клиническом разборе.

%s

Верни СТРОГО JSON с ТЕМИ ЖЕ ключами и той же структурой, что на входе (списки остаются списками
той же длины). Без пояснений и без markdown-ограды.

ВХОД:
%s"""

def make_llm(ask_json):
    """ask_json(prompt) -> dict. Оборачиваем, чтобы модуль не зависел от транспорта."""
    def llm(payload):
        try:
            return ask_json(PROMPT % (GLOSSARY, json.dumps(payload, ensure_ascii=False, indent=1)))
        except Exception as e:
            print(f"(вычитка: модель не ответила — {type(e).__name__} {str(e)[:70]})")
            return None
    return llm

# ---------- 3. Защита фактов ----------
def numbers(t):
    return sorted(re.findall(r"\d+(?:[.,]\d+)?", str(t).replace(",", ".")))

def guard(orig, new):
    """True — правку принимаем: цифры совпали и объём не поплыл."""
    if not isinstance(new, str) or not new.strip():
        return False
    if numbers(orig) != numbers(new):
        return False
    return len(orig) * 0.6 <= len(new) <= len(orig) * 1.8

# ---------- 4. Библиографическая ссылка ----------
def cite(a):
    au = [x.strip().rstrip(".") for x in str(a.get("authors") or "").split(",") if x.strip()]
    authors = ", ".join(au[:3]) + ", et al" if len(au) > 6 else ", ".join(au)
    j = re.sub(r"\s*:\s*.*$", "", (a.get("journal_abbr") or a.get("journal") or "").strip().rstrip("."))
    title = (a.get("en") or a.get("ru") or "").strip().rstrip(".")
    head = " ".join(x for x in [authors + "." if authors else "", title + "." if title else "",
                                j + "." if j else "", f"{a['year']}." if a.get("year") else ""] if x)
    tail = [x for x in [f"doi:{a['doi']}" if a.get("doi") else "",
                        f"PMID: {a['pmid']}" if a.get("pmid") else "",
                        a.get("pmcid") or ""] if x]
    return (head + (" " + ". ".join(tail) + "." if tail else "")).strip()

# ---------- 5. Проход по статье и по брифу ----------
FIELDS = ("ru", "why", "desc")
SUM_FIELDS = ("tldr", "design", "n", "meaning", "evidence", "caveat")

def pass_article(a, llm=None):
    rep = {"det": 0, "llm": 0, "reject": 0}
    for f in FIELDS:
        if isinstance(a.get(f), str):
            new = clean(a[f])
            rep["det"] += new != a[f]
            a[f] = new
    s = a.get("summary") if isinstance(a.get("summary"), dict) else None
    if s:
        for f in SUM_FIELDS:
            if isinstance(s.get(f), str):
                new = clean(s[f]); rep["det"] += new != s[f]; s[f] = new
        if isinstance(s.get("findings"), list):
            old = list(s["findings"]); s["findings"] = [clean(str(x)) for x in old]
            rep["det"] += sum(1 for o, n in zip(old, s["findings"]) if o != n)
    a.setdefault("cite", cite(a))   # библиографию строит biblio, здесь запасной вариант
    if not llm:
        return rep
    payload = {k: a[k] for k in ("ru", "why") if a.get(k)}
    if s:
        payload.update({f: s[f] for f in SUM_FIELDS if s.get(f)})
        if s.get("findings"):
            payload["findings"] = s["findings"]
    fixed = llm(payload)
    if not isinstance(fixed, dict):
        return rep
    for k, v in fixed.items():
        if k == "findings" and s and isinstance(v, list) and len(v) == len(s.get("findings") or []):
            ok = [guard(o, str(n)) for o, n in zip(s["findings"], v)]
            s["findings"] = [str(n) if good else o for o, n, good in zip(s["findings"], v, ok)]
            rep["llm"] += sum(ok); rep["reject"] += len(ok) - sum(ok)
        elif k in ("ru", "why"):
            if guard(a.get(k, ""), v): a[k] = v; rep["llm"] += 1
            else: rep["reject"] += 1
        elif s and k in SUM_FIELDS:
            if guard(s.get(k, ""), v): s[k] = v; rep["llm"] += 1
            else: rep["reject"] += 1
    return rep

def pass_brief(brief, llm=None, log=print):
    seen, total = {}, {"det": 0, "llm": 0, "reject": 0, "articles": 0}
    for key in ("top", "fresh", "mail_articles"):
        for a in brief.get(key) or []:
            aid = a.get("id")
            if aid in seen:                      # одна статья попадает и в top, и в fresh
                a.update(seen[aid]); continue
            r = pass_article(a, llm if (a.get("summary") or a.get("rating") != "none") else None)
            seen[aid] = a
            for k in ("det", "llm", "reject"):
                total[k] += r[k]
            total["articles"] += 1
    log(f"вычитка: статей {total['articles']}, детерминированных правок {total['det']}, "
        f"правок модели принято {total['llm']}, отвергнуто по цифрам {total['reject']}")
    brief["lang_pass"] = total
    return brief
