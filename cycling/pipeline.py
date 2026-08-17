"""The per-file import pipeline.

.fit -> parse -> map-match -> sample DTM -> smooth -> grade -> weather ->
power + uncertainty -> metrics -> SQLite. Any individual stage degrades
gracefully; the only hard failures are "not a FIT file" and "no GPS points".
"""

import hashlib
import pathlib
import time

from . import config, elevation, geo, metrics as metrics_mod, power as power_mod
from . import storage, weather as weather_mod


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
        weather = ride.get("weather") or {}
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
    """Run the two calibration procedures on suitable segments; keep good fits.

    Only fits whose parameters land in physically-sane ranges are applied, so a
    windy or brake-heavy descent cannot corrupt the bike profile.
    """
    results = []
    loop = power_mod.calibrate_loop(records, rider, bike, weather)
    if _acceptable_loop(loop):
        storage.save_calibration(ride_id, loop)
        results.append(loop)

    climb = power_mod.calibrate_climb(records, rider, bike, weather)
    if _acceptable_climb(climb) and not results:
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


def _acceptable_climb(calib):
    if not calib:
        return False
    return (
        calib.get("n_segments", 0) >= 2
        and 0.002 <= calib["crr"] <= 0.01
    )


class DuplicateRideError(ValueError):
    pass
