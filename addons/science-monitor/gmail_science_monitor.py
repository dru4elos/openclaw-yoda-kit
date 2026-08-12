#!/usr/bin/env python3
"""
Gmail Science Monitor
Мониторит Gmail на письма с научными алертами → анализирует через excash gemini-3.1-pro (резерв DeepSeek) → шлёт в Telegram
"""

import imaplib
import email
import email.header
import re
import time
import logging
import requests
import json
import os
import sqlite3
import html
import secrets
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Optional, List, Dict, Tuple
from urllib.parse import quote

# ============================================================
# КОНФИГУРАЦИЯ
# ============================================================
GMAIL_USER = os.getenv("SCIENCE_GMAIL_USER", "")
GMAIL_APP_PASSWORD = os.getenv("SCIENCE_GMAIL_PASSWORD", "")

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
MODEL = "google/gemini-3.1-flash-lite-preview"


def _keys_env(name, default=""):
    try:
        for _l in open("/home/knee_bot/keys.env", encoding="utf-8"):
            _l = _l.strip()
            if _l.startswith(name + "="):
                return _l.split("=", 1)[1].strip().strip(chr(34)).strip(chr(39))
    except Exception:
        pass
    return os.getenv(name, default)


LAST_ENGINE = "?"  # какой движок реально сделал последний анализ (для честного футера)


def _ds_flash_call(messages, max_tokens=4000, temperature=0.3):
    """DeepSeek V4 Flash — быстрый анализ статей (просьба доктора 08.08)."""
    global LAST_ENGINE
    import requests as _rq
    dk = _keys_env("DEEPSEEK_API_KEY")
    if not dk:
        return ""
    try:
        r = _rq.post("https://api.deepseek.com/v1/chat/completions",
            headers={"Authorization": "Bearer " + dk, "Content-Type": "application/json"},
            json={"model": "deepseek-v4-flash", "messages": messages,
                  "max_tokens": max_tokens, "temperature": temperature}, timeout=180)
        r.raise_for_status()
        txt = ((r.json().get("choices") or [{}])[0].get("message", {}) or {}).get("content", "")
        if (txt or "").strip():
            LAST_ENGINE = "DeepSeek V4 Flash"
            return txt.strip()
    except Exception as e:
        log.warning(f"deepseek-flash fail -> excash: {e}")
    return ""


def _excash_call(messages, max_tokens=4000, temperature=0.3):
    """excash gemini-3.1-pro (основной) -> DeepSeek V4 Pro (резерв). OpenRouter с этого IP = 403."""
    import requests as _rq
    k, u = _keys_env("EXCASH_API_KEY"), _keys_env("EXCASH_API_URL")
    if k and u:
        try:
            r = _rq.post(u.rstrip("/") + "/chat/completions",
                headers={"Authorization": "Bearer " + k, "Content-Type": "application/json"},
                json={"model": "gemini-3.1-pro", "messages": messages,
                      "max_tokens": max_tokens, "temperature": temperature}, timeout=300)
            r.raise_for_status()
            txt = ((r.json().get("choices") or [{}])[0].get("message", {}) or {}).get("content", "")
            if (txt or "").strip():
                global LAST_ENGINE
                LAST_ENGINE = "Gemini 3.1 Pro (excash)"
                return txt.strip()
        except Exception as e:
            log.warning(f"excash fail -> DeepSeek: {e}")
    dk = _keys_env("DEEPSEEK_API_KEY")
    if dk:
        try:
            r = _rq.post("https://api.deepseek.com/v1/chat/completions",
                headers={"Authorization": "Bearer " + dk, "Content-Type": "application/json"},
                json={"model": "deepseek-v4-pro", "messages": messages,
                      "max_tokens": max(max_tokens, 8000), "temperature": temperature}, timeout=600)
            r.raise_for_status()
            txt = ((r.json().get("choices") or [{}])[0].get("message", {}) or {}).get("content", "")
            return (txt or "").strip()
        except Exception as e:
            log.error(f"DeepSeek fail: {e}")
    return ""

def _yoda_token():
    """Токен бота Йоды — научные алерты идут туда, а не в DATA_bot."""
    try:
        cfg = open("/home/openclaw/.openclaw/openclaw.json", encoding="utf-8").read()
        m = re.search(r'"botToken"\s*:\s*"([0-9]+:[A-Za-z0-9_-]+)"', cfg)
        if m:
            return m.group(1)
    except Exception:
        pass
    return ""


