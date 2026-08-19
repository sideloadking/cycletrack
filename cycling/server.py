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

from . import coast as coast_mod, config, metrics as metrics_mod, pipeline, power as power_mod, storage

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


@app.get("/api/rides/{ride_id}/descents")
def ride_descents(ride_id: int):
    """Candidate descending runs with coast/pedal/ask classification.

    Geometry-only candidates come from the stored (lidar-smoothed) grade; the
    caution-first classifier labels them, and any manual tag stored on the
    ride overrides the auto label.
    """
    ride = storage.get_ride(ride_id)
    if ride is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    series = storage.get_ride_series(ride_id)
    records = _series_to_records(series)
    rider, _ = storage.get_profile()
    rider = rider or dict(config.DEFAULT_RIDER)
    max_hr = rider.get("max_hr") or power_mod._default_max_hr(rider)
    weights, training_features = _coast_model(max_hr)

    descents = coast_mod.classify_descents(records, max_hr, weights=weights,
                                           training_features=training_features)
    manual = storage.get_coast_segments(ride_id)
    for d in descents:
        d["source"] = "auto"
        for m in manual:
            if m.get("source") != "manual":
                continue
            if (abs(m["t_start"] - d["t_start"]) < 2.0
                    and abs(m["t_end"] - d["t_end"]) < 2.0):
                d["label"] = m["label"]
                d["source"] = "manual"
                break
    return {"descents": descents}


@app.post("/api/rides/{ride_id}/coast_segments")
def save_coast_tag(ride_id: int, payload: dict = Body(...)):
    """Upsert or clear a manual descent tag, then re-run this ride's loop
    calibration with the updated trust set (and re-apply any recovered wind).

    ``label`` is 'coast' | 'pedal' | 'brake', or null to clear the manual tag
    and revert the descent to the auto classifier.
    """
    ride = storage.get_ride(ride_id)
    if ride is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    t_start = float(payload.get("t_start", 0.0))
    t_end = float(payload.get("t_end", 0.0))
    label = payload.get("label")

    manual = [s for s in storage.get_coast_segments(ride_id)
              if s.get("source") == "manual"]
    manual = [s for s in manual
              if not (abs(s["t_start"] - t_start) < 2.0
                      and abs(s["t_end"] - t_end) < 2.0)]
    if label in ("coast", "pedal", "brake"):
        manual.append({"t_start": t_start, "t_end": t_end, "label": label,
                       "source": "manual", "score": None})
    storage.save_coast_segments(ride_id, manual)

    # Re-run calibration with the updated trust set, exactly like the manual
    # /api/calibrate path but with the tags applied to the records.
    records = _records_with_coast_tags(ride_id)
    rider, bike = storage.get_profile()
    rider = rider or dict(config.DEFAULT_RIDER)
    bike = bike or dict(config.DEFAULT_BIKE)
    weather = ride["weather"]
    results = pipeline.try_auto_calibrate(ride_id, records, rider, bike, weather)

    loop_fit = next((c for c in results if c.get("type") == "loop"), None)
    if loop_fit and loop_fit.get("wind_recovered") and loop_fit.get("wind_mps") is not None:
        eff_weather = dict(weather)
        eff_weather["wind_speed_mps"] = loop_fit["wind_mps"]
        eff_weather["wind_dir_deg"] = loop_fit["wind_dir_deg"]
        records = power_mod.compute_power(records, rider, bike, eff_weather)
        m = ride.get("metrics") or {}
        elev_summary = {
            "gain_m": m.get("elevation_gain_m") or ride.get("gain_m") or 0.0,
            "min_elev": m.get("min_elev"),
            "max_elev": m.get("max_elev"),
            "elevation_source": ride.get("elevation_source") or m.get("elevation_source"),
            "snapped_ratio": m.get("snapped_ratio", 0.0),
        }
        meta = {
            "total_distance": ride.get("distance_m"),
            "duration_seconds": ride.get("duration_s"),
        }
        metrics = metrics_mod.compute_ride_metrics(records, rider, bike, elev_summary, meta)
        storage.update_ride_metrics(ride_id, metrics, records)

    # A tag changes the pool of trusted coasts, so refresh the cross-ride fit
    # too and let every ride re-apply its own recovered wind + pedal tags.
    # The pooled Nelder-Mead + full recalculation is slow, so run it off the
    # request thread — the tag and this ride's loop re-fit above are already
    # done synchronously, and storage access is serialised by the module lock.
    def _repool():
        try:
            if len(storage.list_rides()) <= config.POOLED_AUTO_MAX_RIDES:
                pooled = pipeline.try_pooled_calibrate(rider, bike)
                if pooled:
                    fresh_rider, fresh_bike = storage.get_profile()
                    pipeline.recalculate_rides(fresh_rider or rider, fresh_bike or bike)
        except Exception:
            pass
    threading.Thread(target=_repool, daemon=True).start()

    return {"ok": True, "descents": ride_descents(ride_id)["descents"]}


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


