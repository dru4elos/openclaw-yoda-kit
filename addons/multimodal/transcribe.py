#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""STT для Йоды. Каскад (быстрый → качественный), чтобы голосовые не висели:
  1) GigaAM локально (127.0.0.1:8001) — RU-native, мгновенно, без интернета
  2) Groq whisper-large-v3-turbo — быстрый облачный резерв
  3) excash Gemini 3.1 Pro — качественный, но бывает медленным (последним)

OpenClaw вызывает: transcribe.py <путь>  (плейсхолдер {{MediaPath}}).
Печатает расшифровку в stdout. Жёсткие таймауты на каждом шаге.
"""
import base64, os, subprocess, sys, tempfile
import requests

DEBUG = "/tmp/tr_debug.log"
def dbg(msg):
    try:
        with open(DEBUG, "a", encoding="utf-8") as f:
            f.write(str(msg) + "\n")
    except Exception:
        pass

dbg(f"START argv={sys.argv}")

ENV = {}
_p = os.path.expanduser("~/.openclaw/.env")
if os.path.exists(_p):
    for line in open(_p, encoding="utf-8"):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            ENV[k.strip()] = v.strip().strip('"').strip("'")

GIGAAM_URL = ENV.get("GIGAAM_ASR_URL", "http://127.0.0.1:8001/v1/audio/transcriptions")
GROQ_KEY = ENV.get("GROQ_API_KEY", "")
EXCASH_KEY, EXCASH_URL = ENV.get("EXCASH_API_KEY", ""), ENV.get("EXCASH_API_URL", "")
AUDIO_EXT = (".ogg", ".oga", ".opus", ".wav", ".mp3", ".m4a", ".webm", ".flac", ".aac")

def to_wav(path):
    """OGG/Opus -> WAV 16k mono. Без ffmpeg вернём как есть."""
    if path.lower().endswith(".wav"):
        return path, False
    try:
        out = tempfile.mktemp(suffix=".wav")
        subprocess.run(["ffmpeg", "-y", "-i", path, "-ar", "16000", "-ac", "1", out],
                       check=True, capture_output=True, timeout=60)
        return out, True
    except Exception as e:
        dbg(f"ffmpeg fail: {e}")
        return path, False

def try_gigaam(wav):
    r = requests.post(GIGAAM_URL, files={"file": ("a.wav", open(wav, "rb"), "audio/wav")},
                      data={"model": "gigaam", "language": "ru"}, timeout=90)
    r.raise_for_status()
    try:
        return (r.json().get("text") or "").strip()
    except Exception:
        return r.text.strip()

def try_groq(wav):
    if not GROQ_KEY:
        return ""
    r = requests.post("https://api.groq.com/openai/v1/audio/transcriptions",
                      headers={"Authorization": "Bearer " + GROQ_KEY},
                      files={"file": ("a.wav", open(wav, "rb"), "audio/wav")},
                      data={"model": "whisper-large-v3-turbo", "language": "ru",
                            "response_format": "json"}, timeout=90)
    r.raise_for_status()
    return (r.json().get("text") or "").strip()

def try_excash(wav):
    if not (EXCASH_KEY and EXCASH_URL):
        return ""
    b64 = base64.b64encode(open(wav, "rb").read()).decode("ascii")
    r = requests.post(EXCASH_URL.rstrip("/") + "/chat/completions",
                      headers={"Authorization": "Bearer " + EXCASH_KEY, "Content-Type": "application/json"},
                      json={"model": "gemini-3.1-pro", "temperature": 0.1, "max_tokens": 16000,
                            "messages": [{"role": "user", "content": [
                                {"type": "input_audio", "input_audio": {"data": b64, "format": "wav"}},
                                {"type": "text", "text": "Расшифруй это голосовое дословно на языке оригинала. "
                                                         "Выведи ТОЛЬКО текст расшифровки."}]}]},
                      timeout=110)
    r.raise_for_status()
    msg = ((r.json().get("choices") or [{}])[0] or {}).get("message") or {}
    c = msg.get("content")
    if isinstance(c, list):
        c = "\n".join(x.get("text", "") for x in c if isinstance(x, dict))
    return (c or "").strip()

def main():
    audio = next((a for a in sys.argv[1:] if os.path.exists(a) and a.lower().endswith(AUDIO_EXT)), None)
    if not audio:
        audio = next((a for a in sys.argv[1:] if os.path.exists(a)), None)
    tmp_in = None
    if not audio:
        data = sys.stdin.buffer.read() if not sys.stdin.isatty() else b""
        if not data:
            dbg("НЕ НАШЁЛ АУДИО")
            sys.exit("нет аудио: ни файла-аргумента, ни stdin")
        audio = tmp_in = tempfile.mktemp(suffix=".ogg")
        with open(audio, "wb") as f:
            f.write(data)

    wav, converted = to_wav(audio)
    for name, fn in (("gigaam", try_gigaam), ("groq", try_groq), ("excash", try_excash)):
        try:
            txt = fn(wav)
            if txt and txt.strip():
                dbg(f"OK via {name}: {len(txt)} симв")
                print(txt.strip())
                break
            dbg(f"{name}: пусто")
        except Exception as e:
            dbg(f"{name} fail: {type(e).__name__}: {str(e)[:120]}")
    else:
        dbg("ВСЕ ASR НЕ СРАБОТАЛИ")
        sys.exit("не удалось расшифровать аудио")

    for p in (wav if converted else None, tmp_in):
        try:
            if p and os.path.exists(p):
                os.remove(p)
        except Exception:
            pass

main()
