#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""webinar.py — вебинар/конференция под ключ, без импровизации агента.

  plan      --title T --url U --start "DD.MM HH:MM" --end "HH:MM" [--register-url R] [--focus "…"] [--dry-run]
            → один вызов ставит все кроны: (регистрация) → вход и запись за 10 мин → контроль звука
              через 20 мин → финиш через 12 мин после конца (командный крон, без агента)
  finish    --out DIR [--title T]     → стоп записи → звук → GigaAM v3 по кускам → умная чистка →
                                        подробный конспект → доставка владельцу. Идемпотентно (state.json).
  transcribe / clean / summarize / notify --out DIR   → отдельные стадии того же финиша
  selftest  [--minutes N]             → прогон финиша на куске прошлой записи (проверка контура)
  status    --out DIR

Расшифровка: GigaAM v3 (Сбер, gigaam-v3-e2e-rnnt, контейнер 127.0.0.1:8001) — основной,
excash/Gemini — резерв на кусок. Чистка огрехов ASR: gemini-3.8-flash. Конспект: gpt-6-astra-1m.
"""
import argparse
import base64
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests

HOME = os.path.expanduser("~")
OC = f"{HOME}/.openclaw"
SKILLS = f"{OC}/workspace/skills"
WEBREC = f"{SKILLS}/webrec/webrec.py"
SELF = os.path.abspath(__file__)
PY = sys.executable
MSK = ZoneInfo("Europe/Moscow")
EVENTS_DIR = f"{OC}/workspace/Эфиры"
SEG_SEC = 600                                   # кусок для GigaAM, сек
SILENCE_DB = -55.0
CLEAN_MODELS = ["gemini-3.8-flash", "gpt-5.6-sol-1m", "gemini-3.7-flash-tiered"]
SUM_MODELS = ["gpt-6-astra-1m", "gpt-5.6-sol-1m", "gemini-3.8-flash"]


def _env():
    e = {}
    p = f"{OC}/.env"
    if os.path.exists(p):
        for line in open(p, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                e[k.strip()] = v.strip().strip('"').strip("'")
    return e


E = _env()
EXCASH_URL = E.get("EXCASH_API_URL", "").rstrip("/")
EXCASH_KEY = E.get("EXCASH_API_KEY", "")
GIGAAM = E.get("GIGAAM_ASR_URL", "http://127.0.0.1:8001/v1/audio/transcriptions")
OWNER = E.get("OWNER_TG_ID", "")


# ---------- служебное ----------
def log(out, msg):
    line = f"[{datetime.now(MSK).strftime('%d.%m %H:%M:%S')}] {msg}"
    print(line, flush=True)
    try:
        with open(os.path.join(out, "pipeline.log"), "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception:
        pass


def st_load(out):
    p = os.path.join(out, "state.json")
    try:
        return json.load(open(p, encoding="utf-8"))
    except Exception:
        return {"chunks": {}}


def st_save(out, st):
    json.dump(st, open(os.path.join(out, "state.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def sh(cmd, timeout=None, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, **kw)


def hms(sec):
    sec = int(sec)
    return f"{sec // 3600:02d}:{sec % 3600 // 60:02d}:{sec % 60:02d}"


def duration(path):
    r = sh(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path])
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


def loudness(path):
    r = sh(["ffmpeg", "-hide_banner", "-i", path, "-af", "volumedetect", "-vn", "-f", "null", "-"])
    mean = None
    for line in (r.stderr or "").splitlines():
        if "mean_volume" in line:
            mean = float(line.split("mean_volume:")[1].split("dB")[0])
    return mean


def bot_token():
    try:
        c = json.load(open(f"{OC}/openclaw.json", encoding="utf-8"))
        t = c["channels"]["telegram"]["botToken"]
    except Exception:
        return ""
    if t.startswith("${") and t.endswith("}"):
        t = E.get(t[2:-1], "")
    return t


def tg_send(text):
    tok = bot_token()
    if not (tok and OWNER):
        print("(telegram: нет токена или OWNER_TG_ID — не отправляю)")
        return False
    r = requests.post(f"https://api.telegram.org/bot{tok}/sendMessage",
                      json={"chat_id": OWNER, "text": text[:4000], "disable_web_page_preview": True}, timeout=60)
    return r.ok


def tg_doc(path, caption=""):
    tok = bot_token()
    if not (tok and OWNER and os.path.exists(path)):
        return False
    with open(path, "rb") as fh:
        r = requests.post(f"https://api.telegram.org/bot{tok}/sendDocument",
                          data={"chat_id": OWNER, "caption": caption[:1000]},
                          files={"document": (os.path.basename(path), fh)}, timeout=300)
    return r.ok


def llm(models, messages, max_tokens, temperature=0.2, timeout=900):
    """Первая живая модель из списка; пустой ответ = ошибка (агрегатор так «падает»)."""
    if not (EXCASH_URL and EXCASH_KEY):
        raise RuntimeError("нет EXCASH_API_URL/EXCASH_API_KEY в ~/.openclaw/.env")
    last = ""
    for model in models:
        for attempt in (1, 2):
            try:
                r = requests.post(f"{EXCASH_URL}/chat/completions",
                                  headers={"Authorization": f"Bearer {EXCASH_KEY}"},
                                  json={"model": model, "messages": messages, "temperature": temperature,
                                        "max_tokens": max_tokens, "stream": False}, timeout=timeout)
                if r.status_code != 200:
                    last = f"{model}: HTTP {r.status_code} {r.text[:120]}"
                    time.sleep(5 * attempt)
                    continue
                d = r.json()
                text = ((d.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
                if text.strip():
                    return text.strip(), model
                last = f"{model}: пустой ответ"
            except Exception as ex:
                last = f"{model}: {ex}"
            time.sleep(5 * attempt)
    raise RuntimeError("все модели отказали: " + last)


# ---------- стадии ----------
def find_full(out):
    fulls = sorted(glob.glob(os.path.join(out, "*_full.mp4")), key=os.path.getmtime)
    return fulls[-1] if fulls else None


def stage_stop(out, name):
    """Останавливает webrec (если ещё пишет) и склеивает сегменты. Возвращает путь к видео."""
    full = find_full(out)
    pf = os.path.join(out, ".webrec.pid")
    if full and not os.path.exists(pf):
        return full
    cmd = [PY, WEBREC, "stop", "--out", out] + (["--name", name] if name else [])
    r = sh(cmd, timeout=1200)
    log(out, "webrec stop: " + (r.stdout or "").strip()[-400:] + (r.stderr or "").strip()[-200:])
    return find_full(out)


def stage_transcribe(out, video, seg):
    st = st_load(out)
    wav = os.path.join(out, "audio_16k.wav")
    if not os.path.exists(wav):
        r = sh(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", video, "-vn", "-ac", "1",
                "-ar", "16000", "-c:a", "pcm_s16le", wav], timeout=1800)
        if r.returncode != 0:
            raise RuntimeError("ffmpeg не вытащил звук: " + (r.stderr or "")[-300:])
    cdir = os.path.join(out, "chunks")
    chunks = sorted(glob.glob(os.path.join(cdir, "chunk_*.wav")))
    if not chunks:
        os.makedirs(cdir, exist_ok=True)
        r = sh(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", wav, "-f", "segment",
                "-segment_time", str(seg), "-c", "copy", os.path.join(cdir, "chunk_%03d.wav")], timeout=1800)
        chunks = sorted(glob.glob(os.path.join(cdir, "chunk_*.wav")))
    log(out, f"кусков для GigaAM: {len(chunks)} по {seg} с")
    for i, ch in enumerate(chunks):
        key = str(i)
        if key in st["chunks"] and st["chunks"][key].get("text") is not None:
            continue
        start = i * seg
        text, engine = None, None
        t0 = time.time()
        for attempt in range(1, 4):
            try:
                with open(ch, "rb") as fh:
                    r = requests.post(GIGAAM, files={"file": (os.path.basename(ch), fh, "audio/wav")},
                                      data={"model": "gigaam", "language": "ru"}, timeout=1800)
                if r.status_code == 200:
                    text, engine = (r.json().get("text") or "").strip(), "gigaam-v3"
                    break
                log(out, f"кусок {i}: GigaAM HTTP {r.status_code} (попытка {attempt})")
            except Exception as ex:
                log(out, f"кусок {i}: GigaAM {ex} (попытка {attempt})")
            time.sleep(20 * attempt)
        if text is None:
            try:
                text, engine = excash_chunk(ch), "excash-gemini"
                log(out, f"кусок {i}: расшифрован резервом (Gemini через excash)")
            except Exception as ex:
                text, engine = "", f"failed: {ex}"
                log(out, f"кусок {i}: НЕ расшифрован: {ex}")
        st["chunks"][key] = {"i": i, "start": start, "end": start + int(duration(ch)), "text": text,
                             "engine": engine, "sec": round(time.time() - t0)}
        st_save(out, st)
        log(out, f"кусок {i} [{hms(start)}]: {len(text)} симв., {engine}, {round(time.time() - t0)} с")
    write_raw(out, st)
    return st


def excash_chunk(ch):
    mp3 = ch[:-4] + ".mp3"
    sh(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", ch, "-b:a", "48k", mp3], timeout=600)
    b64 = base64.b64encode(open(mp3, "rb").read()).decode()
    text, _ = llm(["gemini-3.8-flash", "gemini-3.7-flash-tiered"], [{"role": "user", "content": [
        {"type": "text", "text": "Расшифруй эту запись дословно на русском. Только текст речи, без комментариев."},
        {"type": "input_audio", "input_audio": {"data": b64, "format": "mp3"}}]}], max_tokens=16000, timeout=900)
    return text


def write_raw(out, st):
    with open(os.path.join(out, "transcript_raw.md"), "w", encoding="utf-8") as fh:
        fh.write("# Сырая расшифровка (GigaAM v3, без правки)\n\n")
        for k in sorted(st["chunks"], key=int):
            c = st["chunks"][k]
            fh.write(f"**[{hms(c['start'])} – {hms(c['end'])}]** ({c['engine']})\n\n{c['text'] or '[тишина / неразборчиво]'}\n\n")


CLEAN_PROMPT = """Ты редактор стенограмм. Ниже — сырой текст автоматического распознавания речи (GigaAM) одного фрагмента вебинара «{title}» ({span}). Сделай из него читаемую стенограмму:
- исправь ошибки распознавания: перепутанные слова, термины, имена, названия компаний и продуктов, числа, англицизмы (пиши их как принято: API, SaaS, LTV, FDA…);
- расставь пунктуацию и абзацы; убери слова-паразиты и запинки-повторы;
- сохрани смысл и ВСЕ факты, цифры, примеры и формулировки говорящего — не сокращай и не пересказывай;
- ничего не выдумывай: непонятное место помечай [неразборчиво];
- если говорят разные люди, начинай реплику каждого с новой строки.
Верни ТОЛЬКО текст стенограммы, без вступлений и пояснений.
{context}
Сырой текст фрагмента:
\"\"\"
{raw}
\"\"\""""