TELEGRAM_TOKEN = (os.getenv("SCIMON_BOT_TOKEN") or _yoda_token()
                  or os.getenv("DATABOT_TOKEN", ""))
TELEGRAM_CHAT_ID = os.getenv("ADMIN_ID", "123456789")

CHECK_INTERVAL_SEC = 300   # проверять каждые 5 минут
NEW_EMAIL_DAYS = 5         # «свежие» письма — не старше N дней
DB_PATH = "/home/knee_bot/data/science_monitor.db"

# Темы интересов доктора
RESEARCH_INTERESTS = """
1. Молекулярные и клеточные механизмы в ортопедии и травматологии
2. Детская травматология и ортопедия (педиатрия)
3. Сколиозы — консервативное и хирургическое лечение
4. Артроскопические методы лечения суставов
5. Ортобиология: PRP, стволовые клетки, факторы роста, биостимуляторы, новые биопрепараты
"""

# Известные отправители журнальных алертов
KNOWN_JOURNAL_SENDERS = {
    "efts@ncbi.nlm.nih.gov": "PubMed",
    "ncbi@ncbi.nlm.nih.gov": "PubMed",
    "alerts@springer.com": "Springer",
    "springer@springernature.com": "SpringerNature",
    "nature@nature.com": "Nature",
    "alerts@nature.com": "Nature",
    "elsevier@elsevier.com": "Elsevier",
    "sciencedirect@elsevier.com": "ScienceDirect",
    "noreply@cochranelibrary.com": "Cochrane",
    "jbjs@jbjs.org": "JBJS",
    "boneandjoint@boneandjoint.org.uk": "Bone&Joint",
    "arthroscopy@arthroscopyjournal.org": "Arthroscopy",
    "spine@lww.com": "Spine",
    "jorthopaedics@elsevier.com": "J.Orthopaedics",
    "journalofpediatricorthopedics@lww.com": "J.Pediatric Ortho",
}

# Ключевые слова в теме письма — дополнительный фильтр
SUBJECT_ALERT_KEYWORDS = [
    "pubmed", "new articles", "new publication", "table of contents",
    "toc alert", "citation alert", "search alert", "article alert",
    "journal alert", "new issue", "weekly update", "latest articles",
    "research alert",
]

# ============================================================
# ЛОГИРОВАНИЕ
# ============================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("/home/knee_bot/science_monitor.log"),
        logging.StreamHandler(),
    ]
)
log = logging.getLogger(__name__)


