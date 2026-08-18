"""Weather lookup for the ride time + location.

Uses the Open-Meteo historical archive (no API key). Results are cached by
(lat, lon, date) so repeated imports of the same ride are free. Missing or
failed weather degrades to the "default assumptions" from the plan — it never
aborts an import.
"""

import datetime
import json
import math
import statistics

import requests

from . import config, geo

_DEFAULTS = {
    "temp_c": 15.0,
    "wind_speed_mps": 0.0,
    "wind_dir_deg": 0.0,
    "pressure_hpa": 1013.0,
    "source": "defaults",
}

# Bump whenever the fetch logic changes the *meaning* of a cached field
# (pressure_hpa switched from surface_pressure to pressure_msl, and the
# ride-local date/hour became timezone-aware): the on-disk cache must not
# re-serve old entries under their previous semantics.
_CACHE_VERSION = "v2"


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


def _local_time(when_unix, tz_name):
    """Unix time as an aware datetime in tz_name, falling back to the
    machine's local wall-clock when the zone is unknown (e.g. "auto")."""
    try:
        from zoneinfo import ZoneInfo
        return datetime.datetime.fromtimestamp(when_unix, tz=ZoneInfo(tz_name))
    except Exception:
        return datetime.datetime.fromtimestamp(when_unix)


_TZ_CACHE = {}


def _resolve_timezone(lat, lon):
    """Resolve Open-Meteo's "auto" to a concrete IANA zone for (lat, lon).

    Open-Meteo computes the location's zone itself, so a cheap single-day
    probe is enough to learn it; the answer depends only on the location and
    is cached in-process. On any failure this returns "auto" and the caller
    degrades to the importing machine's clock (the previous best effort).
    """
    key = f"{lat:.3f},{lon:.3f}"
    cached = _TZ_CACHE.get(key)
    if cached is not None:
        return cached
    tz = "auto"
    try:
        r = requests.get(
            config.OPEN_METEO_ARCHIVE,
            params={
                "latitude": lat,
                "longitude": lon,
                "start_date": "2020-01-01",
                "end_date": "2020-01-01",
                "hourly": "temperature_2m",
                "timezone": "auto",
            },
            timeout=config.HTTP_TIMEOUT,
        )
        r.raise_for_status()
        tz = r.json().get("timezone") or "auto"
    except Exception:
        tz = "auto"
    _TZ_CACHE[key] = tz
    return tz


def fetch_weather(lat, lon, when_unix):
    """Return weather at (lat, lon) around the given ride-local time."""
    try:
        lat, lon = float(lat), float(lon)
        when_unix = float(when_unix)
    except (TypeError, ValueError, OSError):
        return dict(_DEFAULTS)

    # The request date must be the *ride's* local date, not the importing
    # machine's: a ride shortly after UTC midnight is still "yesterday" or
    # "tomorrow" in the ride's zone, and asking for the machine's day would
    # fetch the wrong temperature/pressure/wind. UK rides have a known zone;
    # elsewhere "auto" is resolved up front so the request targets the right
    # day.
    tz_name = _tz_name(lat, lon)
    if tz_name == "auto":
        tz_name = _resolve_timezone(lat, lon)

    try:
        when = _local_time(when_unix, tz_name)
    except (TypeError, ValueError, OSError):
        return dict(_DEFAULTS)

    cache = _cache()
    key = f"{_CACHE_VERSION}:{lat:.3f},{lon:.3f},{when:%Y-%m-%d}"
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
                "hourly": "temperature_2m,wind_speed_10m,wind_direction_10m,pressure_msl,wind_gusts_10m",
                "timezone": tz_name,
            },
            timeout=config.HTTP_TIMEOUT,
        )
        r.raise_for_status()
        payload = r.json()
        data = payload.get("hourly", {})
        times = data.get("time") or []
        # Index with the ride's local hour in the timezone the response
        # actually used (resolved from "auto" when applicable).
        resp_tz = payload.get("timezone") or tz_name
        when = _local_time(when_unix, resp_tz)
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
        pres = _val("pressure_msl")
        gust = _val("wind_gusts_10m")

        # Per-ride wind uncertainty: the spread of the day's hourly wind
        # speeds plus a gust margin. A calm, steady day gets a tight sigma;
        # a gusty or changeable day gets a wide one — the bands follow.
        wind_arr = [float(x) for x in (data.get("wind_speed_10m") or [])
                    if x is not None]
        wind_sigma = None
        if len(wind_arr) >= 3:
            spread = statistics.pstdev(wind_arr)
            wind_sigma = max(0.5, min(4.0, spread * 0.7 + 0.5))
        if gust is not None and wind is not None:
            gust_margin = max(0.0, (float(gust) - float(wind))) * 0.4
            wind_sigma = max(wind_sigma or 0.0, min(4.0, max(0.5, gust_margin)))

        result = {
            "temp_c": float(temp) if temp is not None else _DEFAULTS["temp_c"],
            "wind_speed_mps": float(wind) if wind is not None else _DEFAULTS["wind_speed_mps"],
            "wind_dir_deg": float(wdir) if wdir is not None else _DEFAULTS["wind_dir_deg"],
            "pressure_hpa": float(pres) if pres is not None else _DEFAULTS["pressure_hpa"],
            "source": "open-meteo",
        }
        if wind_sigma is not None:
            result["wind_sigma_mps"] = wind_sigma
        # The full hourly series lets the power stage interpolate the wind
        # across a long ride instead of holding the start-hour value.
        spd = [float(x) for x in (data.get("wind_speed_10m") or []) if x is not None]
        drc = [float(x) for x in (data.get("wind_direction_10m") or []) if x is not None]
        if spd and drc:
            result["wind_speed_hourly"] = spd
            result["wind_dir_hourly"] = drc
        result["ride_hour"] = int(when.hour)
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