def stage_clean(out, title):
    st = st_load(out)
    prev_tail = ""
    used = set()
    for k in sorted(st["chunks"], key=int):
        c = st["chunks"][k]
        if c.get("clean") is not None:
            prev_tail = c["clean"][-600:]
            continue
        raw = (c.get("text") or "").strip()
        if len(raw) < 20:
            c["clean"] = "[тишина / неразборчиво]"
            st_save(out, st)
            continue
        ctx = f"Для связности — конец предыдущего фрагмента (уже выправлен, НЕ повторяй его):\n«…{prev_tail}»\n" if prev_tail else ""
        span = f"{hms(c['start'])} – {hms(c['end'])}"
        t0 = time.time()
        text, model = llm(CLEAN_MODELS, [{"role": "user", "content": CLEAN_PROMPT.format(
            title=title, span=span, context=ctx, raw=raw)}], max_tokens=16000)
        c["clean"], c["clean_model"] = text, model
        used.add(model)
        prev_tail = text[-600:]
        st_save(out, st)
        log(out, f"чистка {k} [{span}]: {len(raw)}→{len(text)} симв., {model}, {round(time.time() - t0)} с")
    with open(os.path.join(out, "transcript.md"), "w", encoding="utf-8") as fh:
        fh.write(f"# Стенограмма «{title}»\n\n_Распознано GigaAM v3 (Сбер), огрехи распознавания выправлены "
                 f"{', '.join(sorted(used)) or 'ранее'}. Таймкоды — от начала записи._\n\n")
        for k in sorted(st["chunks"], key=int):
            c = st["chunks"][k]
            fh.write(f"**[{hms(c['start'])} – {hms(c['end'])}]**\n\n{c.get('clean') or '[тишина / неразборчиво]'}\n\n")
    return st


