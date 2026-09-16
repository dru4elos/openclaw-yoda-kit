#!/usr/bin/env python3
"""webrec — запись экрана и звука виртуального браузера (Xvfb :99 + PulseAudio «webrec»).

  webrec.py start  --out ДИР --name ИМЯ [--duration СЕК | --until HH:MM] [--segment 600]
  webrec.py status --out ДИР
  webrec.py stop   --out ДИР           # аккуратно завершает ffmpeg, склеивает, проверяет
  webrec.py check  ФАЙЛ.mp4            # длительность, потоки, громкость звука
  webrec.py selftest                    # 12-секундная запись тестовой страницы
  webrec.py open   --url URL [--close-others]   # открыть комнату в браузере записи НАПРЯМУЮ, не браузерным инструментом
  webrec.py unmute [--out ДИР | --url URL]      # снять паузу/mute с плееров комнаты
  webrec.py watch  --out ДИР [--every 60]       # сторож записи: комната, экран, звук (start запускает сам)
  webrec.py probe  [--seconds 6]        # есть ли звук в карте ПРЯМО СЕЙЧАС

Пишет сегментами (по умолчанию 10 мин): упавший ffmpeg теряет не всё, а один
кусок. Браузер должен рисовать в :99 и играть звук в sink «webrec» — это делает
обёртка chrome-rec, через которую OpenClaw запускает Chrome.
"""
import argparse
import datetime as dt
import json
import os
import shutil
import signal
import subprocess
import sys
import time

DISPLAY = ":99"
SINK = "webrec"
SIZE = "1280x720"
FPS = "15"
MSK = dt.timezone(dt.timedelta(hours=3))


def _env():
    e = dict(os.environ)
    uid = os.getuid()
    e["XDG_RUNTIME_DIR"] = e.get("XDG_RUNTIME_DIR") or f"/run/user/{uid}"
    e["PULSE_SERVER"] = f"unix:{e['XDG_RUNTIME_DIR']}/pulse/native"
    e["DISPLAY"] = DISPLAY
    return e


def _sh(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, env=_env(), **kw)


def preflight():
    """Экран и звук должны быть живы ДО записи — иначе ffmpeg молча пишет чёрное/тишину."""
    probs = []
    if _sh(["xdpyinfo", "-display", DISPLAY]).returncode != 0:
        probs.append(f"нет экрана {DISPLAY} — systemctl --user start xvfb-webrec")
    r = _sh(["pactl", "list", "sinks", "short"])
    if r.returncode != 0 or SINK not in r.stdout:
        probs.append(f"нет звуковой карты «{SINK}» — systemctl --user start pulse-webrec")
    if _sh(["which", "ffmpeg"]).returncode != 0:
        probs.append("нет ffmpeg")
    return probs


def _pidfile(out):
    return os.path.join(out, ".webrec.pid")


CDP_PORT = 18801
HERE = os.path.dirname(os.path.abspath(__file__))
TOME = os.path.join(os.path.dirname(HERE), "tome", "tome.py")
WEBINAR = os.path.join(os.path.dirname(HERE), "webinar", "webinar.py")


def _plan(out):
    try:
        return json.load(open(os.path.join(out, "plan.json"), encoding="utf-8"))
    except Exception:
        return {}


def _cdp(path, method="GET", port=CDP_PORT, timeout=15):
    import urllib.request
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method=method)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode()


def _cdp_tabs(port=CDP_PORT):
    return [t for t in json.loads(_cdp("/json", port=port)) if t.get("type") == "page"]


def _room_key(url):
    from urllib.parse import urlsplit
    u = urlsplit(url or "")
    host = u.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    return host, u.path.rstrip("/")


def find_room(tabs, url):
    """Вкладка комнаты по адресу из плана: точное совпадение хоста и пути, иначе тот же хост."""
    want = _room_key(url)
    if not want[0]:
        return None
    exact = [t for t in tabs if _room_key(t.get("url")) == want]
    if exact:
        return exact[0]
    same_host = [t for t in tabs if _room_key(t.get("url"))[0] == want[0]]
    return same_host[0] if same_host else None


