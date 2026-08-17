"""Weather lookup for the ride time + location.

Uses the Open-Meteo historical archive (no API key). Results are cached by
(lat, lon, date) so repeated imports of the same ride are free. Missing or
failed weather degrades to the "default assumptions" from the plan — it never
aborts an import.
"""

import datetime
import json
import math

import requests

from . import config, geo

_DEFAULTS = {
    "temp_c": 15.0,
    "wind_speed_mps": 0.0,
    "wind_dir_deg": 0.0,
    "pressure_hpa": 1013.0,
    "source": "defaults",
}


def _cache():
    try:
        return json.loads(config.WEATHER_CACHE.read_text())
    except Exception:
        return {}


def _save_cache(cache):
    try:
        config.WEATHER_CACHE.write_text(json.dumps(cache))
    except Exception:
        pass


def _tz_name(lat, lon):
    if geo.in_uk(lat, lon):
        return "Europe/London"
    return "auto"


def fetch_weather(lat, lon, when_unix):
    """Return weather at (lat, lon) around the given local-wall-clock time."""
    try:
        lat, lon = float(lat), float(lon)
        when = datetime.datetime.fromtimestamp(when_unix)
    except (TypeError, ValueError, OSError):
        return dict(_DEFAULTS)

    cache = _cache()
    key = f"{lat:.3f},{lon:.3f},{when:%Y-%m-%d}"
    if key in cache:
        return cache[key]

    result = dict(_DEFAULTS)
    try:
        r = requests.get(
            config.OPEN_METEO_ARCHIVE,
            params={
                "latitude": lat,
                "longitude": lon,
                "start_date": when.strftime("%Y-%m-%d"),
                "end_date": when.strftime("%Y-%m-%d"),
                "hourly": "temperature_2m,wind_speed_10m,wind_direction_10m,surface_pressure",
                "timezone": _tz_name(lat, lon),
            },
            timeout=config.HTTP_TIMEOUT,
        )
        r.raise_for_status()
        data = r.json().get("hourly", {})
        times = data.get("time") or []
        hour = when.hour
        idx = hour if hour < len(times) else (len(times) - 1 if times else 0)

        def _val(name):
            arr = data.get(name) or []
            if not arr:
                return None
            return arr[idx] if idx < len(arr) else arr[-1]

        temp = _val("temperature_2m")
        wind = _val("wind_speed_10m")
        wdir = _val("wind_direction_10m")
        pres = _val("surface_pressure")

        result = {
            "temp_c": float(temp) if temp is not None else _DEFAULTS["temp_c"],
            "wind_speed_mps": float(wind) if wind is not None else _DEFAULTS["wind_speed_mps"],
            "wind_dir_deg": float(wdir) if wdir is not None else _DEFAULTS["wind_dir_deg"],
            "pressure_hpa": float(pres) if pres is not None else _DEFAULTS["pressure_hpa"],
            "source": "open-meteo",
        }
    except Exception:
        result = dict(_DEFAULTS)

    cache[key] = result
    _save_cache(cache)
    return result


def air_density(weather):
    """Air density (kg/m^3) from temperature and pressure."""
    temp_c = float(weather.get("temp_c", _DEFAULTS["temp_c"]))
    pressure_pa = float(weather.get("pressure_hpa", _DEFAULTS["pressure_hpa"])) * 100.0
    temp_k = temp_c + 273.15
    r_specific = 287.05
    return pressure_pa / (r_specific * temp_k)