# ============================================================
# БАЗА ДАННЫХ — трекинг обработанных писем
# ============================================================
def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS processed_emails (
            message_id TEXT PRIMARY KEY,
            processed_at TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS article_links (
            id TEXT PRIMARY KEY,
            url TEXT NOT NULL,
            doi TEXT,
            title TEXT,
            email_subject TEXT,
            stored_at TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()


# ============================================================
# ИЗВЛЕЧЕНИЕ ССЫЛОК ИЗ ПИСЬМА
# ============================================================
def search_pubmed_by_title(title: str) -> Optional[Dict]:
    """Ищет статью в PubMed по точному названию. Возвращает {doi, pmid, url} или None."""
    try:
        r = requests.get(
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi",
            params={"db": "pubmed", "term": f"{title}[Title]", "retmode": "json",
                    "tool": "science_monitor", "email": GMAIL_USER},
            timeout=15
        )
        if not r.ok:
            return None
        ids = r.json().get("esearchresult", {}).get("idlist", [])
        if not ids:
            # точное совпадение названия не нашлось (обрезки/опечатки в алертах) —
            # пробуем свободный поиск и берём верхний результат
            r_free = requests.get(
                "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi",
                params={"db": "pubmed", "term": title, "retmode": "json", "retmax": "1",
                        "tool": "science_monitor", "email": GMAIL_USER},
                timeout=15
            )
            if r_free.ok:
                ids = r_free.json().get("esearchresult", {}).get("idlist", [])
        if not ids:
            return None
        pmid = ids[0]
        # Получаем DOI через esummary
        r2 = requests.get(
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi",
            params={"db": "pubmed", "id": pmid, "retmode": "json",
                    "tool": "science_monitor", "email": GMAIL_USER},
            timeout=15
        )
        doi = None
        if r2.ok:
            result = r2.json().get("result", {}).get(pmid, {})
            articleids = result.get("articleids", [])
            for aid in articleids:
                if aid.get("idtype") == "doi":
                    doi = aid["value"]
                    break
        url = f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"
        if doi:
            url = f"https://doi.org/{doi}"
        return {"pmid": pmid, "doi": doi, "url": url}
    except Exception as e:
        log.warning(f"PubMed поиск ошибка: {e}")
        return None


def resolve_article_links(analysis: str, subject: str) -> List[Dict]:
    """Извлекает английские названия статей из ответа AI (строка 🔬eng:),
    ищет каждое в PubMed, сохраняет в БД с коротким ID."""
    eng_titles = re.findall(r'🔬eng:\s*(.+)', analysis)
    if not eng_titles:
        return []

    log.info(f"AI вернул {len(eng_titles)} англ. названий, ищем в PubMed...")

    conn = sqlite3.connect(DB_PATH)
    result = []
    now = datetime.now().isoformat()

    for title in eng_titles[:15]:
        title = title.strip().strip("[]")
        pm = search_pubmed_by_title(title)
        url = pm["url"] if pm else ""
        doi = pm["doi"] if pm else None

        short_id = secrets.token_hex(4)
        conn.execute(
            "INSERT OR IGNORE INTO article_links (id, url, doi, title, email_subject, stored_at) VALUES (?,?,?,?,?,?)",
            (short_id, url, doi, title[:300], subject[:200], now)
        )
        result.append({"id": short_id, "url": url, "doi": doi, "title": title})
        time.sleep(0.4)  # PubMed rate limit

    conn.commit()
    conn.close()
    log.info(f"Найдено в PubMed: {sum(1 for r in result if r['url'])} из {len(result)}")
    return result


def is_processed(message_id: str) -> bool:
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute(
        "SELECT 1 FROM processed_emails WHERE message_id = ?", (message_id,)
    ).fetchone()
    conn.close()
    return row is not None


def mark_processed(message_id: str):
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT OR IGNORE INTO processed_emails (message_id, processed_at) VALUES (?, ?)",
        (message_id, datetime.now().isoformat())
    )
    conn.commit()
    conn.close()


# ============================================================
# GMAIL IMAP
# ============================================================
def connect_gmail() -> imaplib.IMAP4_SSL:
    mail = imaplib.IMAP4_SSL("imap.gmail.com")
    mail.login(GMAIL_USER, GMAIL_APP_PASSWORD)
    return mail


def decode_header_value(value: str) -> str:
    parts = email.header.decode_header(value)
    decoded = []
    for part, charset in parts:
        if isinstance(part, bytes):
            decoded.append(part.decode(charset or "utf-8", errors="replace"))
        else:
            decoded.append(part)
    return " ".join(decoded)


def get_email_body(msg) -> str:
    """Извлекает текстовое содержимое письма (text/plain или text/html)."""
    body_plain = ""
    body_html = ""

    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            cd = str(part.get("Content-Disposition", ""))
            if "attachment" in cd:
                continue
            charset = part.get_content_charset() or "utf-8"
            try:
                payload = part.get_payload(decode=True).decode(charset, errors="replace")
            except Exception:
                continue
            if ct == "text/plain" and not body_plain:
                body_plain = payload
            elif ct == "text/html" and not body_html:
                body_html = payload
    else:
        charset = msg.get_content_charset() or "utf-8"
        try:
            raw = msg.get_payload(decode=True).decode(charset, errors="replace")
        except Exception:
            raw = ""
        if msg.get_content_type() == "text/html":
            body_html = raw
        else:
            body_plain = raw

    if body_plain:
        return body_plain
    # HTML → plain text
    if body_html:
        text = re.sub(r"<br\s*/?>", "\n", body_html, flags=re.IGNORECASE)
        text = re.sub(r"<p[^>]*>", "\n", text, flags=re.IGNORECASE)
        text = re.sub(r"<[^>]+>", "", text)
        text = html.unescape(text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()
    return ""


def is_journal_alert(sender: str, subject: str) -> Tuple[bool, str]:
    """Проверяет — это журнальный алерт или нет. Возвращает (True, название_источника)."""
    sender_lower = sender.lower()
    for known_addr, name in KNOWN_JOURNAL_SENDERS.items():
        if known_addr in sender_lower:
            return True, name

    # Дополнительно по домену
    journal_domains = [
        "ncbi.nlm.nih.gov", "springer.com", "springernature.com",
        "nature.com", "elsevier.com", "sciencedirect.com",
        "cochranelibrary.com", "jbjs.org", "boneandjoint.org.uk",
        "lww.com", "wiley.com", "tandfonline.com", "pubmed",
        "oup.com", "sagepub.com", "bmj.com", "thelancet.com",
        "jamanetwork.com", "nejm.org",
        # издательские платформы рассылок
        "bioscientifica.com", "emails-bioscientifica.com",
        "mailing.thieme.de", "thieme.de",
        "emeraldinsight.com", "emerald.com",
        "journals.sagepub.com", "karger.com",
        "ingentaconnect.com", "informahealthcare.com",
        "wolterskluwer.com", "ovid.com",
        "humankinetics.com", "seabs.ac.uk",
        "rsmjournals.com", "rcpjournals.org",
        "efortopen.org", "efort.org",
        "journals.plos.org", "plos.org",
        "frontiersin.org", "mdpi.com",
        "jrheum.org", "acr.org", "aos.org.au",
    ]
    for domain in journal_domains:
        if domain in sender_lower:
            return True, domain.split(".")[0].capitalize()

    # По теме
    subj_lower = subject.lower()
    for kw in SUBJECT_ALERT_KEYWORDS:
        if kw in subj_lower:
            return True, "Journal Alert"

    return False, ""


def _imap_since_date(days_ago: int) -> str:
    """Возвращает дату в формате IMAP SINCE: DD-Mon-YYYY."""
    d = (datetime.now() - timedelta(days=days_ago))
    return d.strftime("%d-%b-%Y")


def _parse_email_num(mail: imaplib.IMAP4_SSL, num: bytes) -> Optional[Dict]:
    """Читает одно письмо по IMAP-номеру, возвращает dict или None если не алерт."""
    try:
        _, msg_data = mail.fetch(num, "(RFC822)")
        raw = msg_data[0][1]
        msg = email.message_from_bytes(raw)

        message_id = msg.get("Message-ID", "").strip()
        if not message_id:
            return None
        if is_processed(message_id):
            return None

        sender = decode_header_value(msg.get("From", ""))
        subject = decode_header_value(msg.get("Subject", ""))
        date_str = msg.get("Date", "")

        is_alert, source = is_journal_alert(sender, subject)
        if not is_alert:
            return None

        body = get_email_body(msg)
        return {
            "imap_num": num,          # нужен для пометки прочитанным
            "message_id": message_id,
            "sender": sender,
            "subject": subject,
            "date": date_str,
            "source": source,
            "body": body[:8000],
            "is_old": False,          # флаг «архивное»
        }
    except Exception as e:
        log.error(f"Ошибка чтения письма {num}: {e}")
        return None


def fetch_recent_alert_emails(mail: imaplib.IMAP4_SSL) -> List[Dict]:
    """Возвращает свежие (≤ NEW_EMAIL_DAYS дней) необработанные алерты.
    Ищет ВСЕ письма (не только непрочитанные) — фильтр по БД processed_emails."""
    mail.select("INBOX")
    since = _imap_since_date(NEW_EMAIL_DAYS)
    _, data = mail.search(None, f"SINCE {since}")
    if not data or not data[0]:
        return []

    ids = data[0].split()
    log.info(f"Всего писем за {NEW_EMAIL_DAYS} дн.: {len(ids)}")

    results = []
    for num in ids:
        item = _parse_email_num(mail, num)  # is_processed() проверка внутри
        if item:
            results.append(item)
    if results:
        log.info(f"Из них необработанных алертов: {len(results)}")
    return results


def fetch_one_old_alert_email(mail: imaplib.IMAP4_SSL) -> Optional[Dict]:
    """Берёт одно необработанное письмо старше NEW_EMAIL_DAYS дней (любое, и прочитанные тоже)."""
    mail.select("INBOX")
    before = _imap_since_date(NEW_EMAIL_DAYS)
    _, data = mail.search(None, f"BEFORE {before}")
    if not data or not data[0]:
        return None

    ids = data[0].split()
    log.info(f"Старых писем (старше {NEW_EMAIL_DAYS} дн.): {len(ids)}")

    # Берём самое старое, перебираем пока не найдём необработанный алерт
    for num in ids[:30]:
        item = _parse_email_num(mail, num)
        if item:
            item["is_old"] = True
            return item

    return None


def mark_email_read_imap(mail: imaplib.IMAP4_SSL, imap_num: bytes):
    """Помечает письмо прочитанным в Gmail."""
    try:
        mail.store(imap_num, "+FLAGS", "\\Seen")
        log.info(f"Письмо {imap_num} помечено прочитанным в Gmail")
    except Exception as e:
        log.error(f"Не удалось пометить прочитанным: {e}")


# ============================================================
# PUBMED API — получить абстракты по PMIDам
# ============================================================
def extract_pmids(text: str) -> List[str]:
    """Ищет PMIDы в тексте письма."""
    # PubMed алерты содержат ссылки вида /pubmed/12345678
    pmids = re.findall(r"/pubmed/(\d{7,8})", text)
    # Или просто PMID: 12345678
    pmids += re.findall(r"PMID[:\s]+(\d{7,8})", text, re.IGNORECASE)
    return list(set(pmids))[:10]  # не более 10


def fetch_pubmed_abstracts(pmids: List[str]) -> List[Dict]:
    """Загружает абстракты из PubMed E-utilities."""
    if not pmids:
        return []
    ids_str = ",".join(pmids)
    url = f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?db=pubmed&id={ids_str}&rettype=abstract&retmode=json&tool=science_monitor&email={GMAIL_USER}"
    try:
        resp = requests.get(url, timeout=20)
        if resp.status_code != 200:
            return []
        data = resp.json()
        articles = []
        for pmid, article_data in data.get("PubmedArticleSet", {}).get("PubmedArticle", {}).items() if isinstance(data.get("PubmedArticleSet", {}).get("PubmedArticle"), dict) else []:
            pass
        # Используем simpler endpoint
        return []
    except Exception:
        return []


def fetch_pubmed_summary(pmids: List[str]) -> str:
    """Получает краткие данные статей через esummary."""
    if not pmids:
        return ""
    ids_str = ",".join(pmids)
    url = f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?db=pubmed&id={ids_str}&rettype=abstract&retmode=text&tool=science_monitor&email={GMAIL_USER}"
    try:
        resp = requests.get(url, timeout=30)
        if resp.status_code == 200:
            return resp.text[:6000]
        return ""
    except Exception as e:
        log.warning(f"PubMed API ошибка: {e}")
        return ""


# ============================================================
# AI АНАЛИЗ через excash gemini-3.1-pro (резерв DeepSeek)
# ============================================================
def analyze_with_ai(email_data: Dict, pubmed_text: str = "") -> Optional[str]:
    """Отправляет содержимое письма в LLM (excash gemini-3.1-pro) и получает анализ."""
    body = email_data["body"]
    if pubmed_text:
        body = pubmed_text + "\n\n---\nИсходное письмо:\n" + body

    system_prompt = f"""Ты — научный ассистент врача-ортопеда-травматолога.
Анализируешь письма с алертами из медицинских журналов.

Интересы доктора:
{RESEARCH_INTERESTS}

Твоя задача — найти ВСЕ статьи в письме и для каждой написать блок СТРОГО в формате ниже.
Статьи должны идти в том же порядке, что и в письме.

ФОРМАТ КАЖДОГО БЛОКА (строго):
🔬 [Название статьи на РУССКОМ]
🔬eng: [Оригинальное название на АНГЛИЙСКОМ — ОБЯЗАТЕЛЬНО, точно как в письме]
📰 [Журнал, год]
📝 [Резюме 2-3 предложения на русском]
[Оценка: 🔥 Суперновинка / ⭐ Важно / 📌 Интересно / ⬜ Нерелевантно]

🔥 Суперновинка = прорывной метод, РКИ с сильными результатами, первое в мире исследование, меняет стандарт лечения.
Блоки разделяй пустой строкой.
Если статей нет — ответь только: "Нет статей для анализа."
Пиши только по-русски (кроме строки 🔬eng). Будь конкретным."""

    user_prompt = f"""Письмо от: {email_data['sender']}
Тема: {email_data['subject']}
Источник: {email_data['source']}

Содержимое:
{body}"""

    try:
        msgs = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        out = _ds_flash_call(msgs, max_tokens=4000, temperature=0.3)
        if not out:
            out = _excash_call(msgs, max_tokens=4000, temperature=0.3)
        return out or None
    except Exception as e:
        log.error(f"LLM ошибка: {e}")
        return None


# ============================================================
# TELEGRAM
# ============================================================
TG_MAX_LEN = 4000  # Telegram лимит 4096, оставляем запас


def _send_one(text: str, reply_markup: Optional[Dict] = None) -> bool:
    """Отправляет одно plain-text сообщение. reply_markup — inline клавиатура (опционально)."""
    payload: Dict = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "disable_web_page_preview": True,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json=payload,
            timeout=15,
        )
        if not resp.ok:
            log.error(f"Telegram ошибка: {resp.text[:300]}")
            return False
        return True
    except Exception as e:
        log.error(f"Telegram exception: {e}")
        return False


def _article_keyboard(url: str, article_id: str) -> Optional[Dict]:
    """Кнопки под статьёй: [🔗 Открыть] [📖 Глубокий анализ]."""
    row = []
    if url:
        row.append({"text": "🔗 Открыть статью", "url": url})
    if article_id:
        row.append({"text": "📖 Глубокий анализ", "callback_data": f"sci:{article_id}"})
    if not row:
        return None
    return {"inline_keyboard": [row]}


def send_telegram_smart(header: str, analysis: str, footer: str,
                        article_links: Optional[List[Dict]] = None) -> bool:
    """Один алерт = ОДНО сообщение (шапка + все статьи + футер) с общей клавиатурой.
    Раньше каждый блок летел отдельно — 5-8 сообщений подряд ощущались спамом."""

    raw_blocks = re.split(r'(?=\n🔬|\A🔬)', "\n" + analysis.strip())
    blocks = [b.strip() for b in raw_blocks if b.strip()]

    parts = [header.strip(), ""]
    kb_rows = []
    for i, block in enumerate(blocks):
        # строку 🔬eng: скрываем из текста (служебная, для поиска PMID)
        block_vis = re.sub(r'\n?🔬eng:.*', '', block).strip()
        if article_links and i < len(article_links):
            art = article_links[i]
            if art.get("url"):
                block_vis += f"\n🔗 {art['url']}"
            row = []
            if art.get("url"):
                row.append({"text": f"🔗 {i+1}", "url": art["url"]})
            if art.get("id"):
                row.append({"text": f"📖 Разбор {i+1}", "callback_data": f"sci:{art['id']}"})
            if row and len(kb_rows) < 8:
                kb_rows.append(row)
        parts.append(block_vis)
        parts.append("")
    parts.append(footer.strip())
    full = "\n".join(parts).strip()
    keyboard = {"inline_keyboard": kb_rows} if kb_rows else None

    if len(full) <= TG_MAX_LEN:
        return _send_one(full, reply_markup=keyboard)

    # не влезло — режем по границам блоков, клавиатура на последнем куске
    ok = True
    chunk = ""
    pieces = []
    for seg in parts:
        if len(chunk) + len(seg) + 1 > TG_MAX_LEN and chunk.strip():
            pieces.append(chunk.strip())
            chunk = ""
        chunk += seg + "\n"
    if chunk.strip():
        pieces.append(chunk.strip())
    for j, piece in enumerate(pieces):
        ok &= _send_one(piece, reply_markup=keyboard if j == len(pieces) - 1 else None)
        time.sleep(0.4)
    return ok


def format_email_date(date_str: str) -> str:
    """Красиво форматирует дату письма."""
    try:
        dt = parsedate_to_datetime(date_str)
        dt_msk = dt.astimezone(timezone(timedelta(hours=3)))
        return dt_msk.strftime("%d.%m.%Y")
    except Exception:
        return date_str[:16] if date_str else "?"


def build_telegram_parts(email_data: Dict, analysis: str) -> Tuple[str, str, str]:
    """Возвращает (header, analysis_clean, footer)."""
    email_date = format_email_date(email_data.get("date", ""))
    now = datetime.now().strftime("%d.%m.%Y %H:%M")
    is_old = email_data.get("is_old", False)

    if is_old:
        label = f"📚 Архивный алерт — {email_data['source']}"
        date_line = f"📅 Письмо от: {email_date} | Обработано: {now}"
    else:
        label = f"📚 Научный алерт — {email_data['source']}"
        date_line = f"📅 {email_date}"

    header = (
        f"{label}\n"
        f"📧 {email_data['subject'][:100]}\n"
        f"{date_line}\n"
        + "─" * 32 + "\n\n"
    )
    footer = "\n" + "─" * 32 + f"\nАнализ: {LAST_ENGINE} | Science Monitor"

    # Убираем markdown-артефакты из AI-ответа
    clean = re.sub(r'\*\*(.+?)\*\*', r'\1', analysis)
    clean = re.sub(r'\*(.+?)\*', r'\1', clean)
    clean = clean.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")

    return header, clean, footer


# ============================================================
# ГЛАВНЫЙ ЦИКЛ
# ============================================================
def process_email(email_data: Dict, mail: Optional[imaplib.IMAP4_SSL] = None):
    """Обрабатывает одно письмо: AI анализ → Telegram → пометка прочитанным."""
    tag = "🗄 Архив" if email_data.get("is_old") else "🆕 Новое"
    log.info(f"[{tag}] Обработка: {email_data['subject'][:60]}")

    # Пробуем получить абстракты из PubMed
    pmids = extract_pmids(email_data["body"])
    pubmed_text = ""
    if pmids:
        log.info(f"Найдено PMIDов: {len(pmids)} → запрашиваем PubMed")
        pubmed_text = fetch_pubmed_summary(pmids)

    # AI анализ
    analysis = analyze_with_ai(email_data, pubmed_text)
    if not analysis:
        log.warning("AI не вернул ответ, пропускаем")
        return

    if "Нет статей для анализа" in analysis:
        log.info("AI: нет статей в письме")
        mark_processed(email_data["message_id"])
        # Старое письмо без статей — всё равно помечаем прочитанным
        if mail and email_data.get("is_old"):
            mark_email_read_imap(mail, email_data["imap_num"])
        return

    # Ищем статьи в PubMed по названиям из AI-ответа
    article_links = resolve_article_links(analysis, email_data["subject"])

    # Убираем строки 🔬eng: из финального текста (служебные)
    analysis = re.sub(r'\n?🔬eng:[^\n]+', '', analysis).strip()

    # Отправляем в Telegram
    header, clean_analysis, footer = build_telegram_parts(email_data, analysis)
    if send_telegram_smart(header, clean_analysis, footer, article_links=article_links):
        log.info("Успешно отправлено в Telegram")
        mark_processed(email_data["message_id"])
        # Помечаем прочитанным в Gmail
        if mail:
            mark_email_read_imap(mail, email_data["imap_num"])
    else:
        log.error("Не удалось отправить в Telegram")


def run():
    log.info("=== Gmail Science Monitor запущен ===")
    log.info(f"Gmail: {GMAIL_USER}")
    log.info(f"Модель: {MODEL}")
    log.info(f"Свежие письма: последние {NEW_EMAIL_DAYS} дн. | +1 архивное за цикл")
    log.info(f"Интервал проверки: {CHECK_INTERVAL_SEC} сек")
    init_db()

    while True:
        try:
            log.info("Подключаемся к Gmail...")
            mail = connect_gmail()

            # 1. Свежие алерты (≤ 5 дней)
            recent = fetch_recent_alert_emails(mail)
            if recent:
                log.info(f"Свежих алертов: {len(recent)}")
                for e_data in recent:
                    process_email(e_data, mail)
                    time.sleep(3)
            else:
                log.info("Свежих алертов нет")

            # 2. Одно архивное письмо
            old = fetch_one_old_alert_email(mail)
            if old:
                log.info(f"Обрабатываем архивное письмо от {format_email_date(old.get('date',''))}")
                process_email(old, mail)
            else:
                log.info("Архивных непрочитанных нет")

            mail.logout()

        except imaplib.IMAP4.error as e:
            log.error(f"IMAP ошибка: {e}")
        except Exception as e:
            log.error(f"Неожиданная ошибка: {e}", exc_info=True)

        log.info(f"Следующая проверка через {CHECK_INTERVAL_SEC // 60} мин...")
        time.sleep(CHECK_INTERVAL_SEC)


if __name__ == "__main__":
    run()