def _tome(text):
    if os.path.exists(TOME):
        subprocess.run([sys.executable, TOME, "msg", text], capture_output=True, timeout=90)


ROOM_FILE = ".webrec.room"
PLAYING_JS = ("(() => [...document.querySelectorAll('video,audio')].some("
              "m => !m.paused && !m.ended && m.readyState > 2))()")


def _room_load(out):
    try:
        return json.load(open(os.path.join(out, ROOM_FILE), encoding="utf-8"))
    except Exception:
        return {}


def _room_save(out, tab):
    try:
        json.dump({"id": tab["id"], "url": tab.get("url") or "", "at": dt.datetime.now(MSK).strftime("%H:%M:%S")},
                  open(os.path.join(out, ROOM_FILE), "w", encoding="utf-8"), ensure_ascii=False)
    except Exception:
        pass


def _playing(tab, timeout=4):
    """Играет ли во вкладке видео или звук прямо сейчас (элементы верхнего документа)."""
    try:
        import asyncio, websockets
    except ImportError:
        return False

    async def run():
        async with websockets.connect(tab["webSocketDebuggerUrl"], max_size=None,
                                      open_timeout=timeout, close_timeout=2) as ws:
            await ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate",
                                      "params": {"expression": PLAYING_JS, "returnByValue": True}}))
            while True:
                m = json.loads(await ws.recv())
                if m.get("id") == 1:
                    return bool(m.get("result", {}).get("result", {}).get("value"))
    try:
        return asyncio.run(asyncio.wait_for(run(), timeout=timeout + 2))
    except Exception:
        return False


def resolve_room(out, tabs=None):
    """Какая вкладка — комната. Сначала та, что запомнили при входе и старте записи: адрес внутри
    может меняться (редирект, «Подключиться», переход на другую страницу). Если её нет или в ней
    тихо, а звук играет в другой вкладке — комната переехала туда.

    16.09 на репетиции сторож искал комнату по адресу из плана; агент перешёл в той же вкладке
    на другой адрес, сторож решил, что комнаты нет, вывел на экран сломанную вкладку и поднял
    ложную тревогу."""
    tabs = tabs if tabs is not None else _cdp_tabs()
    http = [t for t in tabs if (t.get("url") or "").startswith("http")]
    rec = _room_load(out) if out else {}
    by_id = next((t for t in http if t["id"] == rec.get("id")), None)
    if by_id:
        if _playing(by_id):
            return by_id, "id"
        moved = [t for t in http if t["id"] != by_id["id"] and _playing(t)]
        return (moved[0], "звук переехал") if len(moved) == 1 else (by_id, "id")
    playing = [t for t in http if _playing(t)]
    if rec.get("id"):                     # комнату знали, её вкладки больше нет
        return (playing[0], "звук переехал") if len(playing) == 1 else (None, "")
    if len(playing) == 1:
        return playing[0], "звук"
    url = _plan(out).get("url") if out else ""
    by_url = find_room(http, url) if url else None
    if by_url:
        return by_url, "адрес"
    if playing:
        return playing[0], "звук"
    if len(http) == 1:
        return http[0], "единственная"
    return None, ""


