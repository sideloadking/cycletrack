"""FastAPI engine server.

Serves the browser UI and a small REST API on localhost. Imports run on a
background thread with a job queue so the UI can show progress while lidar /
weather are fetched.
"""

import base64
import json
import math
import pathlib
import threading
import time
import uuid

from fastapi import FastAPI, Body
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import config, metrics as metrics_mod, pipeline, power as power_mod, storage

WEB_DIR = pathlib.Path(__file__).resolve().parent.parent / "web"

app = FastAPI(title="Cycling Progress Tracker", version="0.1.0")

# ---------------------------------------------------------------------------
# Job queue
# ---------------------------------------------------------------------------

_jobs = {}
_jobs_lock = threading.Lock()


def _job(job_id, **updates):
    with _jobs_lock:
        job = _jobs.setdefault(job_id, {"id": job_id})
        job.update(updates)
        return dict(job)


def _run_import(job_id, path, rider, bike, name):
    def progress(pct, msg):
        _job(job_id, status="running", progress=pct, message=msg)

    try:
        _job(job_id, status="running", progress=0, message="Starting")
        result = pipeline.import_fit_file(path, rider, bike, progress_cb=progress,
                                          display_name=name)
        _job(job_id, status="done", progress=100, message="Imported", result=result)
    except pipeline.DuplicateRideError as e:
        _job(job_id, status="duplicate", progress=100, message=str(e), error=str(e))
    except Exception as e:
        import traceback
        _job(job_id, status="error", progress=100, message=str(e),
             error=str(e), trace=traceback.format_exc())


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


@app.get("/api/health")
def health():
    rider, bike = storage.get_profile()
    rides = storage.list_rides()
    return {
        "ok": True,
        "app": "Cycling Progress Tracker",
        "version": "0.1.0",
        "db": str(config.DB_PATH),
        "rides": len(rides),
        "profile_setup": rider is not None,
    }


@app.get("/api/profile")
def get_profile():
    rider, bike = storage.get_profile()
    if rider is None:
        rider = dict(config.DEFAULT_RIDER)
        rider["hr_zones"] = metrics_mod.default_hr_zones(
            rider["max_hr"] or metrics_mod.default_max_hr(rider["age"]),
            rider.get("resting_hr"),
        )
    if bike is None:
        bike = dict(config.DEFAULT_BIKE)
    if not rider.get("hr_zones"):
        rider["hr_zones"] = metrics_mod.default_hr_zones(
            rider.get("max_hr") or metrics_mod.default_max_hr(rider.get("age")),
            rider.get("resting_hr"),
        )
    return {"rider": rider, "bike": bike}


def _clean_zones(zones):
    """Validate an editable HR-zones list; None means 'fall back to defaults'."""
    if not isinstance(zones, list):
        return None
    out = []
    for z in zones:
        if not isinstance(z, dict):
            return None
        try:
            lo, hi = int(z.get("lo")), int(z.get("hi"))
        except (TypeError, ValueError):
            return None
        if not (30 <= lo < hi <= 250):
            return None
        out.append({"lo": lo, "hi": hi})
    return out if len(out) == 5 else None


@app.put("/api/profile")
def put_profile(payload: dict = Body(...)):
    rider = payload.get("rider", {})
    bike = payload.get("bike", {})
    if not rider.get("max_hr"):
        rider["max_hr"] = metrics_mod.default_max_hr(rider.get("age"))
    # Use the user's edited zones when valid; otherwise recompute from the
    # (possibly changed) max + resting HR.
    rider["hr_zones"] = _clean_zones(rider.get("hr_zones")) or metrics_mod.default_hr_zones(
        rider["max_hr"], rider.get("resting_hr")
    )
    saved = storage.save_profile(rider, bike)
    saved_rider, saved_bike = saved if isinstance(saved, tuple) else (rider, bike)
    pipeline.recalculate_rides(saved_rider or rider, saved_bike or bike)
    return {"rider": saved_rider or rider, "bike": saved_bike or bike}


@app.post("/api/import")
def import_files(payload: dict = Body(...)):
    """Import .fit file(s) sent as base64 JSON. Returns job ids."""
    files = payload.get("files", [])
    rider, bike = storage.get_profile()
    rider = rider or dict(config.DEFAULT_RIDER)
    bike = bike or dict(config.DEFAULT_BIKE)

    job_ids = []
    for item in files:
        name = item.get("name", "ride.fit")
        raw = item.get("data", "")
        if not raw:
            continue
        try:
            data = base64.b64decode(raw)
        except Exception:
            continue
        job_id = uuid.uuid4().hex[:12]
        path = config.IMPORT_DIR / f"{job_id}_{name}"
        path.write_bytes(data)
        _job(job_id, status="queued", progress=0, message="Queued", filename=name)
        job_ids.append(job_id)
        threading.Thread(target=_run_import, args=(job_id, path, rider, bike, name),
                         daemon=True).start()
    return {"jobs": job_ids}


