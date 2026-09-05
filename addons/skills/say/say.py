#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Голос ассистента: синтез речи (piper, локально) и отправка голосового владельцу в Telegram.

  say.py "текст"                       — озвучить и отправить владельцу (язык по тексту)
  say.py "testo" --lang it             — итальянский голос (paola); --lang ru — русский (dmitri)
  say.py --file /путь/текст.txt        — длинный текст из файла
  say.py "текст" --keep                — не отправлять, только сохранить .ogg и напечатать путь
  say.py --voices                      — какие голоса установлены

Голоса piper лежат в /opt/piper-voices (ru: dmitri, it: paola). Нет голоса для языка —
резерв edge-tts (нужен интернет), если он установлен в venv.
Кому отправлять: OWNER_TG_ID в ~/.openclaw/.env.
"""
import argparse
import glob
import os
import re
import shutil
import subprocess
import sys
import tempfile

import requests

HOME = os.path.expanduser("~")
CFG = f"{HOME}/.openclaw/openclaw.json"
VOICES_DIR = "/opt/piper-voices"
PIPER_VOICES = {"ru": "ru_RU-dmitri-medium", "it": "it_IT-paola-medium"}
EDGE_VOICES = {"ru": "ru-RU-DmitryNeural", "it": "it-IT-ElsaNeural", "en": "en-GB-RyanNeural"}
MAX_CHARS = 2500


def _env():
    e = {}
    p = f"{HOME}/.openclaw/.env"
    if os.path.exists(p):
        for line in open(p, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                e[k.strip()] = v.strip().strip('"').strip("'")
    return e


E = _env()
OWNER = E.get("OWNER_TG_ID", "")


def _bin(name):
    for cand in (f"{HOME}/mailvenv/bin/{name}", f"{HOME}/venv/bin/{name}", shutil.which(name)):
        if cand and os.path.exists(cand):
            return cand
    return None


def token():
    s = open(CFG, encoding="utf-8").read()
    m = re.search(r'botToken"\s*:\s*"([0-9]+:[A-Za-z0-9_-]+)"', s)
    if not m:
        sys.exit("botToken не найден в openclaw.json")
    return m.group(1)


def clean(t):
    t = re.sub(r"https?://\S+", "", t)
    t = re.sub(r"[*_`#>\[\]()|]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t[:MAX_CHARS]


def detect_lang(t):
    if re.search(r"[А-Яа-яЁё]", t):
        return "ru"
    if re.search(r"\b(il|la|di|che|non|per|una|sono|ciao|come|con|del|della|molto|bene|grazie)\b", t.lower()):
        return "it"
    return "en" if re.search(r"\b(the|and|you|with|this|that)\b", t.lower()) else "it"


def synth_piper(text, lang):
    model = os.path.join(VOICES_DIR, PIPER_VOICES.get(lang, "") + ".onnx")
    piper = _bin("piper")
    if not (piper and os.path.exists(model)):
        return None
    wav = tempfile.mktemp(suffix=".wav")
    p = subprocess.run([piper, "-m", model, "-f", wav], input=text.encode("utf-8"),
                       capture_output=True, timeout=300)
    return wav if p.returncode == 0 and os.path.exists(wav) else None


def synth_edge(text, lang):
    edge = _bin("edge-tts")
    voice = EDGE_VOICES.get(lang)
    if not (edge and voice):
        return None
    mp3 = tempfile.mktemp(suffix=".mp3")
    p = subprocess.run([edge, "--voice", voice, "--text", text, "--write-media", mp3],
                       capture_output=True, timeout=120)
    return mp3 if p.returncode == 0 and os.path.exists(mp3) else None


def to_ogg(src):
    ogg = tempfile.mktemp(suffix=".ogg")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", src, "-c:a", "libopus", "-b:a", "48k",
                    "-ac", "1", ogg], check=True, timeout=300)
    os.remove(src)
    return ogg


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("text", nargs="?")
    ap.add_argument("--file")
    ap.add_argument("--lang", default="auto", help="ru | it | en | auto")
    ap.add_argument("--keep", action="store_true")
    ap.add_argument("--voices", action="store_true")
    a = ap.parse_args()
    if a.voices:
        have = {k: os.path.exists(os.path.join(VOICES_DIR, v + ".onnx")) for k, v in PIPER_VOICES.items()}
        print({"piper": _bin("piper"), "piper_voices": have, "edge_tts": bool(_bin("edge-tts")), "owner_set": bool(OWNER)})
        return
    text = open(a.file, encoding="utf-8").read() if a.file else (a.text or "")
    text = clean(text)
    if not text:
        sys.exit("пустой текст")
    lang = a.lang if a.lang != "auto" else detect_lang(text)
    src = synth_piper(text, lang) or synth_edge(text, lang)
    if not src:
        sys.exit(f"нет голоса для языка «{lang}»: ни piper-модели в {VOICES_DIR}, ни edge-tts")
    ogg = to_ogg(src)
    if a.keep:
        print(ogg)
        return
    if not OWNER:
        sys.exit(f"не задан OWNER_TG_ID в ~/.openclaw/.env — файл сохранён: {ogg}")
    with open(ogg, "rb") as f:
        r = requests.post(f"https://api.telegram.org/bot{token()}/sendVoice",
                          data={"chat_id": OWNER}, files={"voice": ("voice.ogg", f)}, timeout=120)
    os.remove(ogg)
    if not r.ok:
        sys.exit(f"Telegram: {r.status_code} {r.text[:200]}")
    print(f"отправлено голосовое ({lang}, {len(text)} симв.)")


if __name__ == "__main__":
    main()