def _alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def cmd_start(a):
    probs = preflight()
    if probs:
        sys.exit("НЕ СТАРТУЮ:\n  " + "\n  ".join(probs))
    os.makedirs(a.out, exist_ok=True)
    pf = _pidfile(a.out)
    if os.path.exists(pf):
        try:
            old = int(open(pf).read().strip())
            if _alive(old):
                sys.exit(f"уже идёт запись (pid {old}) — сначала stop")
        except ValueError:
            pass
    seconds = None
    if a.until:
        hh, mm = map(int, a.until.split(":"))
        now = dt.datetime.now(MSK)
        end = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if end <= now:
            end += dt.timedelta(days=1)
        seconds = int((end - now).total_seconds())
    elif a.duration:
        seconds = int(a.duration)
    pattern = os.path.join(a.out, f"{a.name}_%Y%m%d_%H%M%S.mp4")
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "warning", "-y",
           "-f", "x11grab", "-framerate", FPS, "-video_size", SIZE, "-i", DISPLAY,
           "-f", "pulse", "-i", f"{SINK}.monitor",
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "26", "-pix_fmt", "yuv420p",
           "-g", "30",
           "-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2",
           "-f", "segment", "-segment_time", str(a.segment), "-reset_timestamps", "1",
           "-strftime", "1", pattern]
    if seconds:
        cmd[1:1] = ["-t", str(seconds)]     # общий лимит; segment режет внутри
    log = open(os.path.join(a.out, "webrec.log"), "a")
    p = subprocess.Popen(cmd, stdout=log, stderr=log, env=_env(),
                         start_new_session=True)
    open(pf, "w").write(str(p.pid))
    time.sleep(3)
    if p.poll() is not None:
        sys.exit("ffmpeg упал сразу — смотри " + os.path.join(a.out, "webrec.log"))
    focus = ""
    try:                                      # на экране должна быть комната: 16.09 писалась пустая «New Tab»
        room, how = resolve_room(a.out)
        if room:
            _cdp(f"/json/activate/{room['id']}")
            _room_save(a.out, room)
            focus = f"{(room.get('url') or '')[:70]} ({how})"
        else:
            focus = "вкладка комнаты не найдена"
    except Exception as e:
        focus = f"не удалось: {type(e).__name__}"
    watch_pid = None
    if not getattr(a, "no_watch", False):
        wl = open(os.path.join(a.out, "watch.log"), "a")
        w = subprocess.Popen([sys.executable, os.path.abspath(__file__), "watch", "--out", a.out],
                             stdout=wl, stderr=wl, env=_env(), start_new_session=True)
        open(os.path.join(a.out, ".webrec.watch.pid"), "w").write(str(w.pid))
        watch_pid = w.pid
    print(json.dumps({"ok": True, "pid": p.pid, "out": a.out, "name": a.name,
                      "limit_sec": seconds, "segment_sec": a.segment,
                      "started": dt.datetime.now(MSK).strftime("%H:%M:%S"),
                      "room_on_screen": focus or "адрес комнаты в plan.json не задан",
                      "watch_pid": watch_pid},
                     ensure_ascii=False))


def _segments(out, name=None):
    fs = sorted(f for f in os.listdir(out)
                if f.endswith(".mp4") and (name is None or f.startswith(name + "_"))
                and "_full" not in f)
    return [os.path.join(out, f) for f in fs]


def probe(path):
    r = _sh(["ffprobe", "-v", "error", "-show_entries",
             "format=duration,size:stream=codec_type,codec_name", "-of", "json", path])
    try:
        j = json.loads(r.stdout)
    except Exception:
        return {"ok": False, "err": (r.stderr or "")[-200:]}
    st = j.get("streams", [])
    d = float(j.get("format", {}).get("duration") or 0)
    return {"ok": True, "duration_sec": round(d, 1),
            "size_mb": round(int(j.get("format", {}).get("size") or 0) / 1e6, 1),
            "video": any(s.get("codec_type") == "video" for s in st),
            "audio": any(s.get("codec_type") == "audio" for s in st)}


def loudness(path):
    """mean_volume ниже −55 дБ = записалась тишина. Проверять ОБЯЗАТЕЛЬНО."""
    r = _sh(["ffmpeg", "-hide_banner", "-i", path, "-af", "volumedetect",
             "-vn", "-f", "null", "-"])
    out = r.stderr or ""
    mean = max_ = None
    for line in out.splitlines():
        if "mean_volume" in line:
            mean = float(line.split("mean_volume:")[1].split("dB")[0])
        if "max_volume" in line:
            max_ = float(line.split("max_volume:")[1].split("dB")[0])
    return mean, max_


