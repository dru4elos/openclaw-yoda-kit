#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Погода без ключей: Open-Meteo (основной) -> wttr.in (резерв). По умолчанию Москва.
  weather.py [--city Москва] [--days 1]

Жёсткий бюджет времени: скилл ОБЯЗАН уложиться в ~28с даже при мёртвых API —
иначе exec-окно агента (30с) истекает и брифинг уходит без погоды (инцидент 15.08)."""
import argparse
import os
import sys
import requests

CITIES = {"москва": (55.7558, 37.6173), "moscow": (55.7558, 37.6173),
          "санкт-петербург": (59.9311, 30.3609), "фукуок": (10.2270, 103.9670),
          "шанхай": (31.2304, 121.4737), "пекин": (39.9042, 116.4074),
          "звенигород": (55.7297, 36.8562)}
WMO = {0: "ясно", 1: "малооблачно", 2: "переменная облачность", 3: "пасмурно",
       45: "туман", 48: "изморозь", 51: "морось", 53: "морось", 55: "морось",
       61: "дождь", 63: "дождь", 65: "сильный дождь", 71: "снег", 73: "снег",
       75: "сильный снег", 80: "ливни", 81: "ливни", 82: "сильные ливни",
       95: "гроза", 96: "гроза с градом", 99: "гроза с градом"}

FORCE_FALLBACK = os.environ.get("WEATHER_FORCE_FALLBACK") == "1"   # для тестов


def open_meteo(city, days):
    """Основной источник. Гео 6с (1 попытка), прогноз 7с × 2 попытки."""
    key = city.strip().lower()
    if key in CITIES:
        (lat, lon), name = CITIES[key], city
    else:
        r = requests.get("https://geocoding-api.open-meteo.com/v1/search",
                         params={"name": city, "count": 1, "language": "ru"}, timeout=6)
        res = (r.json().get("results") or [])
        if not res:
            raise LookupError(f"город «{city}» не найден")
        (lat, lon), name = (res[0]["latitude"], res[0]["longitude"]), res[0].get("name", city)

    last = None
    for _ in range(2):
        try:
            r = requests.get("https://api.open-meteo.com/v1/forecast", params={
                "latitude": lat, "longitude": lon, "timezone": "auto",
                "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m",
                "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max,weather_code",
                "forecast_days": max(1, min(days, 7))}, timeout=7)
            r.raise_for_status()
            d = r.json()
            break
        except Exception as e:                       # noqa: BLE001
            last = e
    else:
        raise last

    cur = d.get("current", {})
    print(f"Погода: {name}")
    print(f"  сейчас: {cur.get('temperature_2m')}°C (ощущается {cur.get('apparent_temperature')}°C), "
          f"{WMO.get(cur.get('weather_code'), '—')}, ветер {cur.get('wind_speed_10m')} км/ч")
    dy = d.get("daily", {})
    for i, day in enumerate(dy.get("time", [])[:days]):
        print(f"  {day}: {dy['temperature_2m_min'][i]}…{dy['temperature_2m_max'][i]}°C, "
              f"{WMO.get(dy['weather_code'][i], '—')}, осадки {dy['precipitation_probability_max'][i]}%")


def wttr(city, days):
    """Резерв: wttr.in (сам геокодит по имени). 8с."""
    r = requests.get(f"https://wttr.in/{requests.utils.quote(city)}",
                     params={"format": "j1", "lang": "ru"},
                     headers={"User-Agent": "curl/8"}, timeout=8)
    r.raise_for_status()
    d = r.json()
    cur = (d.get("current_condition") or [{}])[0]
    desc = ((cur.get("lang_ru") or [{}])[0].get("value")
            or (cur.get("weatherDesc") or [{}])[0].get("value", "—"))
    print(f"Погода: {city} (резервный источник wttr.in — Open-Meteo не ответил)")
    print(f"  сейчас: {cur.get('temp_C')}°C (ощущается {cur.get('FeelsLikeC')}°C), "
          f"{desc}, ветер {round(float(cur.get('windspeedKmph', 0)))} км/ч")
    for day in (d.get("weather") or [])[:days]:
        rains = [int(h.get("chanceofrain", 0)) for h in day.get("hourly", [])] or [0]
        print(f"  {day.get('date')}: {day.get('mintempC')}…{day.get('maxtempC')}°C, "
              f"осадки до {max(rains)}%")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--city", default="Москва")
    ap.add_argument("--days", type=int, default=1)
    a = ap.parse_args()
    if not FORCE_FALLBACK:
        try:
            open_meteo(a.city, a.days)
            return
        except LookupError as e:
            sys.exit(str(e))
        except Exception as e:                       # noqa: BLE001
            print(f"[open-meteo недоступен: {type(e).__name__}] пробую wttr.in…", file=sys.stderr)
    try:
        wttr(a.city, a.days)
    except Exception as e:                           # noqa: BLE001
        sys.exit("ПОГОДА НЕДОСТУПНА: и Open-Meteo, и wttr.in не ответили "
                 f"({type(e).__name__}). Скажи об этом честно, не выдумывай данные.")


main()
