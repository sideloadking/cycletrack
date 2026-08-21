"""The per-file import pipeline.

.fit -> parse -> map-match -> sample DTM -> smooth -> grade -> weather ->
power + uncertainty -> metrics -> SQLite. Any individual stage degrades
gracefully; the only hard failures are "not a FIT file" and "no GPS points".
"""

import hashlib
import math
import pathlib
import threading
import time

from . import coast as coast_mod, config, elevation, geo, metrics as metrics_mod
from . import power as power_mod, storage, weather as weather_mod

# Only one pooled cross-ride fit may run at a time. The fit is tens of
# seconds of CPU; without this, an import's inline pooled pass and the
# server's debounced background pass could overlap and stack on the CPU.
# (Storage has its own lock, so ordering pooled-lock -> storage-lock is safe:
# nothing ever acquires the pooled lock while holding the storage lock.)
_pooled_fit_lock = threading.Lock()


def file_hash(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def import_fit_file(path, rider, bike, progress_cb=None, allow_duplicate=False,
                    display_name=None):
    from . import fit_parser

    def prog(pct, msg):
        if progress_cb:
            progress_cb(max(0, min(100, int(pct))), msg)

    path = pathlib.Path(path)
    name = display_name or path.name
    prog(2, f"Reading {name}")
    fhash = file_hash(path)

    if not allow_duplicate and storage.ride_hash_exists(fhash):
        raise DuplicateRideError(f"Already imported: {path.name}")

    prog(5, "Parsing FIT records")
    records, meta = fit_parser.parse_fit(path)
    if not records or len(records) < 20:
        raise ValueError("No usable GPS/record data in FIT file")
    if len(records) > 200000:
        raise ValueError("File too large (more than 200k records)")

    started_at = meta.get("start_time_unix") or records[0]["t"]
    ended_at = meta.get("end_time_unix") or records[-1]["t"]

    # Elevation stage.
    records, elev_summary = elevation.build_elevation(records, progress_cb=prog)

    # Weather at ride time + centroid.
    prog(60, "Fetching weather")
    clat = float(sum(r["lat"] for r in records) / len(records))
    clon = float(sum(r["lon"] for r in records) / len(records))
    weather = weather_mod.fetch_weather(clat, clon, started_at)

    # Power + uncertainty.
    prog(70, "Estimating power + uncertainty bands")
    records = power_mod.compute_power(records, rider, bike, weather)

    # Metrics.
    prog(85, "Computing metrics")
    ride_metrics = metrics_mod.compute_ride_metrics(records, rider, bike, elev_summary, meta)

    # Store.
    prog(93, "Storing")
    ride_data = {
        "bike_id": bike.get("id") or 1,
        "filename": name,
        "started_at": float(started_at),
        "ended_at": float(ended_at),
        "tz": "Europe/London" if geo.in_uk(clat, clon) else "UTC",
        "elevation_source": elev_summary["elevation_source"],
        "weather": weather,
        "metrics": ride_metrics,
        "records": records,
        "file_hash": fhash,
        "bike_calibrated": bool(bike.get("calibrated")),
    }
    ride_id = storage.insert_ride(ride_data)

    # Group this ride with any repeats of the same route.
    storage.recompute_routes()

    # Optional calibration from suitable segments.
    calib = try_auto_calibrate(ride_id, records, rider, bike, weather)

    # If the loop calibration recovered an effective wind (the wind that
    # actually balanced the coasting descents), re-run the power stage with
    # it: the estimate then uses the measured wind instead of the weather
    # forecast, which is exactly where flat-ride error lives.
    loop_fit = next((c for c in calib if c.get("type") == "loop"), None)
    if loop_fit and loop_fit.get("wind_recovered") and loop_fit.get("wind_mps") is not None:
        eff_weather = dict(weather)
        eff_weather["wind_speed_mps"] = loop_fit["wind_mps"]
        eff_weather["wind_dir_deg"] = loop_fit["wind_dir_deg"]
        records = power_mod.compute_power(records, rider, bike, eff_weather)
        ride_metrics = metrics_mod.compute_ride_metrics(
            records, rider, bike, elev_summary, meta)
        storage.update_ride_metrics(ride_id, ride_metrics, records)

    # Pooled cross-ride fit: with this ride now in the pool, fit a shared
    # (Crr, CdA) + per-ride wind across every ride's trusted coasts. When it
    # succeeds it applies to the bike and every ride is re-run with its own
    # recovered wind (and any stored pedal tags). Auto-run only while the
    # history is small; the manual button covers larger libraries.
    if len(storage.list_rides()) <= config.POOLED_AUTO_MAX_RIDES:
        pooled = try_pooled_calibrate(rider, bike)
        if pooled:
            _, calibrated_bike = storage.get_profile()
            recalculate_rides(rider, calibrated_bike or bike)

    prog(100, "Done")
    return {
        "ride_id": ride_id,
        "filename": name,
        "points": len(records),
        "elevation_source": elev_summary["elevation_source"],
        "gain_m": ride_metrics["elevation_gain_m"],
        "distance_m": ride_metrics["distance_m"],
        "duration_s": ride_metrics["duration_s"],
        "avg_watts": ride_metrics["avg_watts"],
        "trimp": ride_metrics["trimp"],
        "calibration": calib,
        "metrics": ride_metrics,
    }


def recalculate_rides(rider, bike):
    """Re-run power + metrics for every stored ride with a changed profile.

    Elevation and weather are not re-fetched (they are profile-independent and
    already stored), so this is cheap and synchronous.
    """
    results = []
    for row in storage.list_rides():
        ride_id = row["id"]
        ride = storage.get_ride(ride_id)
        if ride is None:
            continue
        records = storage.get_ride_records(ride_id)
        if not records:
            continue
        # Re-inject stored coast/pedal/brake tags so a profile change cannot
        # silently drop them (and so a manual "pedal" tag recovers descent
        # power — Phase 3).
        coast_mod.apply_segment_overrides(records, storage.get_coast_segments(ride_id))
        weather = ride.get("weather") or {}
        # If this ride's calibration recovered an effective wind, keep using
        # it (same as the import path) so a profile change cannot silently
        # revert the ride to the weather forecast wind.
        calib = storage.get_ride_calibration(ride_id)
        if calib and calib.get("wind_recovered") and calib.get("wind_mps") is not None:
            weather = dict(weather)
            weather["wind_speed_mps"] = calib["wind_mps"]
            weather["wind_dir_deg"] = calib["wind_dir_deg"]
        records = power_mod.compute_power(records, rider, bike, weather)
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
        results.append({
            "ride_id": ride_id,
            "avg_watts": metrics["avg_watts"],
            "trimp": metrics["trimp"],
            "vo2max": metrics["vo2max"],
        })
    return results


def try_auto_calibrate(ride_id, records, rider, bike, weather):
    """Run the loop calibration and the climb diagnostic on suitable segments.

    Only loop fits are applied to the bike; the climb result is recorded for
    visibility but never applied (see power.calibrate_climb). Fits whose
    parameters land outside physically-sane ranges are rejected, so a windy
    or brake-heavy descent cannot corrupt the bike profile.
    """
    results = []
    loop = power_mod.calibrate_loop(records, rider, bike, weather)
    if _acceptable_loop(loop):
        storage.save_calibration(ride_id, loop)
        results.append(loop)

    # The climb procedure is diagnostic only (see calibrate_climb): it is
    # recorded for visibility even when a loop fit also exists on this ride,
    # but it is never applied to the bike profile. The loop stays
    # authoritative (see storage.get_ride_calibration).
    climb = power_mod.calibrate_climb(records, rider, bike, weather)
    if _acceptable_climb(climb):
        storage.save_calibration(ride_id, climb)
        results.append(climb)
    return results


def _acceptable_loop(calib):
    if not calib:
        return False
    return (
        (calib.get("r2") or 0.0) > 0.6
        and calib.get("n_segments", 0) >= 3
        and 0.002 <= calib["crr"] <= 0.01
        and 0.2 <= calib["cdA"] <= 0.55
    )


def coast_model(max_hr):
    """(weights, training_features) for the caution-first classifier, fitted
    from the user's manual tags only (never auto labels). No tags => the
    cold-start prior with no training features."""
    tags = storage.list_manual_coast_segments()
    by_ride = {}
    for tag in tags:
        by_ride.setdefault(tag["ride_id"], []).append(tag)
    examples = []
    for ride_id, segs in by_ride.items():
        records = storage.get_ride_records(ride_id)
        examples.extend(coast_mod.build_training_examples(records, segs, max_hr))
    weights = coast_mod.fit_classifier(examples)
    return weights, [feats for feats, _ in examples]


def collect_coast_segments_by_ride(rider):
    """Effective (trusted) coast segments for every stored ride.

    Trust means a manual "coast" tag, or an untagged descent the
    caution-first classifier confidently auto-labels coast — never a manual
    "pedal"/"brake" and never the classifier's "ask"/"pedal" band. Returns
    {ride_id: [segments]}.
    """
    max_hr = rider.get("max_hr") or power_mod._default_max_hr(rider)
    weights, training_features = coast_model(max_hr)
    out = {}
    for row in storage.list_rides():
        ride_id = row["id"]
        records = storage.get_ride_records(ride_id)
        coast_mod.apply_segment_overrides(
            records, storage.get_coast_segments(ride_id))
        segs = coast_mod.effective_coast_segments(
            records, max_hr, weights, training_features)
        if segs:
            out[ride_id] = segs
    return out


def try_pooled_calibrate(rider, bike):
    """Fit a shared Crr/CdA + per-ride wind across every ride's trusted
    coasts and apply it when accepted. Returns the pooled dict or None when
    the pool is insufficient (fall back to the single-ride loop fit).

    Serialized: an import thread and the server's debounced background
    worker can both reach this, and the Nelder-Mead fit is heavy enough
    that two concurrent runs would saturate the machine.
    """
    with _pooled_fit_lock:
        segments_by_ride = collect_coast_segments_by_ride(rider)
        if len(segments_by_ride) < 2:
            return None
        weather_by_ride = {}
        for ride_id in segments_by_ride:
            ride = storage.get_ride(ride_id)
            weather_by_ride[ride_id] = (ride or {}).get("weather") or {}
        pooled = power_mod.calibrate_pooled(
            segments_by_ride, rider, bike, weather_by_ride)
        if not _acceptable_pooled(pooled):
            return None
        _save_pooled_calibration(pooled)
        return pooled


def _acceptable_pooled(calib):
    if not calib:
        return False
    return (
        (calib.get("r2") or 0.0) > 0.6
        and calib.get("n_rides", 0) >= 2
        and calib.get("n_segments", 0) >= 3
        and 0.002 <= calib["crr"] <= 0.01
        and 0.2 <= calib["cdA"] <= 0.55
    )


def _save_pooled_calibration(pooled):
    """Store one calibration row per contributing ride so each ride can
    re-apply its own recovered wind via storage.get_ride_calibration."""
    per_ride = pooled.get("per_ride_wind") or {}
    for ride_id, wind in per_ride.items():
        storage.save_calibration(ride_id, {
            "type": "pooled",
            "crr": pooled["crr"],
            "cdA": pooled["cdA"],
            "r2": pooled["r2"],
            "n_segments": pooled["n_segments"],
            "n_points": pooled["n_points"],
            "n_rides": pooled["n_rides"],
            "wind_recovered": True,
            "wind_mps": wind["wind_mps"],
            "wind_dir_deg": wind["wind_dir_deg"],
            "crr_sigma": pooled.get("crr_sigma"),
            "cdA_sigma": pooled.get("cdA_sigma"),
        })


def _acceptable_climb(calib):
    """Whether a climb diagnostic is worth recording.

    The climb result is never applied to the bike (see calibrate_climb), so
    this is a recording gate, not an apply gate: it keeps results with enough
    segments and a finite, positive implied Crr, but deliberately allows a
    wider band than _acceptable_loop because the climb solve ignores wind and
    acceleration and can legitimately overshoot the assumed Crr on a gusty or
    accelerating climb.
    """
    if not calib:
        return False
    crr = calib["crr"]
    return (
        calib.get("n_segments", 0) >= 2
        and math.isfinite(crr)
        and 0.0005 <= crr <= 0.05
    )


class DuplicateRideError(ValueError):
    pass