def cmd_check(a):
    p = probe(a.file)
    print("файл:", a.file)
    print(json.dumps(p, ensure_ascii=False))
    if not p.get("ok"):
        sys.exit(1)
    mean, mx = loudness(a.file)
    verdict = ("ЗВУК ЕСТЬ" if (mean is not None and mean > -55) else
               "ТИШИНА — звук не записался" if mean is not None else "громкость не измерена")
    print(f"громкость: mean {mean} dB, max {mx} dB → {verdict}")
    if not p.get("video"):
        print("ВИДЕО НЕТ")
    ok = p.get("video") and p.get("audio") and mean is not None and mean > -55 and p["duration_sec"] > 1
    print("ИТОГ:", "OK" if ok else "БРАК")
    sys.exit(0 if ok else 2)


def cmd_status(a):
    pf = _pidfile(a.out)
    pid = None
    if os.path.exists(pf):
        try:
            pid = int(open(pf).read().strip())
        except ValueError:
            pass
    segs = _segments(a.out)
    info = {"recording": bool(pid and _alive(pid)), "pid": pid, "segments": len(segs)}
    if segs:
        last = segs[-1]
        info["last_segment"] = os.path.basename(last)
        info["last_segment_mb"] = round(os.path.getsize(last) / 1e6, 1)
        info["last_write_sec_ago"] = int(time.time() - os.path.getmtime(last))
        info["total_mb"] = round(sum(os.path.getsize(s) for s in segs) / 1e6, 1)
    print(json.dumps(info, ensure_ascii=False))


def cmd_stop(a):
    pf = _pidfile(a.out)
    pid = None
    if os.path.exists(pf):
        try:
            pid = int(open(pf).read().strip())
        except ValueError:
            pass
    if pid and _alive(pid):
        os.kill(pid, signal.SIGINT)          # ffmpeg дописывает контейнер корректно
        for _ in range(60):
            if not _alive(pid):
                break
            time.sleep(1)
        else:
            os.kill(pid, signal.SIGKILL)
        print("ffmpeg остановлен")
    else:
        print("запись не шла (pid нет) — только склеиваю то, что есть")
    if os.path.exists(pf):
        os.remove(pf)
    segs = _segments(a.out, a.name)
    if not segs:
        sys.exit("сегментов нет — записи не было")
    name = a.name or os.path.basename(segs[0]).rsplit("_", 2)[0]
    full = os.path.join(a.out, f"{name}_full.mp4")
    lst = os.path.join(a.out, ".concat.txt")
    with open(lst, "w") as fh:
        for s in segs:
            fh.write("file '%s'\n" % s.replace("'", r"'\''"))
    r = _sh(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "concat",
             "-safe", "0", "-i", lst, "-c", "copy", full])
    if r.returncode != 0:
        print("склейка не удалась:", (r.stderr or "")[-300:])
        full = segs[-1]
    p = probe(full)
    mean, mx = loudness(full)
    print(json.dumps({"segments": len(segs), "full": full, **p,
                      "mean_db": mean, "max_db": mx,
                      "sound": (mean is not None and mean > -55)}, ensure_ascii=False))
    if not (p.get("audio") and mean is not None and mean > -55):
        print("ВНИМАНИЕ: звука в записи нет или он на уровне тишины")


def _screen_busy():
    """Есть ли на :99 чужой Chrome (браузер OpenClaw) или идущая запись.

    02.09.2026 селфтест запустили во время живого эфира: его окно легло ПОВЕРХ
    вебинара, агент упал и не убрал его — 47 минут записи ушли на тестовую
    страницу с тоном. Больше селфтест на занятом экране не стартует."""
    r = _sh(["pgrep", "-u", str(os.getuid()), "-f", "remote-debugging-port=18801"])
    if r.returncode == 0 and r.stdout.strip():
        return "на :99 уже работает браузер OpenClaw, профиль rec (порт 18801) — селфтест лёг бы поверх него"
    r = _sh(["pgrep", "-u", str(os.getuid()), "-f", "x11grab"])
    if r.returncode == 0 and r.stdout.strip():
        return "идёт запись (ffmpeg x11grab) — селфтест испортил бы её"
    return None