@app.get("/api/import/status/{job_id}")
def import_status(job_id: str):
    with _jobs_lock:
        return _jobs.get(job_id, {"id": job_id, "status": "unknown"})


@app.get("/api/jobs")
def list_jobs():
    with _jobs_lock:
        return list(_jobs.values())


@app.get("/api/rides")
def rides():
    return storage.list_rides()


@app.get("/api/rides/{ride_id}")
def ride_detail(ride_id: int):
    ride = storage.get_ride(ride_id)
    if ride is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    ride["route"] = storage.route_for_ride(ride_id)
    return ride


@app.get("/api/routes")
def routes_list():
    return storage.list_routes()


@app.get("/api/overview")
def overview():
    """One read path for the dashboard's complete view model.

    The browser can paint the overview from one local request instead of
    coordinating seven independent reads. The underlying calculations and
    storage remain unchanged; this is a presentation projection only.
    """
    return {
        "rides": storage.list_rides(),
        "routes": storage.list_routes(),
        "records": storage.get_records(),
        "fitness": trends_fitness(),
        "power": trends_power(),
        "drift": trends_cardiac(),
        "wattsHr": trends_watts_hr(),
    }


@app.get("/api/routes/{route_id}")
def route_detail(route_id: int):
    """A route and its rides, enriched for same-route comparison."""
    route = storage.get_route(route_id)
    if route is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    enriched = []
    for ride in route["rides"]:
        full = storage.get_ride(ride["id"])
        enriched.append(_route_ride_row(full, ride["route_n"]))
    route["rides"] = enriched
    return route


def _route_ride_row(ride, route_n):
    m = ride.get("metrics") or {}
    weather = ride.get("weather") or {}
    wah = ride.get("watts_at_hr") or {}

    def wah_at(hr):
        v = wah.get(str(hr)) or {}
        return v.get("watts") if v.get("watts") is not None else None

    return {
        "id": ride["id"],
        "route_n": route_n,
        "filename": ride["filename"],
        "started_at": ride["started_at"],
        "distance_m": m.get("distance_m", ride.get("distance_m")),
        "duration_s": m.get("duration_s", ride.get("duration_s")),
        "gain_m": m.get("elevation_gain_m", ride.get("gain_m")),
        "avg_speed_mps": m.get("avg_speed_mps"),
        "avg_hr": m.get("avg_hr", ride.get("avg_hr")),
        "trimp": m.get("trimp", ride.get("trimp")),
        "avg_watts": m.get("avg_watts", ride.get("avg_watts")),
        "normalized_power": m.get("normalized_power"),
        "vo2max": m.get("vo2max"),
        "watts_140": wah_at(140),
        "watts_150": wah_at(150),
        "temp_c": weather.get("temp_c"),
        "wind_mps": weather.get("wind_speed_mps"),
        "elevation_source": ride.get("elevation_source"),
    }


@app.get("/api/trends/power")
def trends_power(recent: int = 5):
    """Best-N-minute power over time, plus recent full power curves."""
    rows = storage.list_rides()
    rows.sort(key=lambda r: r["started_at"])
    series = {str(m): [] for m in config.POWER_CURVE_MINUTES}
    for ride in rows:
        full = storage.get_ride(ride["id"])
        pc = full.get("power_curve") or {}
        for m in config.POWER_CURVE_MINUTES:
            v = pc.get(str(m)) or {}
            if v.get("watts") is None:
                continue
            series[str(m)].append({
                "date": ride["started_at"],
                "ride_id": ride["id"],
                "watts": v["watts"],
                "lo": v.get("lo"),
                "hi": v.get("hi"),
            })
    curves = []
    for ride in rows[-recent:]:
        full = storage.get_ride(ride["id"])
        pc = full.get("power_curve") or {}
        curves.append({
            "ride_id": ride["id"],
            "date": ride["started_at"],
            "filename": ride["filename"],
            "points": [
                {"min": m, "watts": (pc.get(str(m)) or {}).get("watts")}
                for m in config.POWER_CURVE_MINUTES
            ],
        })
    return {"durations": config.POWER_CURVE_MINUTES, "series": series, "curves": curves}


@app.get("/api/trends/cardiac")
def trends_cardiac():
    """Cardiac drift per ride (steady-effort HR rise), for the dashboard."""
    rows = [r for r in storage.list_rides() if r.get("has_hr")]
    rows.sort(key=lambda r: r["started_at"])
    points = []
    for ride in rows:
        full = storage.get_ride(ride["id"])
        m = full.get("metrics") or {}
        d = m.get("cardiac_drift")
        if d:
            points.append({"date": ride["started_at"], "ride_id": ride["id"], **d})
    return {"points": points}