SUM_PROMPT = """Ниже — стенограмма вебинара/конференции «{title}» с таймкодами. Составь ПОДРОБНЫЙ структурированный конспект на русском (Markdown). Читатель — {reader}. Он не смотрел эфир и хочет получить из конспекта всё содержательное, чтобы не пересматривать запись.
{focus}
Структура:
1. **О чём эфир** — 3–5 предложений: тема, кто выступал (имена/роли, если названы), формат, главный посыл.
2. **Подробный конспект по разделам** — раздел на каждую смысловую часть, в заголовке таймкод начала. Внутри: тезисы, аргументы, цифры, примеры, кейсы, названия инструментов и компаний, точные формулировки говорящего там, где они важны (в кавычках). Ничего существенного не пропускай — это главный раздел, он должен быть длинным.
3. **Ключевые выводы** — 7–15 пунктов, конкретно.
4. **Практическое: что применить читателю** — конкретные шаги и идеи, привязанные к его делам.
5. **Вопросы из чата и ответы спикера** — если были.
6. **Ссылки, контакты, упомянутые ресурсы, сроки, цены, условия** — списком, дословно.
7. **Что осталось неясным или помечено [неразборчиво]** — коротко.
Пиши плотно и точно, без воды и общих фраз. Не выдумывай того, чего нет в стенограмме.

Стенограмма:
\"\"\"
{text}
\"\"\""""