def cmd_selftest(a):
    """Полный круг без OpenClaw: свой Chrome на тестовой странице → 12 с записи → проверка."""
    probs = preflight()
    if probs:
        sys.exit("селфтест невозможен:\n  " + "\n  ".join(probs))
    busy = _screen_busy()
    if busy and not a.force:
        sys.exit("СЕЛФТЕСТ ОТМЕНЁН: " + busy + ". Экран и звук живы (preflight OK) — этого достаточно. "
                 "Принудительно: --force, но НЕ во время эфира.")
    here = os.path.dirname(os.path.abspath(__file__))
    page = os.path.join(here, "webrec_test.html")
    out = "/tmp/webrec_selftest"
    subprocess.run(["rm", "-rf", out]); os.makedirs(out)
    prof = "/tmp/webrec_selftest_profile"
    subprocess.run(["rm", "-rf", prof])
    chrome = subprocess.Popen([os.path.expanduser("~/bin/chrome-rec"),
                               "--user-data-dir=" + prof, "--no-first-run",
                               "--remote-debugging-port=18899", "file://" + page],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                              env=_env(), start_new_session=True)
    time.sleep(6)
    try:
        ns = argparse.Namespace(out=out, name="selftest", duration=12, until=None, segment=600)
        cmd_start(ns)
        time.sleep(15)
        ns2 = argparse.Namespace(out=out, name="selftest")
        cmd_stop(ns2)
    finally:
        # Chrome селфтеста убираем ВСЕГДА, даже если упали посередине —
        # иначе его окно остаётся поверх экрана и попадает в чужую запись
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(os.getpgid(chrome.pid), sig)
            except Exception:
                pass
            time.sleep(1)
        subprocess.run(["pkill", "-u", str(os.getuid()), "-f", "user-data-dir=" + prof])
    full = os.path.join(out, "selftest_full.mp4")
    print("файл селфтеста:", full)


def cmd_probe(a):
    """Живой замер монитора карты: есть ли звук ПРЯМО СЕЙЧАС, не дожидаясь сегмента.

    02.09 запись выглядела здоровой (поток в карту есть, кадр есть), а сегмент
    оказался немым: плееры комнаты стояли на паузе. Проверять надо сам звук."""
    probs = preflight()
    if probs:
        sys.exit("НЕ МОГУ: " + "; ".join(probs))
    r = _sh(["ffmpeg", "-hide_banner", "-f", "pulse", "-i", f"{SINK}.monitor",
             "-t", str(a.seconds), "-af", "volumedetect", "-f", "null", "-"])
    mean = None
    for line in (r.stderr or "").splitlines():
        if "mean_volume" in line:
            mean = float(line.split("mean_volume:")[1].split("dB")[0])
    ok = mean is not None and mean > -55
    print(json.dumps({"sound": ok, "mean_db": mean, "seconds": a.seconds,
                      "verdict": "звук идёт" if ok else "ТИШИНА — включи плееры (webrec unmute) и проверь вкладку"},
                     ensure_ascii=False))
    sys.exit(0 if ok else 2)


