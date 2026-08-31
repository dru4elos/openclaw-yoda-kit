#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Telegram доктора для Йоды — ЧЕРЕЗ ЕДИНЫЙ ШЛЮЗ tg-gateway (127.0.0.1:8099).

  yoda_tg.py dialogs [--n 25]
  yoda_tg.py read "<чат>" [--n 30]
  yoda_tg.py search "<текст>" [--chat "<чат>"] [--n 20]
  yoda_tg.py media "<чат>" [--n 5]      -> фото в /tmp/tg_*.jpg, печатает пути
  yoda_tg.py digest [--folder СТАРТАПЫ] [--hours 24]
  yoda_tg.py send "<чат>" "<текст>" --confirm yes
  yoda_tg.py sendfile "<чат>" <путь> [--caption ""] --confirm yes

СВОЮ TELETHON-СЕССИЮ ЗДЕСЬ ЗАВОДИТЬ НЕЛЬЗЯ. Раньше скрипт держал отдельную
(loft_outreach/cancel_sess) и она регулярно умирала с AuthKeyDuplicatedError:
Телеграм аннулирует ключ, если им подключаются с двух IP. Единственное
подключение держит шлюз, все остальные ходят к нему по HTTP.
"""
import argparse
import os
import sys
from datetime import datetime, timedelta, timezone

import requests

GW = os.environ.get("TG_GATEWAY_URL", "http://127.0.0.1:8099")
MEDIA_DIR = "/tmp"


def gw(path, method="GET", **kw):
    """Запрос к шлюзу. Понятная ошибка вместо стектрейса телетона."""
    try:
        r = (requests.post(GW + path, json=kw.get("json") or {}, timeout=kw.get("timeout", 300))
             if method == "POST" else
             requests.get(GW + path, params=kw.get("params") or {}, timeout=kw.get("timeout", 300)))
        r.raise_for_status()
        return r.json()
    except requests.HTTPError as e:
        sys.exit(f"шлюз ответил {e.response.status_code}: {e.response.text[:200]}")
    except Exception as e:
        sys.exit(f"шлюз недоступен ({type(e).__name__}). Проверь: systemctl status tg-gateway")


def _when(iso, fmt="%d.%m %H:%M"):
    if not iso:
        return ""
    try:
        return datetime.fromisoformat(iso).strftime(fmt)
    except Exception:
        return ""


def cmd_dialogs(a):
    print(f"Последние {a.n} диалогов:")
    for d in gw("/dialogs", params={"limit": a.n}).get("dialogs") or []:
        unread = f" [непроч.{d['unread']}]" if d.get("unread") else ""
        last = (d.get("last") or "").replace("\n", " ")[:70]
        print(f"- {d['name']}{unread} | {_when(d.get('date'))} | {last}")


def cmd_read(a):
    r = gw("/read", params={"chat": a.chat, "limit": a.n})
    msgs = r.get("messages") or []
    print(f"Чат: {a.chat} — последние {len(msgs)} сообщений (снизу вверх свежие):")
    lines = []
    for m in msgs:
        who = "Я" if m.get("out") else (m.get("sender") or "?")
        body = (m.get("text") or "").replace("\n", " ")
        if not body and m.get("has_media"):
            body = "[медиа]"
        lines.append(f"[{_when(m.get('date'))}] {who}: {body[:400]}")
    for line in reversed(lines):
        print(line)


def cmd_search(a):
    params = {"query": a.query, "limit": a.n}
    if a.chat:
        params["chat"] = a.chat
    r = gw("/search", params=params)
    print(f"Поиск «{a.query}»" + (f" в «{a.chat}»" if a.chat else " по всем чатам") + ":")
    found = r.get("messages") or []
    for m in found:
        print(f"- [{_when(m.get('date'), '%d.%m.%y %H:%M')}] {m.get('chat')} | "
              f"{m.get('sender') or '?'}: {(m.get('text') or '')[:200]}")
    if not found:
        print("(ничего не найдено)")


def cmd_media(a):
    r = gw("/media", method="POST", json={"chat": a.chat, "limit": a.n, "dir": MEDIA_DIR})
    files = r.get("files") or []
    print(f"Картинки из «{a.chat}» (последние {len(files)}):")
    for f in files:
        cap = (f.get("caption") or "").replace("\n", " ")[:60]
        print(f"{f['path']} | {_when(f.get('date'))} | {cap}")
    if not files:
        print("(картинок не найдено)")


def _excash():
    env = {}
    for p in ("/home/openclaw/.openclaw/.env", "/home/knee_bot/keys.env"):
        if os.path.exists(p):
            for line in open(p, encoding="utf-8"):
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    env.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    return env.get("EXCASH_API_KEY", ""), env.get("EXCASH_API_URL", "")


def _sum_llm(text, folders):
    k, u = _excash()
    if not k:
        return "[нет ключа LLM — сырые заголовки]\n" + text[:3000]
    prompt = (
        "Ниже — посты за последние сутки из Telegram-каналов доктора "
        f"(папки: {folders}). Сделай ДАЙДЖЕСТ на русском для занятого человека:\n\n"
        "## Главное\n(3-5 пунктов — что действительно стоит внимания, с указанием канала)\n"
        "## Стартапы и продукт\n## DS / ML / ИИ\n## Мимо кассы\n(одной строкой, что было шумом)\n\n"
        "Правила: только по существу, без воды и без пересказа рекламы. Если пост важный — "
        "поясни ЧЕМ именно. Дубли из разных каналов объединяй. Максимум 350 слов."
    )
    try:
        r = requests.post(u.rstrip("/") + "/chat/completions",
                          headers={"Authorization": "Bearer " + k, "Content-Type": "application/json"},
                          json={"model": "gemini-3.1-pro", "max_tokens": 8000, "temperature": 0.3,
                                "messages": [{"role": "user",
                                              "content": prompt + "\n\n=== ПОСТЫ ===\n" + text[:250000]}]},
                          timeout=600)
        r.raise_for_status()
        return ((r.json().get("choices") or [{}])[0].get("message", {}) or {}).get("content", "").strip()
    except Exception as e:
        return f"[ошибка LLM: {type(e).__name__}]\n" + text[:2000]


def cmd_digest(a):
    want = [x.strip().lower() for x in (a.folder or ["СТАРТАПЫ", "DS ML"])]
    all_folders = gw("/folders", timeout=600).get("folders") or []
    peers, names = [], []
    for fl in all_folders:
        if fl["title"].strip().lower() in want:
            names.append(fl["title"])
            peers.extend(fl.get("peers") or [])
    if not peers:
        have = ", ".join(f["title"] for f in all_folders)
        sys.exit(f"папки не найдены: {', '.join(want)}. Есть: {have}")

    cutoff = datetime.now(timezone.utc) - timedelta(hours=a.hours)
    chunks, total = [], 0
    for p in peers:
        target = p.get("username") or str(p["id"])
        try:
            msgs = gw("/read", params={"chat": target, "limit": 40}).get("messages") or []
        except SystemExit:
            continue
        posts = []
        for m in msgs:
            try:
                dt = datetime.fromisoformat(m["date"]) if m.get("date") else None
            except Exception:
                dt = None
            if dt and dt < cutoff:
                break
            txt = (m.get("text") or "").strip()
            if len(txt) > 40:
                posts.append(txt[:1200])
        if posts:
            total += len(posts)
            chunks.append(f"### Канал: {p['name']}\n" + "\n---\n".join(posts[:12]))

    if not chunks:
        print(f"За {a.hours} ч в папках {', '.join(names)} ничего нового.")
        return
    print(f"Собрано {total} постов из {len(chunks)} каналов ({', '.join(names)}). Готовлю выжимку…",
          file=sys.stderr)
    out = _sum_llm("\n\n".join(chunks), ", ".join(names))
    print(f"📰 Дайджест за {a.hours} ч — {', '.join(names)}\n\n" + out)


def _resolve_strict(chat):
    """Для отправки: только точное совпадение — чтобы не написать не тому человеку."""
    chat = (chat or "").strip()
    if not chat:
        sys.exit("нужно указать чат")
    low = chat.lower()
    if low in ("me", "saved", "savedmessages", "избранное", "избранные", "себе"):
        return "me"
    if chat.lstrip("-").isdigit() or chat.startswith("@"):
        return chat
    dialogs = gw("/dialogs", params={"limit": 400}).get("dialogs") or []
    exact = [d for d in dialogs if (d.get("name") or "").strip().lower() == low]
    if len(exact) == 1:
        return str(exact[0]["id"])
    if len(exact) > 1:
        sys.exit(f"несколько чатов с именем «{chat}» — укажи @username или id")
    part = [d["name"] for d in dialogs if low in (d.get("name") or "").strip().lower()]
    if part:
        sys.exit("точного чата «%s» нет. Похожие: %s. Уточни ПОЛНОЕ имя, @username или id."
                 % (chat, "; ".join(part[:8])))
    sys.exit(f"чат «{chat}» не найден — уточни @username или id")


OWNER_ID = "123456789"   # свой telegram id
_OWNER_NAME = "имя владельца"  # как к нему обращаются в группах, строчными

def _bot_copy(target, text):
    """Копия доктору голосом БОТА о каждой отправке с его личного аккаунта.
    Неотключаемая подотчётность после инцидента 07.08 (автоответ Наталье Карловой)."""
    try:
        import json as _j, re as _re, urllib.request as _u
        cfg = open("/home/openclaw/.openclaw/openclaw.json", encoding="utf-8").read()
        m = _re.search(r'"botToken"\s*:\s*"([^"]+)"', cfg)
        if not m:
            return
        token = m.group(1)
        body = _j.dumps({"chat_id": OWNER_ID,
                         "text": f"\U0001F4E4 С твоего аккаунта отправлено в [{target}]:\n{text[:800]}"}).encode()
        req = _u.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=body,
                         headers={"Content-Type": "application/json"})
        _u.urlopen(req, timeout=10).read()
    except Exception:
        pass  # копия best-effort: сбой уведомления не должен ломать отправку



# Служебные отправители: их «неответ» доктора не волнует
_NOISE = ("telegram", "notifications", "spambot", "bot", "канал", "channel",
          "vip сигналы", "сигналы", "reminders", "яндекс", "ozon", "wildberries",
          "сбер", "тинькофф", "т-банк", "госуслуги", "delivery", "доставка",
          # собственные сервисы доктора — они уведомляют, а не ждут ответа
          "йода", "докмед", "свойврач", "доктор семенов", "симулейтив",
          "избранное", "saved messages")

# Маркеры обещаний в СВОИХ сообщениях
_PROMISE = ("скину", "пришлю", "отправлю", "сделаю", "посмотрю", "гляну", "проверю",
            "напишу", "перезвоню", "позвоню", "уточню", "разберусь", "займусь",
            "подготовлю", "договорюсь", "завтра", "на неделе", "чуть позже",
            "как освобожусь", "вечером", "обещаю", "к понедельнику", "до конца недели")


def _noisy(name):
    n = (name or "").lower()
    return any(w in n for w in _NOISE)


DISMISS_FILE = "/home/openclaw/.openclaw/workspace/memory/pending_dismissed.json"
STATE_FILE = "/home/openclaw/.openclaw/workspace/memory/pending_state.json"


def _load_dismissed():
    """{chat_id: запись} — что доктор просил не напоминать.

    Режимы: "until_new" — замять текущий долг, но вернуть, если человек напишет
    НОВОЕ (он ведь спросит уже о другом); "until:<iso>" — отложить до даты;
    "forever" — не показывать вовсе. Старый формат (строка) читается как раньше.
    """
    try:
        import json
        with open(DISMISS_FILE, encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return {}
    live, now = {}, datetime.now(timezone.utc)
    for k, v in (data or {}).items():
        rec = v if isinstance(v, dict) else (
            {"mode": "forever"} if v == "forever" else {"mode": "until", "until": v})
        if rec.get("mode") == "until":
            try:
                if datetime.fromisoformat(rec.get("until") or "") <= now:
                    continue                      # срок вышел — долг снова виден
            except Exception:
                continue
        live[str(k)] = rec
    return live


def _is_hidden(dl, dismissed):
    """Скрыт ли чат сейчас. until_new снимается сам, как только пришло новое."""
    rec = dismissed.get(str(dl.get("id")))
    if not rec:
        return False
    if rec.get("mode") == "until_new":
        return (dl.get("date") or "") <= (rec.get("sig") or "")
    return True


def cmd_dismiss(a):
    """Снять напоминание по чату: навсегда или на N дней."""
    import json
    import os
    d = {}
    if os.path.exists(DISMISS_FILE):
        try:
            d = json.load(open(DISMISS_FILE, encoding="utf-8")) or {}
        except Exception:
            d = {}
    target, found = str(a.chat), None
    if not target.lstrip("-").isdigit():                # дали имя — ищем id
        # Личные чаты берём отфильтрованными: в общем списке лимит съедали каналы,
        # имя не находилось, и в файл писалось само имя — dismiss молча не срабатывал,
        # доктор просил «убери», а чат возвращался в бриф на следующее утро.
        pools = [{"kinds": "user", "days": 365, "limit": 500}, {"limit": 400}]
        low = a.chat.strip().lower()
        for params in pools:
            dls = gw("/dialogs", params=params).get("dialogs") or []
            exact = [d for d in dls if (d.get("name") or "").strip().lower() == low]
            part = [d for d in dls if low in (d.get("name") or "").strip().lower()]
            hits = exact or part
            if len(hits) == 1:
                found = hits[0]
                break
            if len(hits) > 1:
                sys.exit("несколько чатов подходят под «%s»: %s. Уточни полное имя или id."
                         % (a.chat, "; ".join((h.get("name") or "?") for h in hits[:8])))
        if not found:
            sys.exit(f"чат «{a.chat}» не найден — уточни @username или id. "
                     "Ничего не записал: молча скрыть не тот чат опаснее.")
        target = str(found["id"])
    name = (found or {}).get("name") if not str(a.chat).lstrip("-").isdigit() else a.chat
    now_iso = datetime.now(timezone.utc).isoformat()
    if a.days == 0 and not a.forever:
        d.pop(target, None)
        json.dump(d, open(DISMISS_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print(f"OK: чат {a.chat} снова в напоминаниях.")
        return
    if a.forever:
        d[target] = {"mode": "forever", "name": name, "at": now_iso}
        when = "навсегда (даже если напишет ещё)"
    elif a.days is None:
        # по умолчанию — «замять этот долг»: новое сообщение вернёт чат в список
        sig = (found or {}).get("date") or ""
        d[target] = {"mode": "until_new", "name": name, "at": now_iso, "sig": sig}
        when = "до следующего его сообщения"
    else:
        until = datetime.now(timezone.utc) + timedelta(days=a.days)
        d[target] = {"mode": "until", "name": name, "at": now_iso,
                     "until": until.isoformat()}
        when = f"на {a.days} дн. (до {until.strftime('%d.%m')})"
    os.makedirs(os.path.dirname(DISMISS_FILE), exist_ok=True)
    json.dump(d, open(DISMISS_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"OK: чат {a.chat} (id {target}) убран из напоминаний {when}.")
    print("Вернуть: yoda_tg.py dismiss <чат> --days 0 | посмотреть замятое: yoda_tg.py dismissed")



def cmd_dismissed(a):
    """Показать, что доктор просил не напоминать, и как это снять."""
    d = _load_dismissed()
    if not d:
        print("Ничего не замято — в напоминаниях всё.")
        return
    print(f"ЗАМЯТО ({len(d)}):\n")
    names = {}
    if any(not (r.get("name") or "") for r in d.values()):
        for dl in (gw("/dialogs", params={"kinds": "user", "days": 365,
                                          "limit": 500}).get("dialogs") or []):
            names[str(dl["id"])] = dl.get("name")
    for k, rec in d.items():
        nm = rec.get("name") or names.get(k) or k
        mode = rec.get("mode")
        if mode == "forever":
            how = "навсегда"
        elif mode == "until_new":
            how = "пока не напишет что-то новое"
        else:
            how = "до " + _when(rec.get("until"), "%d.%m")
        print(f"  • {nm} — {how}   (снять: dismiss \"{nm}\" --days 0)")


def _llm_env(name):
    """Ключи лежат у openclaw — скрипт ходит от root через sudo."""
    try:
        for line in open("/home/openclaw/.openclaw/.env", encoding="utf-8"):
            line = line.strip()
            if line.startswith(name + "="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    except Exception:
        pass
    return ""


def _classify_debts(items):
    """items: [(ключ, имя, текст-контекст)] -> {ключ: {"kind","what"}}.

    kind: work  — профессиональный запрос, пациент, деньги, документы, договорённость
          promise — доктор что-то обещал и не закрыл
          social — бытовое, шутки, «сходим куда-нибудь», реакции
    Один вызов на всю пачку: быстро и дёшево. Модель недоступна — возвращаем
    пусто, и тогда показываем ВСЁ (лучше лишнее, чем потерять важное)."""
    if not items:
        return {}
    import json as _j
    import urllib.request as _u
    key = _llm_env("DEEPSEEK_API_KEY")
    if not key:
        return {}
    # Пачками: одним куском на 70+ чатов модель отдаёт обрезанный JSON или 504,
    # и тогда терялись вердикты сразу по всем — долг молча исчезал из брифа.
    BATCH = 15
    if len(items) > BATCH:
        # Параллельно: пачка идёт ~70 с, а их бывает шесть. Последовательно это
        # 7 минут — утренний бриф столько не ждёт и рисует раздел пустым.
        from concurrent.futures import ThreadPoolExecutor
        chunks = [items[i:i + BATCH] for i in range(0, len(items), BATCH)]
        out = {}
        with ThreadPoolExecutor(max_workers=min(6, len(chunks))) as ex:
            for got in ex.map(_classify_debts, chunks):
                out.update(got or {})
        return out
    # Нумеруем чаты 1..N вместо длинных telegram-id: на id модель регулярно
    # возвращала свой вариант ключа, вердикт не сопоставлялся, и чат молча падал
    # в «дела» без сути — 45 таких из 51 в прогоне 30.08.
    idx2key = {str(i + 1): k for i, (k, _n, _c) in enumerate(items)}
    name2key = {(_n or "").strip().lower(): k for k, _n, _c in items}
    blob = "\n\n".join(f"### ЧАТ {i + 1} | {name}\n{ctx[:1400]}"
                       for i, (k, name, ctx) in enumerate(items))
    prompt = (
        "Ты помощник врача (детский травматолог-ортопед, ведёт клинику и IT-проекты).\n"
        "Ниже куски переписок. Реши по КАЖДОМУ чату: висит ли на враче незакрытое дело.\n\n"
        "ГЛАВНЫЙ КРИТЕРИЙ — что в ПОСЛЕДНИХ сообщениях собеседника:\n"
        "  • есть ВОПРОС или ПРОСЬБА к врачу, на которые он не ответил → work\n"
        "  • врач сам обещал что-то сделать/прислать/узнать и не закрыл → promise\n"
        "  • НЕТ ни вопроса, ни просьбы → social (даже если переписка деловая по теме)\n\n"
        "В social идут: «спасибо», «ок», «понял», «сделаю», смайлы и реакции, "
        "пересланная ссылка или новость без вопроса, обсуждение прочитанного/фильмов, "
        "«как дела», приветствие без продолжения, светская болтовня, договорённость "
        "которую собеседник уже подтвердил. Если собеседник поблагодарил или сам сказал "
        "«сделаю» — врачу отвечать НЕЧЕГО, это social.\n\n"
        "В work идут: вопрос про лечение/диагноз/снимок от пациента или родителя, "
        "рабочий вопрос коллеги, «делаем или нет», сроки, деньги, документы, просьба "
        "прислать или посмотреть, назначенная встреча без подтверждения врача.\n\n"
        "Не путай тему и действие: разговор МОЖЕТ быть про медицину, но если вопроса "
        "нет — это social.\n\n"
        "Верни СТРОГО JSON-массив без пояснений, элемент:\n"
        '{"chat":"<НОМЕР чата из заголовка, например 3>","kind":"work|promise|social",'
        '"what":"<одной фразой: какой именно вопрос ждёт ответа или что он обещал; '
        'для social — пустая строка>"}\n\n'
        + blob)

    def _try(url, api_key, model):
        # max_tokens щедрый: модели reasoning тратят сотни токенов на рассуждения,
        # при скупом лимите content приходит пустым или обрезанным
        body = _j.dumps({"model": model, "max_tokens": 8000, "temperature": 0.1,
                         "messages": [{"role": "user", "content": prompt}]}).encode()
        req = _u.Request(url, data=body,
                         headers={"Authorization": "Bearer " + api_key,
                                  "Content-Type": "application/json"})
        r = _j.loads(_u.urlopen(req, timeout=180).read().decode())
        txt = (((r.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
        if txt.startswith("```"):
            txt = txt.split("```")[1]
            txt = txt[4:] if txt.lower().startswith("json") else txt
        i, jx = txt.find("["), txt.rfind("]")
        if i == -1:
            return {}
        chunk = txt[i:jx + 1] if jx > i else txt[i:] + "]"   # добираем обрезанный хвост
        arr = _j.loads(chunk)
        out = {}
        for x in arr:
            raw = str(x.get("chat") or "").strip()
            key = idx2key.get(raw) or idx2key.get(raw.lstrip("#").strip()) \
                or name2key.get(raw.lower())
            if not key:                       # вдруг модель всё же отдала id
                key = raw if raw in {k for k, _n, _c in items} else None
            if not key:
                continue
            out[key] = {"kind": (x.get("kind") or "work").lower(),
                        "what": x.get("what") or ""}
        return out

    attempts = [("https://api.deepseek.com/chat/completions", key, "deepseek-v4-flash")]
    ex_key, ex_url = _llm_env("EXCASH_API_KEY"), _llm_env("EXCASH_API_URL")
    if ex_key and ex_url:
        attempts.append((ex_url.rstrip("/") + "/chat/completions", ex_key,
                         "gemini-3.7-flash-tiered"))
    acc = {}
    for url, api_key, model in attempts:
        try:
            got = _try(url, api_key, model)
        except Exception as e:
            print(f"(классификатор {model}: {type(e).__name__} — пробую резерв)")
            continue
        acc.update(got or {})
        if len(acc) >= len(items):
            return acc
        items = [it for it in items if it[0] not in acc]   # доспрашиваем только пропущенных
    if acc:
        # часть чатов модель молча пропустила — недостача уходит в «дела»,
        # лучше лишний пункт в брифе, чем потерянный долг
        miss = [k for k, _n, _c in items if k not in acc]
        if miss:
            print(f"(классификатор пропустил {len(miss)} чат(ов) — считаю их делами)")
        return acc
    print("(классификатор не ответил — показываю всё подряд)")
    return {}


def _state_load():
    """{ключ: {"sig","kind","what","since"}} — вердикты прошлых прогонов.

    Нужна ради ПОВТОРНЫХ напоминаний: без неё классификатор каждое утро судил
    заново и мог перекинуть тот же чат из «дела» в «бытовое» — долг молча
    пропадал из брифа. Пока в чате нет нового сообщения, вердикт не пересматриваем.
    """
    try:
        import json
        with open(STATE_FILE, encoding="utf-8") as fh:
            return json.load(fh) or {}
    except Exception:
        return {}


def _state_save(st):
    try:
        import json, os
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        tmp = STATE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(st, fh, ensure_ascii=False, indent=1)
        os.replace(tmp, STATE_FILE)
    except Exception:
        pass


def cmd_pending(a):
    """Долги доктора: кто ждёт ответа и что он сам обещал.

    Висят, ПОКА НЕ ЗАКРЫТЫ: счётчик дней растёт, окно --days. Снимается либо
    ответом доктора (тогда последним пишет он и чат уходит из списка), либо
    явной командой dismiss.

    30.08: личные чаты берём отфильтрованными на стороне шлюза. Раньше лимит
    скана съедали каналы и группы (на 120 диалогов приходилось 5 личных), и в
    бриф попадала случайная горстка долгов — каждый день разная.
    """
    me = gw("/me") or {}
    my_id, my_user = me.get("id"), (me.get("username") or "").lower()
    dismissed = _load_dismissed()
    state = _state_load()
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=a.days)

    def _days(iso):
        try:
            return (now - datetime.fromisoformat(iso)).total_seconds() / 86400
        except Exception:
            return 0.0

    def _ago(iso):
        d = _days(iso)
        return f"{int(d * 24)} ч" if d < 1 else f"{int(d)} дн"

    def _msgs(dl, limit):
        return gw("/read", params={"chat": str(dl["id"]), "limit": limit}).get("messages") or []

    def _ctx_text(msgs, chat_name):
        out = []
        for m in msgs[::-1]:
            who = "ВРАЧ" if m.get("out") else (m.get("sender") or chat_name)[:20]
            t = (m.get("text") or m.get("caption") or "[медиа]").replace("\n", " ")[:200]
            out.append(f"{who}: {t}")
        return "\n".join(out)

    def _dump(msgs, chat_name):
        for m in msgs[::-1]:
            who = "ДОКТОР" if m.get("out") else (m.get("sender") or chat_name)[:22]
            txt = (m.get("text") or m.get("caption") or "[медиа]").replace("\n", " ")[:230]
            mark = "  ⚑ОБЕЩАНИЕ" if (m.get("out") and any(p in txt.lower() for p in _PROMISE)) else ""
            print(f"    {_when(m.get('date'))} {who}: {txt}{mark}")

    # --- 1. личные чаты: фильтрует шлюз, лимит тратится только на них
    personal_raw = gw("/dialogs", params={"kinds": "user", "days": a.days,
                                          "limit": a.scan}).get("dialogs") or []
    personal, promises, hidden = [], [], 0
    for dl in personal_raw:
        if _noisy(dl.get("name") or ""):
            continue
        if _is_hidden(dl, dismissed):
            hidden += 1
            continue
        if dl.get("out") is False:
            personal.append(dl)
        else:
            # последним писал доктор — но если он ОБЕЩАЛ и ответа нет, долг за ним
            ms = _msgs(dl, 4)
            for m in ms:
                if not m.get("out"):
                    break                              # собеседник уже отреагировал
                if any(p in (m.get("text") or "").lower() for p in _PROMISE):
                    promises.append(dl)
                    break

    # --- 2. группы: там ищем прямые обращения к доктору
    group_hits, skipped_mass = [], 0
    for dl in (gw("/dialogs", params={"limit": a.gscan}).get("dialogs") or []):
        if not dl.get("is_group") or _noisy(dl.get("name") or ""):
            continue
        if _is_hidden(dl, dismissed):
            hidden += 1
            continue
        try:
            when = datetime.fromisoformat(dl["date"]) if dl.get("date") else None
        except Exception:
            when = None
        if when and when < cutoff:
            continue
        if (dl.get("unread") or 0) > a.mass:
            skipped_mass += 1
            continue
        msgs = _msgs(dl, a.ctx * 3)
        for m in msgs:
            if m.get("out"):
                continue
            t = (m.get("text") or "").lower()
            if (my_user and ("@" + my_user) in t) or \
               str(m.get("reply_to_user_id") or "") == str(my_id) or \
               _OWNER_NAME in t:
                group_hits.append((dl, msgs, m))
                break

    personal.sort(key=lambda x: x.get("date") or "")     # самые старые сверху
    promises.sort(key=lambda x: x.get("date") or "")

    # --- 3. вердикты: из памяти, если в чате ничего не изменилось
    items = [(str(dl["id"]), dl) for dl in personal] + \
            [("p" + str(dl["id"]), dl) for dl in promises]
    ctx_cache, to_class, fresh = {}, [], {}
    for key, dl in items:
        sig = dl.get("date") or ""
        old = state.get(key) or {}
        if not a.recheck and old.get("sig") == sig and old.get("kind"):
            fresh[key] = {"kind": old["kind"], "what": old.get("what", ""),
                          "since": old.get("since") or now.isoformat()}
            continue
        ms = _msgs(dl, a.ctx)
        ctx_cache[key] = ms
        to_class.append((key, dl["name"], _ctx_text(ms, dl["name"])))

    verdict = {} if a.no_filter else _classify_debts(to_class)
    for key, dl in items:
        if key in fresh:
            continue
        v = verdict.get(key, {})
        kind = v.get("kind") or ("promise" if key.startswith("p") else "work")
        prev = state.get(key) or {}
        # since — когда долг ВПЕРВЫЕ попал в бриф, а не дата письма: иначе счётчик
        # «напоминаю N-й день» врал бы про напоминания, которых не было
        fresh[key] = {"kind": kind, "what": v.get("what", ""),
                      "since": prev.get("since") or now.isoformat()}

    # память: держим только живые долги, закрытые забываем
    _state_save({k: {"sig": dict(items)[k].get("date") or "", **fresh[k]} for k, _ in items})

    work, prom, social = [], [], []
    for key, dl in items:
        f = fresh[key]
        row = (key, dl, f.get("what", ""), f.get("since") or now.isoformat())
        (prom if f["kind"] == "promise" else social if f["kind"] == "social" else work).append(row)

    def _section(title, rows, note=""):
        if not rows:
            return
        print(f"\n## {title} ({len(rows)})")
        if note:
            print(note)
        print()
        shown = rows[: a.max]
        for key, dl, what, since in shown:
            d = _days(dl.get("date"))
            flag = "  🔥 ВИСИТ ДАВНО" if d >= 3 else ""
            unread = f", непрочитано {dl['unread']}" if dl.get("unread") else ""
            nth = int(_days(since)) + 1
            rep = f"  [напоминаю {nth}-й день]" if nth > 1 else ""
            print(f"### {dl['name']} — ждёт {_ago(dl.get('date'))}{unread}{flag}{rep}")
            if what:
                print(f"    СУТЬ: {what}")
            ms = ctx_cache.get(key) or _msgs(dl, a.ctx)
            _dump(ms, dl["name"])
            print()
        rest = rows[a.max:]
        if rest:
            # ничего не теряем молча: хвост — строкой, но с именами и сроками
            tail = "; ".join(f"{dl['name']} ({_ago(dl.get('date'))}"
                             + (f", {what[:60]}" if what else "") + ")"
                             for _, dl, what, _s in rest)
            print(f"ЕЩЁ ЖДУТ ({len(rest)}) — тоже незакрыты, покажи списком: {tail}\n")

    print(f"\nОТФИЛЬТРОВАНО: дела {len(work)}, обещания {len(prom)}, бытовое {len(social)} "
          f"(личных чатов просмотрено {len(personal_raw)})")
    _section("ТРЕБУЮТ РЕШЕНИЯ", work,
             "(профессиональные запросы, пациенты, деньги, документы, договорённости)")
    _section("ОБЕЩАЛ И НЕ ЗАКРЫЛ", prom, "(слова самого доктора)")

    if social:
        names = ", ".join(dl["name"] for _, dl, _, _ in social[:8])
        more = f" и ещё {len(social) - 8}" if len(social) > 8 else ""
        print(f"\n## Бытовое — без действия ({len(social)}): {names}{more}")
        print("   (шутки, реакции, «как дела» — доктору решать нечего, показано одной строкой)")

    if group_hits:
        print(f"\n## В ГРУППАХ ОБРАТИЛИСЬ К ДОКТОРУ ({len(group_hits)})\n")
        for dl, msgs, hit in group_hits[: a.max]:
            print(f"### {dl['name']} — обращение {_ago(hit.get('date'))} назад")
            print(f"    ОБРАЩЕНИЕ: {(hit.get('sender') or '?')}: {(hit.get('text') or '')[:230]}")
            _dump(msgs[: a.ctx], dl["name"])
            print()

    if not (work or prom or group_hits):
        print("\nДолгов по делу нет: всё, что требовало решения, закрыто.")
    if hidden:
        print(f"(скрыто по просьбе доктора: {hidden} чат(ов))")
    if skipped_mass:
        print(f"(пропущено массовых чатов-болталок: {skipped_mass})")

    print("\n" + "=" * 62)
    print("Как подавать доктору: каждый пункт — КТО, СКОЛЬКО ЖДЁТ, ЧЕГО хотят. "
          "Висящее 3+ дня выделяй. Строку ЕЩЁ ЖДУТ не выбрасывай — это тоже "
          "незакрытые долги, дай их перечнем. ⚑ОБЕЩАНИЕ — слова самого доктора: "
          "проверь по переписке, закрыто ли, и напоминай только про незакрытое. "
          "Доктор решает, что оставить: «забей» -> dismiss \"<чат>\" (вернётся, если "
          "человек напишет новое); «напомни через неделю» -> --days 7; «убери совсем» "
          "-> --forever; «верни» -> --days 0; «что я замял» -> dismissed. "
          "Всё через sudo /root/yoda_tg.sh. Сам ничего не замалчивай и за доктора не отвечай.")


def cmd_send(a):
    """Отправка от имени доктора. Требует --confirm yes."""
    if a.confirm != "yes":
        sys.exit("ОТКАЗ: отправка требует --confirm yes. Сначала покажи доктору получателя и "
                 "полный текст, получи явное согласие.")
    target = _resolve_strict(a.chat)
    gw("/send", method="POST", json={"chat": target, "text": a.text})
    with open("/root/yoda_tg_sent.log", "a", encoding="utf-8") as fh:
        fh.write(f"{datetime.now():%Y-%m-%d %H:%M} | {a.chat} | {a.text[:200]}\n")
    _bot_copy(a.chat, a.text)
    print(f"OK ОТПРАВЛЕНО в [{a.chat}]: {a.text[:120]}")


def cmd_sendfile(a):
    """Отправка файла от имени доктора. Требует --confirm yes."""
    if a.confirm != "yes":
        sys.exit("ОТКАЗ: отправка требует --confirm yes.")
    if not os.path.exists(a.path):
        sys.exit(f"файл не найден: {a.path}")
    target = _resolve_strict(a.chat)
    gw("/sendfile", method="POST", json={"chat": target, "path": a.path, "caption": a.caption})
    with open("/root/yoda_tg_sent.log", "a", encoding="utf-8") as fh:
        fh.write(f"file | {a.chat} | {os.path.basename(a.path)}\n")
    _bot_copy(a.chat, f"[файл] {a.path}")
    print(f"OK ОТПРАВЛЕН ФАЙЛ в [{a.chat}]: {os.path.basename(a.path)}")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("dialogs"); d.add_argument("--n", type=int, default=25)
    r = sub.add_parser("read"); r.add_argument("chat"); r.add_argument("--n", type=int, default=30)
    s = sub.add_parser("search"); s.add_argument("query"); s.add_argument("--chat")
    s.add_argument("--n", type=int, default=20)
    md = sub.add_parser("media"); md.add_argument("chat"); md.add_argument("--n", type=int, default=5)
    dg = sub.add_parser("digest"); dg.add_argument("--folder", action="append")
    dg.add_argument("--hours", type=int, default=24)
    pn = sub.add_parser("pending", help="неотвеченное и обещанное")
    pn.add_argument("--days", type=int, default=30, help="окно активности чатов")
    pn.add_argument("--scan", type=int, default=400, help="сколько ЛИЧНЫХ чатов взять")
    pn.add_argument("--gscan", type=int, default=200, help="сколько диалогов смотреть ради групп")
    pn.add_argument("--max", type=int, default=12, help="сколько чатов раскрыть подробно")
    pn.add_argument("--recheck", action="store_true",
                    help="пересудить все чаты заново, игнорируя память вердиктов")
    pn.add_argument("--ctx", type=int, default=8, help="сообщений контекста на чат")
    pn.add_argument("--no-filter", action="store_true",
                    help="показать всё без разделения на дела/болтовню")
    pn.add_argument("--mass", type=int, default=300,
                    help="группы с непрочитанным выше этого — считать болталкой")

    ds = sub.add_parser("dismiss", help="убрать чат из напоминаний")
    ds.add_argument("chat")
    ds.add_argument("--days", type=int, default=None,
                    help="отложить на N дней (0 — вернуть в список). "
                         "Без флага — замять до следующего сообщения от человека")
    ds.add_argument("--forever", action="store_true")

    sub.add_parser("dismissed", help="что сейчас замято и как вернуть")
    sn = sub.add_parser("send"); sn.add_argument("chat"); sn.add_argument("text")
    sn.add_argument("--confirm", default="no")
    sf = sub.add_parser("sendfile"); sf.add_argument("chat"); sf.add_argument("path")
    sf.add_argument("--caption", default=""); sf.add_argument("--confirm", default="no")
    a = ap.parse_args()

    {"dialogs": cmd_dialogs, "read": cmd_read, "search": cmd_search, "media": cmd_media,
     "digest": cmd_digest, "pending": cmd_pending, "dismiss": cmd_dismiss,
     "dismissed": cmd_dismissed,
     "send": cmd_send, "sendfile": cmd_sendfile}[a.cmd](a)


if __name__ == "__main__":
    main()