READER = E.get("WEBINAR_READER", "владелец ассистента; ценит конкретику, цифры и применимые идеи")


def stage_summarize(out, title, focus=""):
    text = open(os.path.join(out, "transcript.md"), encoding="utf-8").read()
    body = text.split("\n\n", 2)[-1]
    foc = f"Особый интерес читателя: {focus}.\n" if focus else ""
    t0 = time.time()
    if len(body) > 600000:
        half = len(body) // 2
        cut = body.rfind("\n**[", 0, half)
        parts = [body[:cut], body[cut:]]
        partial = []
        for i, p in enumerate(parts, 1):
            s, _ = llm(SUM_MODELS, [{"role": "user", "content": SUM_PROMPT.format(
                title=f"{title} (часть {i} из 2)", reader=READER, focus=foc, text=p)}], max_tokens=32000)
            partial.append(s)
        summary, model = llm(SUM_MODELS, [{"role": "user", "content":
            f"Объедини два конспекта частей одного эфира «{title}» в один цельный подробный конспект той же структуры, "
            f"без потерь содержания и без повторов:\n\nЧАСТЬ 1:\n{partial[0]}\n\nЧАСТЬ 2:\n{partial[1]}"}], max_tokens=32000)
    else:
        summary, model = llm(SUM_MODELS, [{"role": "user", "content": SUM_PROMPT.format(
            title=title, reader=READER, focus=foc, text=body)}], max_tokens=32000)
    with open(os.path.join(out, "summary.md"), "w", encoding="utf-8") as fh:
        fh.write(f"# Конспект: {title}\n\n_Составлен по стенограмме GigaAM v3, модель {model}. Файлы: {out}_\n\n{summary}\n")
    log(out, f"конспект: {len(summary)} симв., {model}, {round(time.time() - t0)} с")
    return model