def cmd_unmute(a):
    """Снять паузу и mute со всех <video>/<audio> во вкладке эфира через CDP.

    Комнаты вебинаров (Pruffme и др.) часто стартуют плееры на паузе, пока
    пользователь не кликнет. Runtime.evaluate с userGesture=true даёт браузеру
    «жест пользователя», и play() со звуком проходит."""
    import urllib.request
    try:
        import asyncio, websockets
    except ImportError:
        sys.exit("нужен пакет websockets (есть в ~/mailvenv) — запускай ~/mailvenv/bin/python")
    tabs = [t for t in json.load(urllib.request.urlopen(f"http://127.0.0.1:{a.port}/json"))
            if t.get("type") == "page"]
    # 16.09: вкладку искали по слову «webinar» в адресе — у Телемоста его нет, unmute не сработал ни разу
    cands = []
    if a.out:
        try:
            room, _how = resolve_room(a.out, tabs)
            cands = [room] if room else []
        except Exception:
            cands = []
    if not cands and a.url:
        room = find_room(tabs, a.url)
        cands = [room] if room else []
    if not cands and a.match:
        cands = [t for t in tabs if a.match in (t.get("url") or "")]
    if not cands:
        cands = [t for t in tabs if (t.get("url") or "").startswith("http")]
    if not cands:
        sys.exit(f"в браузере записи нет открытой комнаты (порт {a.port}): "
                 + ", ".join((t.get("url") or "")[:50] for t in tabs))
    STATE = ("(() => [...document.querySelectorAll('video,audio')].map((m,i)=>({i,tag:m.tagName,"
             "paused:m.paused,muted:m.muted,vol:m.volume})))()")
    FIX = """(async () => { const out=[];
      for (const m of document.querySelectorAll('video,audio')) {
        try { m.muted=false; m.volume=1; if (m.paused) await m.play(); out.push({paused:m.paused,muted:m.muted}); }
        catch(e) { out.push({err:String(e).slice(0,60)}); } }
      try { if (window.AudioContext) { const c=new AudioContext(); await c.resume(); } } catch(e){}
      return out; })()"""

    async def run(tab):
        # жёсткие таймауты: 02.09 подключение к вкладке зависло и утащило за собой
        # весь вызов — агент бы ждал вечно
        async with websockets.connect(tab["webSocketDebuggerUrl"], max_size=None,
                                      open_timeout=15, close_timeout=5) as ws:
            async def ev(i, expr, gesture=False):
                await ws.send(json.dumps({"id": i, "method": "Runtime.evaluate",
                                          "params": {"expression": expr, "returnByValue": True,
                                                     "awaitPromise": True, "userGesture": gesture}}))
                while True:
                    m = json.loads(await ws.recv())
                    if m.get("id") == i:
                        return m.get("result", {}).get("result", {}).get("value")
            before = await ev(1, STATE)
            await ev(2, FIX, gesture=True)
            await asyncio.sleep(2)
            after = await ev(3, STATE)
            print(json.dumps({"tab": (tab.get("url") or "")[:80], "before": before, "after": after},
                             ensure_ascii=False)[:900])
    failed = []
    for tab in cands:
        try:
            asyncio.run(asyncio.wait_for(run(tab), timeout=45))
        except Exception as e:
            failed.append("%s: %s" % (type(e).__name__, str(e)[:80]))
    if failed and len(failed) == len(cands):
        sys.exit("unmute не удался: " + "; ".join(failed))