@app.get("/api/rides/{ride_id}/series")
def ride_series(ride_id: int, downsample: int = 1800):
    """Return a bounded Ride timeline for smooth browser replay."""
    downsample = max(240, min(int(downsample or 1800), 5000))
    return storage.get_ride_series(ride_id, downsample=downsample)


@app.delete("/api/rides/{ride_id}")
def delete_ride(ride_id: int):
    storage.delete_ride(ride_id)
    return {"ok": True}


@app.get("/api/trends/watts_hr")
def trends_watts_hr():
    """The headline chart: watts produced at a fixed HR over time."""
    rows = storage.list_rides()
    series = {}
    for hr in config.WATTS_AT_HR:
        series[str(hr)] = []

    for ride in rows:
        wah = ride.get("watts_at_hr") or {}
        calibrated = bool(ride.get("bike_calibrated"))
        for hr in config.WATTS_AT_HR:
            v = wah.get(str(hr))
            if not v or v.get("watts") is None:
                continue
            conf = "confident" if (calibrated and (v.get("r2") or 0) >= 0.5) else "context"
            series[str(hr)].append({
                "date": ride["started_at"],
                "ride_id": ride["id"],
                "watts": v["watts"],
                "lo": v["lo"],
                "hi": v["hi"],
                "n": v.get("n", 0),
                "r2": v.get("r2"),
                "confidence": conf,
            })

    for hr in series:
        series[hr].sort(key=lambda p: p["date"])
    return {"fixed_hrs": config.WATTS_AT_HR, "series": series}


@app.get("/api/trends/fitness")
def trends_fitness():
    """CTL/ATL/TSB impulse-response fitness over TRIMP."""
    rows = [r for r in storage.list_rides() if r.get("has_hr")]
    rows.sort(key=lambda r: r["started_at"])
    points = []
    ctl = atl = 0.0
    prev_day = None
    for r in rows:
        trimp = r.get("trimp") or 0.0
        day = r["started_at"] / 86400.0
        if prev_day is not None and day > prev_day:
            delta = day - prev_day
            ctl *= math.exp(-delta / config.CTL_TAU_DAYS)
            atl *= math.exp(-delta / config.ATL_TAU_DAYS)
        ctl += trimp * (1.0 - math.exp(-1.0 / config.CTL_TAU_DAYS))
        atl += trimp * (1.0 - math.exp(-1.0 / config.ATL_TAU_DAYS))
        prev_day = day
        points.append({
            "date": r["started_at"],
            "ride_id": r["id"],
            "trimp": trimp,
            "ctl": round(ctl, 1),
            "atl": round(atl, 1),
            "tsb": round(ctl - atl, 1),
        })
    return {"points": points, "ctl_tau": config.CTL_TAU_DAYS, "atl_tau": config.ATL_TAU_DAYS}


@app.get("/api/records")
def records():
    return storage.get_records()


@app.get("/api/calibrations")
def calibrations():
    return storage.list_calibrations()


@app.post("/api/calibrate/{ride_id}")
def calibrate_ride(ride_id: int, payload: dict = Body(...)):
    """Re-run a calibration procedure on a stored ride."""
    ride = storage.get_ride(ride_id)
    if ride is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    series = storage.get_ride_series(ride_id)
    records = _series_to_records(series)
    rider, bike = storage.get_profile()
    rider = rider or dict(config.DEFAULT_RIDER)
    bike = bike or dict(config.DEFAULT_BIKE)
    weather = ride["weather"]

    ctype = payload.get("type", "loop")
    if ctype == "loop":
        result = power_mod.calibrate_loop(records, rider, bike, weather)
    else:
        result = power_mod.calibrate_climb(records, rider, bike, weather)
    if result is None:
        return JSONResponse({"error": "No suitable segments found"}, status_code=422)
    storage.save_calibration(ride_id, result)
    return result


def _series_to_records(series):
    gps = {p["idx"]: p for p in series["gps"]}
    records = []
    for i in sorted(gps):
        p = gps[i]
        rec = {
            "t": p["t"], "lat": p["lat"], "lon": p["lon"],
            "elev": p["elev"], "elev_raw": p["elev_raw"], "grade": p["grade"],
            "speed": p.get("speed"), "dist": p.get("dist"), "hr": None,
        }
        records.append(rec)
    # attach hr + power
    hr_by_t = {}
    for h in series["hr"]:
        hr_by_t.setdefault(round(h["t"]), h["hr"])
    pw_by_t = {}
    for p in series["power"]:
        pw_by_t.setdefault(round(p["t"]), p)
    for rec in records:
        t = round(rec["t"])
        rec["hr"] = hr_by_t.get(t)
        pw = pw_by_t.get(t)
        if pw:
            rec["watts_est"] = pw["watts_est"]
    return records


# ---------------------------------------------------------------------------
# Static UI
# ---------------------------------------------------------------------------

app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")


@app.get("/")
def index():
    return FileResponse(str(WEB_DIR / "index.html"))