def stage_notify(out, title, video, mean_db, test=False):
    st = st_load(out)
    engines = {c.get("engine") for c in st["chunks"].values()}
    n = len(st["chunks"])
    bad = sum(1 for c in st["chunks"].values() if str(c.get("engine", "")).startswith("failed"))
    dur = duration(video) if video else 0
    sound = "есть" if (mean_db is not None and mean_db > SILENCE_DB) else f"НЕТ ({mean_db} дБ)"
    head = "[ТЕСТ контура] " if test else ""
    lines = [f"{head}🎓 {title}",
             f"Запись: {hms(dur)}, звук: {sound}",
             f"Стенограмма: {n} фрагм., GigaAM v3" + (" + резерв Gemini" if "excash-gemini" in engines else "")
             + (f", НЕ расшифровано: {bad}" if bad else ""),
             f"Конспект: {st.get('summary_model', '?')}",
             f"Папка: {out}"]
    if video:
        lines.append(f"Видео: {os.path.basename(video)} ({os.path.getsize(video) // 1048576} МБ) — на сервере")
    ok = tg_send("\n".join(lines))
    ok &= tg_doc(os.path.join(out, "summary.md"), f"{head}Конспект: {title}")
    ok &= tg_doc(os.path.join(out, "transcript.md"), f"{head}Стенограмма: {title}")
    log(out, f"доставка владельцу: {'ok' if ok else 'ОШИБКА'}")
    return ok


def cmd_finish(a):
    out = os.path.abspath(a.out)
    os.makedirs(out, exist_ok=True)
    plan = {}
    try:
        plan = json.load(open(os.path.join(out, "plan.json"), encoding="utf-8"))
    except Exception:
        pass
    title = a.title or plan.get("title") or os.path.basename(out)
    name = a.name or plan.get("slug")
    st = st_load(out)
    log(out, f"=== finish: {title} ===")
    video = stage_stop(out, name)
    if not video:
        log(out, "записи нет — сегментов не найдено")
        tg_send(f"❌ {title}: запись не найдена в {out} — расшифровывать нечего.")
        sys.exit(2)
    mean_db = st.get("mean_db")
    if mean_db is None:
        mean_db = loudness(video)
        st["mean_db"], st["video"] = mean_db, video
        st_save(out, st)
    log(out, f"видео: {video}, {hms(duration(video))}, mean {mean_db} дБ")
    if mean_db is None or mean_db <= -70:
        tg_send(f"{'[ТЕСТ контура] ' if a.test else ''}⚠️ {title}: запись немая (mean {mean_db} дБ) — звук не записался, "
                f"расшифровки не будет. Видео: {video}")
        sys.exit(3)
    st = stage_transcribe(out, video, a.segment)
    spoken = sum(len(c.get("text") or "") for c in st["chunks"].values())
    if spoken < 200:
        log(out, f"речи не распознано (всего {spoken} симв.) — конспекта не будет")
        tg_send(f"{'[ТЕСТ контура] ' if a.test else ''}⚠️ {title}: звук в записи есть (mean {mean_db} дБ), но речи GigaAM "
                f"не распознал ({spoken} симв.) — похоже, писался не эфир (тон, музыка, пустая страница). Видео: {video}")
        sys.exit(4)
    stage_clean(out, title)
    st = st_load(out)
    if not st.get("summary_model") or a.force:
        st["summary_model"] = stage_summarize(out, title, a.focus or plan.get("focus", ""))
        st_save(out, st)
    if not a.no_notify:
        stage_notify(out, title, video, mean_db, test=a.test)
    log(out, "=== готово ===")


