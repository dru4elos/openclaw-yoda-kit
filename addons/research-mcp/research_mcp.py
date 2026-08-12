#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
research_mcp — реальный веб-поиск для Йоды. Без ключей (DuckDuckGo), с извлечением
текста страниц.

Философия: инструмент обязан ЯВНО отличать «нашёл» от «не нашёл». Пустая выдача
возвращается как заметный маркер НИЧЕГО_НЕ_НАЙДЕНО, чтобы модель не могла принять
тишину за подтверждение. У каждого факта есть URL — иначе его нельзя цитировать.
"""
import io
import json
import re
import sys
import time
import threading

import httpx
from ddgs import DDGS
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("research")

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")

NOTHING = "НИЧЕГО_НЕ_НАЙДЕНО"
_lock = threading.Lock()          # ddgs не любит параллельные вызовы из одного процесса
_last_call = [0.0]


def _throttle(min_gap=1.2):
    """DDG банит за частые запросы. Держим паузу между вызовами."""
    with _lock:
        gap = time.time() - _last_call[0]
        if gap < min_gap:
            time.sleep(min_gap - gap)
        _last_call[0] = time.time()


def _ddg(kind, query, max_results, region, timelimit=None):
    """Один поиск с ретраями. Возвращает список словарей либо кидает исключение."""
    last_err = None
    for attempt in range(3):
        try:
            _throttle()
            with DDGS(timeout=25) as d:
                fn = getattr(d, kind)
                kw = dict(query=query, max_results=max_results, region=region)
                if timelimit:
                    kw["timelimit"] = timelimit
                return list(fn(**kw))
        except Exception as e:                       # noqa: BLE001
            last_err = e
            time.sleep(2 + attempt * 3)
    raise RuntimeError(f"{type(last_err).__name__}: {last_err}")


def _fmt_results(rows, query, kind="веб"):
    if not rows:
        return (f"{NOTHING}\n"
                f"Запрос: «{query}» ({kind}). Поиск отработал, но выдача пуста.\n"
                f"Это НЕ значит, что явления не существует — переформулируй запрос "
                f"(другие слова, английский язык, синонимы) и попробуй снова.\n"
                f"Придумывать результаты вместо поиска — запрещено.")
    out = [f"НАЙДЕНО: {len(rows)} рез. по запросу «{query}» ({kind})", ""]
    for i, r in enumerate(rows, 1):
        title = (r.get("title") or "").strip()
        url = (r.get("href") or r.get("url") or "").strip()
        body = (r.get("body") or r.get("excerpt") or "").strip()
        date = (r.get("date") or "").strip()
        src = (r.get("source") or "").strip()
        out.append(f"[{i}] {title}")
        out.append(f"    URL: {url}")
        if date or src:
            out.append(f"    Дата/источник: {date} {src}".rstrip())
        if body:
            out.append(f"    Фрагмент: {body[:400]}")
        out.append("")
    out.append("Цитировать можно ТОЛЬКО то, что есть выше, с указанием URL. "
               "Фрагмент — это не вся страница: чтобы утверждать что-то по сути, "
               "открой страницу через read_url.")
    return "\n".join(out)


def _extract(url, max_chars):
    """Скачать страницу и вытащить читаемый текст. PDF тоже."""
    try:
        with httpx.Client(follow_redirects=True, timeout=30,
                          headers={"User-Agent": UA}) as c:
            resp = c.get(url)
            resp.raise_for_status()
            ctype = resp.headers.get("content-type", "").lower()
            raw = resp.content
    except Exception as e:                           # noqa: BLE001
        return f"ОШИБКА_ЗАГРУЗКИ: {url}\n{type(e).__name__}: {str(e)[:200]}\n" \
               f"Страницу прочитать не удалось. Не выдумывай её содержимое."

    if "pdf" in ctype or url.lower().endswith(".pdf"):
        try:
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(raw))
            text = "\n".join((p.extract_text() or "") for p in reader.pages[:40])
        except Exception as e:                       # noqa: BLE001
            return f"ОШИБКА_PDF: {url}\n{type(e).__name__}: {str(e)[:200]}"
    else:
        html = raw.decode(resp.encoding or "utf-8", errors="replace")
        text = ""
        try:
            import trafilatura
            text = trafilatura.extract(
                html, include_comments=False, include_tables=True,
                favor_recall=True) or ""
        except Exception:                            # noqa: BLE001
            text = ""
        if len(text) < 200:                          # фолбэк на голый разбор тегов
            try:
                from bs4 import BeautifulSoup
                soup = BeautifulSoup(html, "lxml")
                for t in soup(["script", "style", "nav", "footer", "header", "noscript"]):
                    t.decompose()
                text = re.sub(r"\n{3,}", "\n\n", soup.get_text("\n", strip=True))
            except Exception:                        # noqa: BLE001
                pass

    text = (text or "").strip()
    if not text:
        return (f"ПУСТАЯ_СТРАНИЦА: {url}\nТекст извлечь не удалось "
                f"(вероятно, JS-рендеринг или защита). Не выдумывай содержимое — "
                f"возьми другой источник.")
    truncated = len(text) > max_chars
    return (f"ИСТОЧНИК: {url}\n"
            f"Символов: {len(text)}{' (обрезано)' if truncated else ''}\n"
            f"{'-' * 60}\n{text[:max_chars]}")


@mcp.tool()
def web_search(query: str, max_results: int = 8, region: str = "ru-ru",
               timelimit: str = "") -> str:
    """Реальный поиск в интернете (DuckDuckGo). Возвращает заголовки, URL и фрагменты.

    Единственный законный способ узнать факт из интернета. Всё, чего нет в выдаче
    этого инструмента (или read_url), цитировать как факт нельзя.

    query: поисковый запрос. Для мировых тем ищи ПО-АНГЛИЙСКИ — выдача богаче.
    max_results: сколько результатов (1-25).
    region: ru-ru для России, us-en для мира, wt-wt без региона.
    timelimit: пусто = за всё время; d/w/m/y = за день/неделю/месяц/год.
    """
    try:
        rows = _ddg("text", query, max(1, min(int(max_results), 25)), region,
                    timelimit or None)
    except Exception as e:                           # noqa: BLE001
        return (f"ПОИСК_СЛОМАЛСЯ: {e}\nЗапрос: «{query}»\n"
                f"Сообщи пользователю, что поиск недоступен. "
                f"НЕ ОТВЕЧАЙ ПО ПАМЯТИ вместо поиска.")
    return _fmt_results(rows, query, "веб")


@mcp.tool()
def web_news(query: str, max_results: int = 8, region: str = "ru-ru",
             timelimit: str = "") -> str:
    """Поиск свежих новостей по теме. Те же правила цитирования, что у web_search.

    timelimit: d = за сутки, w = за неделю, m = за месяц.
    """
    try:
        rows = _ddg("news", query, max(1, min(int(max_results), 25)), region,
                    timelimit or None)
    except Exception as e:                           # noqa: BLE001
        return f"ПОИСК_СЛОМАЛСЯ: {e}\nЗапрос: «{query}». Не отвечай по памяти."
    return _fmt_results(rows, query, "новости")


@mcp.tool()
def read_url(url: str, max_chars: int = 20000) -> str:
    """Открыть страницу и вернуть её читаемый текст (HTML или PDF).

    Фрагменты из выдачи поиска — это не содержание страницы. Если утверждение
    важное (цифра, вывод, цитата), открывай страницу этим инструментом.
    """
    return _extract(url, max(1000, min(int(max_chars), 80000)))


@mcp.tool()
def search_and_read(query: str, num_sources: int = 4, region: str = "ru-ru",
                    chars_per_source: int = 6000) -> str:
    """Найти и СРАЗУ прочитать несколько источников по теме. Основной инструмент
    исследования: один вызов = поиск + чтение верхних страниц.

    Используй, когда нужно разобраться в вопросе по существу, а не просто найти ссылки.
    num_sources: сколько страниц открыть (1-8).
    """
    n = max(1, min(int(num_sources), 8))
    try:
        rows = _ddg("text", query, n + 4, region, None)
    except Exception as e:                           # noqa: BLE001
        return f"ПОИСК_СЛОМАЛСЯ: {e}\nЗапрос: «{query}». Не отвечай по памяти."
    if not rows:
        return _fmt_results([], query, "веб")

    parts = [f"ИССЛЕДОВАНИЕ ПО ЗАПРОСУ: «{query}»",
             f"Найдено ссылок: {len(rows)}, открываю: до {n}", "=" * 60, ""]
    opened = 0
    for r in rows:
        if opened >= n:
            break
        url = (r.get("href") or "").strip()
        if not url:
            continue
        body = _extract(url, chars_per_source)
        if body.startswith(("ОШИБКА_", "ПУСТАЯ_")):
            parts.append(f"[пропущено] {url} — {body.splitlines()[0]}")
            parts.append("")
            continue
        opened += 1
        parts.append(f"### ИСТОЧНИК {opened}: {(r.get('title') or '').strip()}")
        parts.append(body)
        parts.append("")
    if opened == 0:
        parts.append(f"{NOTHING}: ссылки нашлись, но ни одну страницу открыть не "
                     f"удалось. Не пересказывай содержимое по памяти — скажи это прямо.")
    else:
        parts.append("=" * 60)
        parts.append(f"Прочитано источников: {opened}. Каждое утверждение в ответе "
                     f"привязывай к конкретному URL выше.")
    return "\n".join(parts)


@mcp.tool()
def search_multi(queries: list[str], max_results: int = 5,
                 region: str = "ru-ru") -> str:
    """Прогнать НЕСКОЛЬКО поисковых запросов за один вызов. queries — список строк.

    Для широкой темы это правильный подход: один запрос никогда не покрывает вопрос.
    Пример: queries = ["dating startup biometrics", "heart rate matching app",
                       "speed dating technology 2026"]
    """
    if isinstance(queries, str):                     # терпим и строку через | или запятую
        raw = queries.split("|") if "|" in queries else queries.split(",")
    else:
        raw = list(queries or [])
    qs = [str(q).strip() for q in raw if str(q).strip()][:8]
    if not qs:
        return ("Не передано ни одного запроса. Передай список строк, например: "
                '["первый запрос", "второй запрос"]')
    out = []
    for q in qs:
        try:
            rows = _ddg("text", q, max(1, min(int(max_results), 15)), region, None)
            out.append(_fmt_results(rows, q, "веб"))
        except Exception as e:                       # noqa: BLE001
            out.append(f"ПОИСК_СЛОМАЛСЯ по «{q}»: {e}")
        out.append("\n" + "=" * 60 + "\n")
    return "\n".join(out)


@mcp.tool()
def research_status() -> str:
    """Самопроверка контура поиска: работает ли выход в интернет прямо сейчас."""
    checks = []
    try:
        rows = _ddg("text", "test query openclaw", 2, "wt-wt", None)
        checks.append(f"DuckDuckGo: OK ({len(rows)} рез.)")
    except Exception as e:                           # noqa: BLE001
        checks.append(f"DuckDuckGo: СЛОМАН — {e}")
    try:
        with httpx.Client(timeout=15, headers={"User-Agent": UA}) as c:
            r = c.get("https://example.com")
        checks.append(f"Прямой HTTP: OK ({r.status_code})")
    except Exception as e:                           # noqa: BLE001
        checks.append(f"Прямой HTTP: СЛОМАН — {type(e).__name__}")
    return "\n".join(checks)


if __name__ == "__main__":
    mcp.run()