def cmd_open(a):
    """Открыть комнату в браузере записи через CDP, а не браузерным инструментом OpenClaw.

    16.09 комнату открыл крон через browser open. Такие вкладки OpenClaw считает своими и
    закрывает, когда сессия крона кончается: через 9 минут звонок исчез, и 63 минуты
    писалась тишина. Вкладку, открытую напрямую, OpenClaw не трогает."""
    from urllib.parse import quote
    try:
        tabs = _cdp_tabs(a.port)
    except Exception:
        # браузер записи запускает шлюз OpenClaw; после перезапуска шлюза его может не быть
        import glob as _glob
        oc = shutil.which("openclaw") or (sorted(_glob.glob(os.path.expanduser("~/.nvm/versions/node/*/bin/openclaw"))) or ["openclaw"])[-1]
        env = dict(os.environ)
        env["PATH"] = os.path.dirname(oc) + ":" + env.get("PATH", "")
        subprocess.run([oc, "browser", "--browser-profile", "rec", "start"], capture_output=True, text=True,
                       timeout=120, env=env)
        try:
            tabs = _cdp_tabs(a.port)
        except Exception as e:
            sys.exit(f"браузер записи не поднялся на порту {a.port} ({type(e).__name__}): "
                     f"openclaw browser --browser-profile rec start не помог")
    room, created = find_room(tabs, a.url), False
    if not room:
        room = json.loads(_cdp("/json/new?" + quote(a.url, safe=":/?&=%#@+,;~"), method="PUT", port=a.port))
        created = True
    _cdp(f"/json/activate/{room['id']}", port=a.port)
    if a.out:
        _room_save(a.out, room)
    closed = []
    if a.close_others:
        for t in _cdp_tabs(a.port):
            if t["id"] != room["id"] and (t.get("url") or "").startswith("http"):
                try:
                    _cdp(f"/json/close/{t['id']}", port=a.port)
                    closed.append((t.get("url") or "")[:60])
                except Exception:
                    pass
    print(json.dumps({"ok": True, "tab_id": room["id"], "url": room.get("url") or a.url,
                      "created": created, "closed_other_rooms": closed,
                      "next": "работай в этой вкладке браузерным инструментом (profile rec, targetId = tab_id); "
                              "комнату через browser open НЕ открывай"}, ensure_ascii=False))