# ---------- планировщик ----------
def _openclaw():
    w = shutil.which("openclaw")
    if w:
        return w
    c = sorted(glob.glob(f"{HOME}/.nvm/versions/node/*/bin/openclaw"))
    return c[-1] if c else "openclaw"


def _slug(title):
    tr = dict(zip("абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
                  ["a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "y", "k", "l", "m", "n", "o", "p", "r", "s", "t",
                   "u", "f", "h", "c", "ch", "sh", "sch", "", "y", "", "e", "yu", "ya"]))
    s = "".join(tr.get(ch, ch) for ch in title.lower())
    s = re.sub(r"[^a-z0-9]+", "_", s).strip("_")
    return s[:32] or "efir"


def _parse_when(start, end):
    now = datetime.now(MSK)
    d, t = start.split()
    dd, mm = map(int, d.split("."))
    hh, mi = map(int, t.split(":"))
    s = now.replace(month=mm, day=dd, hour=hh, minute=mi, second=0, microsecond=0)
    if s < now - timedelta(hours=1):
        s = s.replace(year=s.year + 1)
    eh, em = map(int, end.split(":"))
    e = s.replace(hour=eh, minute=em)
    if e <= s:
        e += timedelta(days=1)
    return s, e


def cmd_plan(a):
    s, e = _parse_when(a.start, a.end)
    slug = _slug(a.title)
    out = a.out or os.path.join(EVENTS_DIR, f"{s.strftime('%Y-%m-%d')}_{slug}")
    os.makedirs(out, exist_ok=True)
    oc = _openclaw()
    owner = OWNER or "<OWNER_TG_ID>"
    until = (e + timedelta(minutes=10)).strftime("%H:%M")
    common_agent = ["--agent", "background", "--session", "isolated", "--announce", "--channel", "telegram",
                    "--to", owner, "--delete-after-run"]

    def iso(dt_):
        return dt_.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    jobs = []
    if a.register_url:
        jobs.append(("Эфир: регистрация — " + a.title, iso(datetime.now(MSK) + timedelta(minutes=2)), 900, [
            "--message",
            f"Зарегистрируй владельца на «{a.title}» ({s.strftime('%d.%m %H:%M')} МСК): {a.register_url}. "
            f"Действуй по скиллу webinar, раздел «Регистрация»: форма — в браузере (профиль по умолчанию), данные "
            f"владельца из USER.md, только обязательные поля; письмо-подтверждение — mail.py, ссылку подтверждения открой. "
            f"Регистрация через Telegram-бота организатора — за владельца нажать нельзя: отправь ему ссылку и попроси "
            f"переслать тебе ссылку на трансляцию. Итог одной строкой: зарегистрирован/что мешает, ссылка на трансляцию "
            f"(если появилась — положи её в {out}/plan.json в поле url)."]))
    jobs.append(("Эфир: вход и запись — " + a.title, iso(s - timedelta(minutes=10)), 1500, [
        "--message",
        f"Эфир «{a.title}», начало {s.strftime('%H:%M')} МСК. Ссылка: {a.url or '— возьми из ' + out + '/plan.json (поле url)'}. "
        f"Строго по скиллу webrec, браузер ТОЛЬКО с профилем rec (profile: \"rec\"):\n"
        f"1) открой ссылку, войди как участник (без камеры и микрофона; имя — владельца из USER.md).\n"
        f"2) Как только видно плеер/спикера: {PY} {WEBREC} start --out \"{out}\" --name {slug} --until {until}\n"
        f"3) {PY} {WEBREC} unmute ; потом {PY} {WEBREC} probe — нужно sound: true. Тишина → повтори unmute (до 3 раз), "
        f"проверь, что вкладка эфира активна.\n"
        f"4) Доложи одной строкой: вошёл/нет, recording: true/false, звук есть/нет. Не запускай selftest, других сайтов "
        f"в профиле rec не открывай, конца эфира не жди и не поллируй — обработку делает отдельный крон."]))
    jobs.append(("Эфир: контроль звука — " + a.title, iso(s + timedelta(minutes=20)), 600, [
        "--message",
        f"Контроль записи эфира «{a.title}»: {PY} {WEBREC} status --out \"{out}\" и {PY} {WEBREC} probe. "
        f"Если recording: false — запусти {PY} {WEBREC} start --out \"{out}\" --name {slug} --until {until} "
        f"(вкладка эфира в профиле rec должна быть открыта). Если тишина — {PY} {WEBREC} unmute и снова probe. "
        f"Доложи одной строкой."]))
    finish_cmd = f"{PY} {SELF} finish --out \"{out}\""
    jobs.append(("Эфир: обработка — " + a.title, iso(e + timedelta(minutes=12)), 10800, [
        "--command", finish_cmd, "--command-cwd", out]))

    plan = {"title": a.title, "slug": slug, "url": a.url, "register_url": a.register_url, "focus": a.focus,
            "start": s.isoformat(), "end": e.isoformat(), "out": out, "jobs": []}
    for name, at, timeout, payload in jobs:
        cmd = [oc, "cron", "add", "--name", name, "--at", at, "--timeout-seconds", str(timeout)]
        cmd += common_agent if "--message" in payload else ["--agent", "background", "--delete-after-run",
                                                             "--announce", "--channel", "telegram", "--to", owner]
        cmd += payload
        if a.dry_run:
            print("DRY:", " ".join(f'"{c}"' if " " in c else c for c in cmd)[:400], "…")
            continue
        r = sh(cmd, timeout=90)
        m = re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", (r.stdout or "") + (r.stderr or ""))
        jid = m.group(0) if m else None
        plan["jobs"].append({"name": name, "at_utc": at, "id": jid, "ok": r.returncode == 0,
                             "err": (r.stderr or "").strip()[-200:] if r.returncode else ""})
        print(f"{'✓' if r.returncode == 0 else '✗'} {name} @ {at} {jid or (r.stderr or '')[-160:]}")
    json.dump(plan, open(os.path.join(out, "plan.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(json.dumps({"out": out, "slug": slug, "start_msk": s.strftime("%d.%m %H:%M"), "end_msk": e.strftime("%H:%M"),
                      "jobs": len(plan["jobs"]), "failed": sum(1 for j in plan["jobs"] if not j["ok"])}, ensure_ascii=False))


