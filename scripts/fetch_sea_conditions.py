#!/usr/bin/env python3
"""
GalaiGO — build sea_conditions.json from Open-Meteo (free, non-commercial).

For each beach it fetches the Marine API (daily wave max + current wave/SST)
and the Forecast API (sunrise), then derives:
  - after-storm flag (recent daily wave max was high AND now is calm)

Tide times are intentionally NOT included: Open-Meteo's coastal sea-level model
is too coarse for Israel's tiny (~0.3 m) tides to be meaningful.

Publishes the file ONLY if every beach succeeded, so a partial failure never
overwrites good data with junk.

Attribution required by Open-Meteo/DWD (CC BY 4.0) is written into the file.
Run: python3 fetch_sea_conditions.py [output_path]
"""

import json
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, date

# --- Beach list (v1). lat/lon approximate — verify before wide launch. ---
BEACHES = [
    {"id": "tel-aviv-gordon", "name": "חוף גורדון, תל אביב", "region": "תל אביב", "lat": 32.088, "lon": 34.767},
    {"id": "ashkelon-delila", "name": "חוף דלילה, אשקלון",   "region": "אשקלון",   "lat": 31.673, "lon": 34.556},
    {"id": "palmachim",       "name": "חוף פלמחים",           "region": "מרכז",     "lat": 31.930, "lon": 34.698},
    {"id": "caesarea-aqueduct","name": "חוף האקוודוקט, קיסריה","region": "חוף הכרמל","lat": 32.516, "lon": 34.892},
    {"id": "netanya-sironit", "name": "חוף סירונית, נתניה",   "region": "נתניה",    "lat": 32.328, "lon": 34.849},
    {"id": "haifa-dado",      "name": "חוף דדו, חיפה",        "region": "חיפה",     "lat": 32.826, "lon": 34.955},
    {"id": "eilat-north",     "name": "החוף הצפוני, אילת",    "region": "אילת",     "lat": 29.556, "lon": 34.951},
]

STORM_WAVE_M = 1.5   # a recent daily max above this = "there was a storm"
CALM_WAVE_M = 1.0    # and now below this = good time to go
PAST_DAYS = 3
FORECAST_DAYS = 7

MARINE_URL = "https://marine-api.open-meteo.com/v1/marine"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"


def _get(url, params, tries=3):
    q = "&".join(f"{k}={urllib.request.quote(str(v))}" for k, v in params.items())
    full = f"{url}?{q}"
    last = None
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(full, timeout=30) as r:
                return json.loads(r.read().decode("utf-8"))
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
            last = e
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"request failed after {tries} tries: {full} ({last})")


def _hhmm(iso):
    # "2026-07-17T11:00" -> "11:00"
    return iso.split("T")[1][:5] if "T" in iso else iso


def _day_of(iso):
    return iso.split("T")[0]


def build_beach(b):
    marine = _get(MARINE_URL, {
        "latitude": b["lat"], "longitude": b["lon"],
        "daily": "wave_height_max",
        "current": "wave_height,sea_surface_temperature",
        "past_days": PAST_DAYS, "forecast_days": FORECAST_DAYS,
        "length_unit": "metric", "cell_selection": "sea", "timezone": "auto",
    })
    fc = _get(FORECAST_URL, {
        "latitude": b["lat"], "longitude": b["lon"],
        "daily": "sunrise", "forecast_days": FORECAST_DAYS,
        "timezone": "auto",
    })

    dtimes = marine["daily"]["time"]
    wmax = marine["daily"]["wave_height_max"]
    daily_wmax = {d: w for d, w in zip(dtimes, wmax)}
    sunrise = {_day_of(t): _hhmm(t) for t in fc["daily"]["sunrise"]}

    # Use the API's local "today" (timezone=auto), not UTC — so the date matches
    # the Israeli user's device and sunrise lines up near midnight UTC.
    today = fc["daily"]["time"][0]
    wave_now = marine.get("current", {}).get("wave_height")
    sst_now = marine.get("current", {}).get("sea_surface_temperature")

    # after-storm: any of the past PAST_DAYS had a high max, and now calm
    recent = [daily_wmax.get(d) for d in dtimes if d < today]
    recent = [w for w in recent[-PAST_DAYS:] if w is not None]
    stormy = any(w >= STORM_WAVE_M for w in recent)
    after_storm = bool(stormy and wave_now is not None and wave_now < CALM_WAVE_M)
    storm_note = "הים היה גבוה לאחרונה — עכשיו הזדמנות מצוינת, החול זז ונחשפו שכבות." if after_storm else None

    day_block = {
        "date": today,
        "sunrise": sunrise.get(today),
        "waveHeightNowM": round(wave_now, 2) if wave_now is not None else None,
        "waveHeightMaxTodayM": round(daily_wmax[today], 2) if daily_wmax.get(today) is not None else None,
        "seaTempC": round(sst_now, 1) if sst_now is not None else None,
        "afterStorm": after_storm,
        "afterStormNote": storm_note,
    }

    forecast = []
    for d in dtimes:
        if d <= today:
            continue
        forecast.append({
            "date": d,
            "waveMaxM": round(daily_wmax[d], 2) if daily_wmax.get(d) is not None else None,
        })

    return {
        "id": b["id"], "name": b["name"], "region": b["region"],
        "lat": b["lat"], "lon": b["lon"],
        "today": day_block, "forecast": forecast,
    }


def main():
    out_path = sys.argv[1] if len(sys.argv) > 1 else "sea_conditions.json"
    beaches = []
    for b in BEACHES:
        beaches.append(build_beach(b))   # raises on failure -> no write
        time.sleep(1)                     # be gentle to the free API
    doc = {
        "generatedAt": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "attribution": "נתוני ים: Open-Meteo · DWD (CC BY 4.0)",
        "beaches": beaches,
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
    print(f"wrote {out_path} with {len(beaches)} beaches")


if __name__ == "__main__":
    main()