def cmd_watch(a):
    """Сторож записи: раз в минуту — жива ли запись, открыта ли комната, на экране ли она, есть ли звук.

    Не зависит от моделей: 16.09 контроль звука делал агент, модели сбоили, про закрытый
    звонок узнали через 8 минут, а зайти обратно никто не смог."""
    out = os.path.abspath(a.out)
    wl = os.path.join(out, "watch.log")

    def log(msg):
        with open(wl, "a", encoding="utf-8") as fh:
            fh.write(dt.datetime.now(MSK).strftime("%H:%M:%S ") + msg + "\n")

    def say(text):
        log("сообщение: " + text)
        if not a.quiet:
            _tome(text)

    silent, silent_since, told_silence, told_cdp, last_rejoin = 0, None, False, False, 0.0
    log("сторож запущен")
    while True:
        time.sleep(a.every)
        plan = _plan(out)
        title = plan.get("title") or os.path.basename(out)
        try:
            pid = int(open(_pidfile(out)).read().strip())
        except Exception:
            log("pid-файла нет — запись остановлена, выхожу")
            return
        if not _alive(pid):
            log("ffmpeg завершился — выхожу")
            return
        url = plan.get("url") or ""
        try:
            tabs = _cdp_tabs()
            told_cdp = False
        except Exception as e:
            log(f"браузер записи не отвечает: {type(e).__name__}")
            if not told_cdp:
                told_cdp = True
                say(f"⚠️ {title}: браузер записи не отвечает — запись идёт, но комнату проверить не могу.")
            continue
        known = _room_load(out)
        room, how = resolve_room(out, tabs)
        if room:
            if room["id"] != known.get("id") or (room.get("url") or "") != known.get("url"):
                log(f"комната: {(room.get('url') or '')[:80]} ({how})")
            _room_save(out, room)
            if not a.no_focus:
                try:
                    _cdp(f"/json/activate/{room['id']}")
                except Exception:
                    pass
        else:
            again = known.get("url") or url           # последний рабочий адрес надёжнее ссылки из плана
            if not again:
                log("комнаты нет и адрес неизвестен — жду")
            else:
                now = dt.datetime.now(MSK).strftime("%H:%M")
                r = subprocess.run([sys.executable, os.path.abspath(__file__), "open", "--url", again, "--out", out],
                                   capture_output=True, text=True, timeout=60, env=_env())
                log(f"вкладки комнаты нет — открыл заново {again[:60]}: " + ((r.stdout or r.stderr) or "").strip()[:120])
                if time.time() - last_rejoin > 600:
                    last_rejoin = time.time()
                    note = "перезаход не заказан (тестовый режим)"
                    if not a.no_rejoin and os.path.exists(WEBINAR):
                        rj = subprocess.run([sys.executable, WEBINAR, "rejoin", "--out", out],
                                            capture_output=True, text=True, timeout=180)
                        note = ((rj.stdout or rj.stderr) or "").strip()[-160:]
                    log("перезаход: " + note)
                    say(f"⚠️ {title}: в {now} закрылась вкладка комнаты. Открыл её заново и запустил перезаход, "
                        f"звук проверю через пару минут.")
                continue
        r = _sh(["ffmpeg", "-hide_banner", "-f", "pulse", "-i", f"{SINK}.monitor",
                 "-t", "5", "-af", "volumedetect", "-f", "null", "-"])
        mean = None
        for line in (r.stderr or "").splitlines():
            if "mean_volume" in line:
                mean = float(line.split("mean_volume:")[1].split("dB")[0])
        if mean is not None and mean > -55:
            if told_silence:
                say(f"✅ {title}: звук вернулся.")
            silent, silent_since, told_silence = 0, None, False
            continue
        silent += 1
        silent_since = silent_since or dt.datetime.now(MSK).strftime("%H:%M")
        log(f"тишина ({mean} дБ), проверок подряд: {silent}")
        if silent == 2:
            u = subprocess.run([sys.executable, os.path.abspath(__file__), "unmute", "--out", out],
                               capture_output=True, text=True, timeout=90, env=_env())
            log("unmute: " + ((u.stdout or u.stderr) or "").strip()[:200])
        if silent >= 4 and not told_silence:
            told_silence = True
            say(f"⚠️ {title}: в записи тишина с {silent_since}. Комната открыта, включение звука не помогло — "
                f"возможно, перерыв или эфир закончился.")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("start"); s.add_argument("--out", required=True); s.add_argument("--name", required=True)
    s.add_argument("--duration", type=int); s.add_argument("--until", help="HH:MM мск")
    s.add_argument("--segment", type=int, default=600)
    s.add_argument("--no-watch", action="store_true", help="не запускать сторожа (только для отладки)")
    st = sub.add_parser("status"); st.add_argument("--out", required=True)
    sp = sub.add_parser("stop"); sp.add_argument("--out", required=True); sp.add_argument("--name")
    c = sub.add_parser("check"); c.add_argument("file")
    se = sub.add_parser("selftest"); se.add_argument("--force", action="store_true")
    pb = sub.add_parser("probe", help="есть ли звук в карте прямо сейчас"); pb.add_argument("--seconds", type=int, default=6)
    um = sub.add_parser("unmute", help="снять паузу/mute с плееров во вкладке эфира")
    um.add_argument("--port", type=int, default=18801); um.add_argument("--match", default="")
    um.add_argument("--out", default=""); um.add_argument("--url", default="")
    op = sub.add_parser("open", help="открыть комнату в браузере записи напрямую, не браузерным инструментом")
    op.add_argument("--url", required=True); op.add_argument("--port", type=int, default=18801)
    op.add_argument("--close-others", action="store_true"); op.add_argument("--out", default="")
    wa = sub.add_parser("watch", help="сторож записи (start запускает сам)"); wa.add_argument("--out", required=True)
    wa.add_argument("--every", type=int, default=60); wa.add_argument("--quiet", action="store_true")
    wa.add_argument("--no-rejoin", action="store_true"); wa.add_argument("--no-focus", action="store_true")
    a = ap.parse_args()
    {"start": cmd_start, "status": cmd_status, "stop": cmd_stop, "check": cmd_check,
     "selftest": cmd_selftest, "probe": cmd_probe, "unmute": cmd_unmute, "open": cmd_open, "watch": cmd_watch}[a.cmd](a)


if __name__ == "__main__":
    main()