def cmd_status(a):
    out = os.path.abspath(a.out)
    st = st_load(out)
    print(json.dumps({"out": out, "video": find_full(out), "recording": os.path.exists(os.path.join(out, ".webrec.pid")),
                      "chunks": len(st.get("chunks", {})),
                      "cleaned": sum(1 for c in st.get("chunks", {}).values() if c.get("clean")),
                      "summary": os.path.exists(os.path.join(out, "summary.md")),
                      "summary_model": st.get("summary_model"), "mean_db": st.get("mean_db")}, ensure_ascii=False))


def cmd_selftest(a):
    out = os.path.join(EVENTS_DIR, "_selftest")
    shutil.rmtree(out, ignore_errors=True)
    os.makedirs(out)
    clip = os.path.join(out, "selftest_full.mp4")
    cands = [a.source] if a.source else sorted(
        glob.glob(f"{OC}/workspace/*/*_full.mp4") + glob.glob(f"{OC}/workspace/*/*_efir.mp4")
        + glob.glob(f"{OC}/workspace/*/*_[0-9]*.mp4") + glob.glob(f"{EVENTS_DIR}/*/*_full.mp4"),
        key=os.path.getsize, reverse=True)
    src = None
    for cand in cands:                       # нужен кусок СО ЗВУКОМ, немые сегменты прошлых эфиров бывают
        if os.path.abspath(cand).startswith(out):
            continue
        r = sh(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", str(a.offset), "-i", cand, "-t",
                str(a.minutes * 60), "-c", "copy", clip], timeout=600)
        if r.returncode != 0 or duration(clip) < 30:
            continue
        m = loudness(clip)
        words = 0
        if m is not None and m > -50:              # громко ≠ речь (тон тестовой страницы тоже −13 дБ)
            probe_wav = os.path.join(out, "probe.wav")
            sh(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", clip, "-t", "25", "-vn", "-ac", "1",
                "-ar", "16000", probe_wav], timeout=120)
            try:
                with open(probe_wav, "rb") as fh:
                    words = len((requests.post(GIGAAM, files={"file": ("p.wav", fh, "audio/wav")},
                                               data={"model": "gigaam", "language": "ru"}, timeout=300)
                                 .json().get("text") or "").split())
            except Exception as ex:
                print("GigaAM probe:", ex)
        print(f"кандидат {os.path.basename(cand)}: mean {m} дБ, слов в первых 25 с: {words}")
        if words >= 5:
            src = cand
            break
    if not src:
        sys.exit("не нашёл записи со звуком для селфтеста — укажи --source")
    print(f"источник: {src} → {a.minutes} мин с {a.offset} с")
    ns = argparse.Namespace(out=out, title="Проверка контура вебинаров", name="selftest", segment=a.segment,
                            focus="", force=True, no_notify=a.no_notify, test=True)
    cmd_finish(ns)
    st = st_load(out)
    print(json.dumps({"chunks": {k: (v["engine"], v["sec"], len(v["text"]), len(v.get("clean") or "")) for k, v in st["chunks"].items()},
                      "summary_model": st.get("summary_model"), "summary_chars": os.path.getsize(os.path.join(out, "summary.md"))},
                     ensure_ascii=False))


