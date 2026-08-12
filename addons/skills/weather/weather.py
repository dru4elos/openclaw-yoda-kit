#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Погода без ключей (Open-Meteo). По умолчанию Москва.
  weather.py [--city Москва] [--days 1]"""
import argparse, sys
import requests

CITIES = {"москва": (55.7558, 37.6173), "moscow": (55.7558, 37.6173),
          "санкт-петербург": (59.9311, 30.3609), "фукуок": (10.2270, 103.9670),
          "шанхай": (31.2304, 121.4737), "пекин": (39.9042, 116.4074)}
WMO = {0: "ясно", 1: "малооблачно", 2: "переменная облачность", 3: "пасмурно",
       45: "туман", 48: "изморозь", 51: "морось", 53: "морось", 55: "морось",
       61: "дождь", 63: "дождь", 65: "сильный дождь", 71: "снег", 73: "снег",
       75: "сильный снег", 80: "ливни", 81: "ливни", 82: "сильные ливни",
       95: "гроза", 96: "гроза с градом", 99: "гроза с градом"}

def geocode(city):
    key = city.strip().lower()
    if key in CITIES:
        return CITIES[key], city
    r = requests.get("https://geocoding-api.open-meteo.com/v1/search",
                     params={"name": city, "count": 1, "language": "ru"}, timeout=30)
    res = (r.json().get("results") or [])
    if not res:
        sys.exit(f"город «{city}» не найден")
    return (res[0]["latitude"], res[0]["longitude"]), res[0].get("name", city)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--city", default="Москва")
    ap.add_argument("--days", type=int, default=1)
    a = ap.parse_args()
    (lat, lon), name = geocode(a.city)
    r = requests.get("https://api.open-meteo.com/v1/forecast", params={
        "latitude": lat, "longitude": lon, "timezone": "auto",
        "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m",
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max,weather_code",
        "forecast_days": max(1, min(a.days, 7))}, timeout=40)
    r.raise_for_status()
    d = r.json()
    cur = d.get("current", {})
    print(f"Погода: {name}")
    print(f"  сейчас: {cur.get('temperature_2m')}°C (ощущается {cur.get('apparent_temperature')}°C), "
          f"{WMO.get(cur.get('weather_code'), '—')}, ветер {cur.get('wind_speed_10m')} км/ч")
    dy = d.get("daily", {})
    for i, day in enumerate(dy.get("time", [])[:a.days]):
        print(f"  {day}: {dy['temperature_2m_min'][i]}…{dy['temperature_2m_max'][i]}°C, "
              f"{WMO.get(dy['weather_code'][i], '—')}, осадки {dy['precipitation_probability_max'][i]}%")

main()