@app.post("/api/calibrate/pooled")
def calibrate_pooled(payload: dict = Body(...)):
    """Run the pooled cross-ride fit (shared Crr/CdA + per-ride wind) and
    apply it to the bike when accepted, then re-run every ride with its own
    recovered wind and any stored pedal tags."""
    rider, bike = storage.get_profile()
    rider = rider or dict(config.DEFAULT_RIDER)
    bike = bike or dict(config.DEFAULT_BIKE)
    pooled = pipeline.try_pooled_calibrate(rider, bike)
    if pooled is None:
        return JSONResponse(
            {"error": "Not enough coasting descents across rides for a pooled fit"},
            status_code=422)
    fresh_rider, fresh_bike = storage.get_profile()
    pipeline.recalculate_rides(fresh_rider or rider, fresh_bike or bike)
    return {"calibration": pooled}


@app.post("/api/calibrate/{ride_id}")
def calibrate_ride(ride_id: int, payload: dict = Body(...)):
    """Re-run a calibration procedure on a stored ride."""
    ride = storage.get_ride(ride_id)
    if ride is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    records = _records_with_coast_tags(ride_id)
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
    # Mirror the import path: if the fit recovered an effective wind, re-run
    # this ride's power with it so manual calibration behaves like auto
    # calibration.
    if result.get("wind_recovered") and result.get("wind_mps") is not None:
        eff_weather = dict(weather)
        eff_weather["wind_speed_mps"] = result["wind_mps"]
        eff_weather["wind_dir_deg"] = result["wind_dir_deg"]
        records = power_mod.compute_power(records, rider, bike, eff_weather)
        m = ride.get("metrics") or {}
        elev_summary = {
            "gain_m": m.get("elevation_gain_m") or ride.get("gain_m") or 0.0,
            "min_elev": m.get("min_elev"),
            "max_elev": m.get("max_elev"),
            "elevation_source": ride.get("elevation_source") or m.get("elevation_source"),
            "snapped_ratio": m.get("snapped_ratio", 0.0),
        }
        meta = {
            "total_distance": ride.get("distance_m"),
            "duration_seconds": ride.get("duration_s"),
        }
        metrics = metrics_mod.compute_ride_metrics(records, rider, bike, elev_summary, meta)
        storage.update_ride_metrics(ride_id, metrics, records)
    return result


def _coast_model(max_hr):
    """(weights, training_features) for the caution-first classifier.

    Fitted from the user's manual coast/pedal/brake tags only (never auto
    labels), blended toward the cold-start prior. No tags => the prior
    exactly as before, so cold start behaves identically to phase 0.
    """
    return pipeline.coast_model(max_hr)


def _records_with_coast_tags(ride_id):
    """Stored ride records with manual coast/pedal/brake tags applied."""
    series = storage.get_ride_series(ride_id)
    records = _series_to_records(series)
    return coast_mod.apply_segment_overrides(records, storage.get_coast_segments(ride_id))


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