def main():
    ap = argparse.ArgumentParser(description="Вебинар под ключ")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan"); p.add_argument("--title", required=True); p.add_argument("--url", default="")
    p.add_argument("--start", required=True, help='"DD.MM HH:MM" МСК'); p.add_argument("--end", required=True, help='"HH:MM" МСК')
    p.add_argument("--register-url", default=""); p.add_argument("--focus", default=""); p.add_argument("--out", default="")
    p.add_argument("--dry-run", action="store_true"); p.set_defaults(func=cmd_plan)
    for name in ("finish", "transcribe", "clean", "summarize", "notify"):
        q = sub.add_parser(name); q.add_argument("--out", required=True); q.add_argument("--title", default="")
        q.add_argument("--name", default=""); q.add_argument("--segment", type=int, default=SEG_SEC)
        q.add_argument("--focus", default=""); q.add_argument("--force", action="store_true")
        q.add_argument("--no-notify", action="store_true"); q.add_argument("--test", action="store_true")
        q.set_defaults(func={"finish": cmd_finish,
                             "transcribe": lambda a: stage_transcribe(os.path.abspath(a.out), find_full(os.path.abspath(a.out)), a.segment),
                             "clean": lambda a: stage_clean(os.path.abspath(a.out), a.title or os.path.basename(a.out)),
                             "summarize": lambda a: stage_summarize(os.path.abspath(a.out), a.title or os.path.basename(a.out), a.focus),
                             "notify": lambda a: stage_notify(os.path.abspath(a.out), a.title or os.path.basename(a.out),
                                                              find_full(os.path.abspath(a.out)), st_load(os.path.abspath(a.out)).get("mean_db"), a.test)}[name])
    s = sub.add_parser("status"); s.add_argument("--out", required=True); s.set_defaults(func=cmd_status)
    t = sub.add_parser("selftest"); t.add_argument("--minutes", type=int, default=6); t.add_argument("--offset", type=int, default=60)
    t.add_argument("--segment", type=int, default=180); t.add_argument("--source", default=""); t.add_argument("--no-notify", action="store_true")
    t.set_defaults(func=cmd_selftest)
    a = ap.parse_args()
    a.func(a)


if __name__ == "__main__":
    main()
