"""Standalone backend sanity checks (no pytest needed: ``python tests/verify_backend.py``).

Three things the plan promised and this repo now implements:

1. Loop-CdA calibration closes the energy budget on coasting descents and
   recovers a known (crr, CdA) from synthetic physics.
2. Route detection groups jittered repeats of the same loop — including the
   same loop ridden in reverse — while keeping different routes apart.
3. Cardiac drift recovers a known HR rise during steady estimated power, and
   stays quiet when power is not steady.

Each check is a plain ``assert`` so failures are loud.
"""

import math
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

from cycling import config, metrics as metrics_mod, power as power_mod, routes as routes_mod

G = 9.80665
MASS = 75.0 + 9.0 + 2.0  # rider + bike + kit (matches power.KIT_MASS_KG)


def make_loop_points(center=(52.0, -1.5), size_km=5.0, n=400, noise_m=5.0):
    """A rectangular loop of road around ``center`` with GPS-ish noise."""
    lat0, lon0 = center
    # Local metre->deg scale.
    lat_deg_per_m = 1.0 / 111320.0
    lon_deg_per_m = 1.0 / (111320.0 * math.cos(math.radians(lat0)))
    s = size_km * 1000.0
    # Rectangle perimeter, clockwise.
    corners = [(0, 0), (s, 0), (s, s), (0, s), (0, 0)]
    raw = []
    for (x1, y1), (x2, y2) in zip(corners, corners[1:]):
        seg_len = math.hypot(x2 - x1, y2 - y1)
        steps = max(2, int(seg_len / (4.0 * size_km)))  # ~4 m spacing
        for i in range(steps):
            t = i / steps
            raw.append((x1 + (x2 - x1) * t, y1 + (y2 - y1) * t))
    if len(raw) < n:
        raw = raw * (n // len(raw) + 1)
    # Resample to exactly n points evenly along the loop.
    cum = np.cumsum([0.0] + [math.hypot(raw[i + 1][0] - raw[i][0], raw[i + 1][1] - raw[i][1])
                             for i in range(len(raw) - 1)])
    targets = np.linspace(0.0, cum[-1], n)
    xs = np.interp(targets, cum, [p[0] for p in raw])
    ys = np.interp(targets, cum, [p[1] for p in raw])
    rng = random.Random(42)
    pts = []
    for x, y in zip(xs, ys):
        ang = rng.uniform(0, 2 * math.pi)
        r = rng.gauss(0, noise_m)
        pts.append((lat0 + (y + r * math.sin(ang)) * lat_deg_per_m,
                    lon0 + (x + r * math.cos(ang)) * lon_deg_per_m))
    return pts


def test_compressed_timestamp_fit():
    """Compressed-timestamp record messages (a real Wahoo/Garmin FIT feature)
    must parse to Unix timestamps without desyncing the message stream.

    Regression: the compressed branch once stored FIT-epoch seconds (~1970)
    while full-timestamp records stored Unix seconds, so mixed files sorted
    out of order and rides came out ~20 years long; the payload size also
    counted the omitted timestamp field, eating the next message's bytes.
    """
    import struct
    import tempfile
    from pathlib import Path

    from cycling import fit_parser

    defn = (bytes([0x40, 0x00, 0x00]) + struct.pack("<H", 20) + bytes([3])
            + bytes([0, 4, 0x05, 1, 4, 0x05, 253, 4, 0x0C]))
    lat, lon = 520000000, -15000000  # ~52N, -1.5E
    data_full = bytes([0x00]) + struct.pack("<iiI", lat, lon, 100)
    data_compressed = bytes([0x80 | 0x05]) + struct.pack("<ii", lat, lon)
    data_trailing = bytes([0x00]) + struct.pack("<iiI", lat, lon, 107)
    section = defn + data_full + data_compressed + data_trailing
    header = (bytes([14, 0x10]) + struct.pack("<H", 0x0010)
              + struct.pack("<I", len(section)) + b".FIT" + struct.pack("<H", 0))

    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "compressed.fit"
        p.write_bytes(header + section)
        records, _ = fit_parser.parse_fit(p)

    assert len(records) == 3, f"message stream desynced: {len(records)} records"
    ts = [r["t"] for r in records]
    assert all(6e8 < t < 7e8 for t in ts), f"timestamps not Unix-epoch: {ts}"
    assert ts == sorted(ts), "records out of chronological order"
    assert ts[-1] - ts[0] == 7, f"wrong ride duration: {ts[-1] - ts[0]}s"
    print(f"compressed-timestamp FIT OK: {ts[0]} -> {ts[-1]} ({ts[-1] - ts[0]}s)")


def test_route_detection():
    loop_a = make_loop_points(noise_m=5.0)
    loop_b = make_loop_points(noise_m=6.0)      # same loop, different noise
    loop_c = list(reversed(make_loop_points(noise_m=5.0, n=360)))  # reverse direction
    other = make_loop_points(center=(52.35, -1.0), size_km=8.0)    # different roads
    downtown = make_loop_points(center=(52.02, -1.51), size_km=1.2)  # different shape

    fps = [(1, routes_mod.fingerprint(*zip(*loop_a))),
           (2, routes_mod.fingerprint(*zip(*loop_b))),
           (3, routes_mod.fingerprint(*zip(*loop_c))),
           (4, routes_mod.fingerprint(*zip(*other))),
           (5, routes_mod.fingerprint(*zip(*downtown)))]

    groups = routes_mod.group_rides(fps)
    by_ref = {g["ref_ride_id"]: set(g["ride_ids"]) for g in groups}

    same = next((set(g["ride_ids"]) for g in groups
                 if 1 in g["ride_ids"] and 2 in g["ride_ids"]), None)
    assert same is not None, "rides 1+2 should share a route"
    assert 3 in same, "reversed loop should match the same route"
    assert 4 not in same and 5 not in same, "different routes must not group"
    # Every ride must be assigned exactly once.
    assigned = [rid for g in groups for rid in g["ride_ids"]]
    assert sorted(assigned) == [1, 2, 3, 4, 5]
    print(f"route detection OK: {len(groups)} routes, same-route sizes {sorted(len(g['ride_ids']) for g in groups)}")


def make_coast_ride(segments, max_hr=180.0):
    """Synthetic ride: climbing pauses separating coasting descents.

    ``segments``: list of (grade, speed_mps, duration_s). Descents have
    negative grade so find_coast_segments picks them up.
    """
    records = []
    t = 0.0
    dist = 0.0
    elev = 120.0
    for i, (grade, v, dur) in enumerate(segments):
        n = max(2, int(round(dur)))
        for j in range(n):
            dt = dur / n
            rec = {
                "t": t, "lat": 52.0 + 0.0001 * i, "lon": -1.5 + 0.0001 * j,
                "elev": elev, "grade": grade, "speed": v, "dist": dist,
                "hr": 90.0 if grade < 0 else 155.0,
            }
            records.append(rec)
            elev += grade * v * dt
            dist += v * dt
            t += dt
    return records


def test_loop_calibration():
    rider = {"weight_kg": 75.0, "max_hr": 180}
    bike = {"mass_kg": 9.0, "cdA": 0.35, "crr": 0.005}
    weather = {"temp_c": 15.0, "pressure_hpa": 1013.0, "wind_speed_mps": 0.0}

    rho = power_mod.weather_air_density(weather)
    true_crr, true_cdA = 0.005, 0.35
    # Equilibrium coasts: at a given speed the descent grade exactly balances
    # rolling + aero losses (no braking, no pedalling), so the energy budget
    # closes: m·g·dh = crr·m·g·ds + 0.5·ρ·CdA·∫v³dt.
    def coast_grade(v):
        return -(true_crr + 0.5 * rho * true_cdA * v * v / (MASS * G))

    segs = [
        (0.05, 5.0, 90),                        # climb to separate segments
        (coast_grade(7.0), 7.0, 120),           # descent A
        (0.05, 5.0, 90),
        (coast_grade(5.0), 5.0, 160),           # descent B (slow -> rolling-heavy)
        (0.04, 4.5, 80),
        (coast_grade(10.0), 10.0, 90),          # descent C (fast -> aero-heavy)
        (0.05, 5.0, 90),
        (coast_grade(8.0), 8.0, 110),           # descent D
    ]
    records = make_coast_ride(segs)

    # Elevation follows the grade exactly (what the power model assumes).
    for i in range(1, len(records)):
        a, b = records[i - 1], records[i]
        grade = (a["grade"] + b["grade"]) / 2.0
        v = (a["speed"] + b["speed"]) / 2.0
        dt = b["t"] - a["t"]
        b["dist"] = a["dist"] + v * dt
        b["elev"] = a["elev"] + grade * v * dt

    calib = power_mod.calibrate_loop(records, rider, bike, weather)
    assert calib is not None, "loop calibration should find coast segments"
    assert calib["n_segments"] >= 3, calib
    # Recovered params should be near the injected 0.005 / 0.35.
    assert 0.0035 <= calib["crr"] <= 0.0065, f"crr={calib['crr']:.5f}"
    assert 0.25 <= calib["cdA"] <= 0.45, f"cdA={calib['cdA']:.3f}"
    assert calib["r2"] > 0.9, calib
    print(f"loop calibration OK: crr={calib['crr']:.5f} (true {true_crr}), "
          f"cdA={calib['cdA']:.3f} (true {true_cdA}), r2={calib['r2']:.3f}, "
          f"n_segments={calib['n_segments']}")


def test_cardiac_drift():
    rider = {"max_hr": 180, "age": 40}
    records = []
    t = 0.0
    # 35 min steady: constant estimated power, HR drifting up 15 bpm over 30 min.
    start_hr = 128.0
    rng = random.Random(7)
    for i in range(35 * 60):
        frac = min(1.0, max(0.0, (t - 120) / 1800.0))  # drift between 2 and 32 min
        rec = {
            "t": t, "lat": 52.0, "lon": -1.5, "elev": 100.0, "grade": 0.005,
            "speed": 8.0, "dist": t * 8.0, "mode": "pedal",
            "watts_est": 200.0 + rng.gauss(0, 6.0),
            "hr": start_hr + 15.0 * frac + rng.gauss(0, 0.8),
        }
        records.append(rec)
        t += 1.0

    d = metrics_mod.cardiac_drift(records, rider)
    assert d is not None, "steady ride should produce a drift window"
    # 15 bpm over 30 min = 30 bpm/hour.
    assert abs(d["drift_bpm_per_hr"] - 30.0) < 6.0, d
    assert d["duration_min"] >= 28, d
    print(f"cardiac drift OK: {d['drift_bpm_per_hr']} bpm/hr (true 30), "
          f"pct {d['drift_pct_per_hr']}%/hr, {d['duration_min']} min window")

    # Unsteady power must NOT produce a drift reading. A slow surge (5-min
    # period, big amplitude) survives 60 s smoothing and must still fail.
    noisy = [dict(r) for r in records]
    for i, r in enumerate(noisy):
        r["watts_est"] = 200.0 + 80.0 * math.sin(2 * math.pi * i / 300.0)
    assert metrics_mod.cardiac_drift(noisy, rider) is None, \
        "unsteady power must not be reported as drift"


def make_steady_ride(seconds=3600, watts=200.0, hr=150.0, include_hr=True):
    """Synthetic 1 Hz ride at constant power (and optionally constant HR)."""
    records = []
    for i in range(seconds):
        rec = {
            "t": float(i), "lat": 52.0, "lon": -1.5, "elev": 100.0,
            "grade": 0.0, "speed": 8.0, "dist": i * 8.0, "mode": "pedal",
            "watts_est": watts, "watts_lo": watts * 0.8, "watts_hi": watts * 1.3,
        }
        if include_hr:
            rec["hr"] = hr
        records.append(rec)
    return records


def test_calories():
    from cycling import metrics as metrics_mod

    kj_per_kcal = metrics_mod.KJ_PER_KCAL
    efficiency = metrics_mod.GROSS_EFFICIENCY
    rider = {"weight_kg": 75.0, "age": 40, "sex": "male"}

    # --- Work-based: 200 W for 1 h = 720 kJ of mechanical work. Food kcal
    # --- comes from dividing by 4.184 kJ/kcal and gross efficiency.
    rec = make_steady_ride(seconds=3600, watts=200.0, include_hr=False)
    cal = metrics_mod.estimate_calories(rec, rider)
    assert cal is not None and cal["method"] == "power", cal
    expected_kcal = 200.0 * 3600 / 1000 / kj_per_kcal / efficiency
    assert abs(cal["kcal"] - expected_kcal) < 1.0, (cal, expected_kcal)
    # The uncertainty band follows the injected watts_lo/watts_hi.
    assert abs(cal["power_lo"] - expected_kcal * 0.8) < 1.0
    assert abs(cal["power_hi"] - expected_kcal * 1.3) < 1.0
    assert cal["hr_kcal"] is None

    # --- HR-based (Keytel male): 150 bpm for 1 h at 75 kg / 40 y.
    rec = make_steady_ride(seconds=3600, watts=200.0, hr=150.0, include_hr=True)
    cal = metrics_mod.estimate_calories(rec, rider)
    assert cal["method"] == "hr", cal
    kj_min = -55.0969 + 0.6309 * 150.0 + 0.1988 * 75.0 + 0.2017 * 40.0
    expected_hr_kcal = kj_min * 60.0 / kj_per_kcal
    assert abs(cal["kcal"] - expected_hr_kcal) < 2.0, (cal, expected_hr_kcal)
    assert cal["hr_coverage"] == 1.0
    assert cal["lo"] < cal["kcal"] < cal["hi"]
    assert cal["power_kcal"] > 0  # cross-check always reported

    # --- HR-derived average power cross-check (watts = kcal*4.184*0.239*1000/s).
    hp = cal["hr_power"]
    assert hp is not None, cal
    watts_hr = expected_hr_kcal * metrics_mod.KJ_PER_KCAL \
        * metrics_mod.GROSS_EFFICIENCY * 1000.0 / 3600.0
    assert abs(hp["watts"] - watts_hr) < 1.0, (hp, watts_hr)
    assert hp["lo"] < hp["watts"] < hp["hi"], hp
    assert hp["lo"] > watts_hr * 0.7 and hp["hi"] < watts_hr * 1.25, hp

    # --- Sex affects the estimate; unknown sex widens the band.
    female = metrics_mod.estimate_calories(
        make_steady_ride(seconds=3600, hr=150.0), {"weight_kg": 75.0, "age": 40, "sex": "female"})
    unknown = metrics_mod.estimate_calories(
        make_steady_ride(seconds=3600, hr=150.0), {"weight_kg": 75.0, "age": 40})
    assert female["kcal"] < cal["kcal"], (female, cal)  # female eq is lower at same HR
    assert unknown["method"] == "hr"
    assert (unknown["hi"] - unknown["lo"]) > (cal["hi"] - cal["lo"])
    # The unknown-sex HR-power band is wider than the known-sex one.
    assert (unknown["hr_power"]["hi"] - unknown["hr_power"]["lo"]) \
        > (cal["hr_power"]["hi"] - cal["hr_power"]["lo"])

    # --- No records -> None; no HR -> no hr_power cross-check.
    assert metrics_mod.estimate_calories([], rider) is None
    no_hr = metrics_mod.estimate_calories(
        make_steady_ride(seconds=3600, include_hr=False), rider)
    assert no_hr["hr_power"] is None and no_hr["hr_kcal"] is None
    print(f"calories OK: power-based {expected_kcal:.0f} kcal, "
          f"HR-based {expected_hr_kcal:.0f} kcal, female {female['kcal']} kcal")


def test_pause_moving_time_and_coverage():
    """Auto-pause gaps: duration comes from the device moving time, and HR
    coverage divides by moving time rather than the wall-clock record span.

    Regression: duration was the last-first record timestamp (pauses included),
    so a paused ride showed elapsed time as "moving time" and HR coverage read
    low against that same span.
    """
    from cycling import fit_parser

    # Duration: records span 4200 s wall-clock but the device timer reports
    # 3600 s of moving time (a 10-minute auto-pause).
    records = [{"t": 0.0}, {"t": 4200.0}]
    meta = {"total_timer": 3600.0, "total_distance": 20000.0}
    fit_parser._finalize_meta(meta, records)
    assert meta["duration_seconds"] == 3600.0, meta
    # A missing/zero timer falls back to the record span.
    no_timer = {"total_timer": 0.0, "total_distance": 20000.0}
    fit_parser._finalize_meta(no_timer, records)
    assert no_timer["duration_seconds"] == 4200.0, no_timer

    # HR coverage: 1 h of 1 Hz HR with a 10-minute auto-pause gap.
    moving, pause = 3600.0, 600.0
    recs = make_steady_ride(seconds=3600, hr=150.0)
    for r in recs:
        if r["t"] >= 1800.0:
            r["t"] += pause
    cal = metrics_mod.estimate_calories(recs, {"weight_kg": 75.0, "age": 40, "sex": "male"},
                                        duration=moving)
    # HR covers every moving second (~1.0); the same signal over the 4200 s
    # elapsed span would read ~0.86.
    assert cal["hr_coverage"] >= 0.99, cal
    print(f"pause moving time OK: duration {meta['duration_seconds']:.0f}s "
          f"vs 4200s elapsed, HR coverage {cal['hr_coverage']:.2f}")


def test_elevation_gain_shallow_climb():
    """Elevation gain must count a 2% climb. The old 1 m / 25 m threshold was
    a 4% grade cutoff, so every 1-4% climb contributed zero."""
    from cycling import elevation as elevation_mod
    dist = np.linspace(0.0, 500.0, 200)
    elev = 0.02 * dist  # steady 2% grade = 10 m rise over 500 m
    gain = elevation_mod._elevation_gain(dist, elev, threshold=config.ELEVATION_GAIN_THRESHOLD)
    assert 9.0 <= gain <= 10.5, gain
    print(f"elevation gain OK: 2% climb -> {gain:.1f} m (expected ~10)")


def test_trimp_sex():
    """TRIMP must use Banister's female constants for female riders."""
    records = make_steady_ride(seconds=3600, watts=200.0, hr=150.0, include_hr=True)
    male = metrics_mod._hr_metrics(records, {"age": 40, "resting_hr": 55, "max_hr": 180, "sex": "male"})
    female = metrics_mod._hr_metrics(records, {"age": 40, "resting_hr": 55, "max_hr": 180, "sex": "female"})
    assert male["trimp"] > 0 and female["trimp"] > 0
    assert abs(female["trimp"] - male["trimp"]) > 1e-6, (male["trimp"], female["trimp"])
    print(f"TRIMP sex OK: male {male['trimp']:.1f}, female {female['trimp']:.1f}")


def test_wind_direction_wrap():
    """Hourly wind direction must interpolate across north along the short
    arc (350° -> 10° passes through 0°, never through 180°)."""
    weather = {"wind_speed_mps": 5.0, "wind_dir_deg": 0.0, "ride_hour": 0,
               "wind_speed_hourly": [5.0, 5.0, 5.0],
               "wind_dir_hourly": [350.0, 10.0, 20.0]}
    records = [{"t": 0.0}, {"t": 1800.0}, {"t": 3600.0}]
    ws, wd = power_mod._per_point_wind(records, weather, 3)
    assert wd[0] > 340.0 and wd[0] < 360.0, wd
    assert abs(wd[1]) < 5.0 or abs(wd[1] - 360.0) < 5.0, wd  # ~0° midpoint
    assert wd[2] > 0.0 and wd[2] < 20.0, wd
    print(f"wind wrap OK: dir {wd[0]:.1f} -> {wd[1]:.1f} -> {wd[2]:.1f}")


def test_non_uk_weather_date():
    """A non-UK ride whose UTC instant falls on a different local date must
    fetch weather for the ride's local date, not the importing machine's."""
    import datetime
    from unittest import mock
    from cycling import weather as weather_mod

    # New York (UTC-5 standard time): 2024-01-02 02:00 UTC is still
    # 2024-01-01 21:00 locally, so the UTC date differs from the ride's
    # local date and the request must target the latter.
    when_unix = datetime.datetime(2024, 1, 2, 2, 0, 0,
                                  tzinfo=datetime.timezone.utc).timestamp()

    calls = []

    class _FakeResp:
        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            pass

        def json(self):
            return self._payload

    def _fake_get(url, params=None, timeout=None):
        params = dict(params or {})
        calls.append(params)
        if params.get("hourly") == "temperature_2m":
            # The timezone-resolution probe.
            return _FakeResp({"timezone": "America/New_York",
                              "hourly": {"time": ["2020-01-01T00:00"],
                                         "temperature_2m": [0.0]}})
        start = params.get("start_date")
        return _FakeResp({
            "timezone": "America/New_York",
            "hourly": {
                "time": [f"{start}T{i:02d}:00" for i in range(24)],
                "temperature_2m": [5.0] * 24,
                "wind_speed_10m": [0.0] * 24,
                "wind_direction_10m": [0.0] * 24,
                "pressure_msl": [1013.0] * 24,
                "wind_gusts_10m": [0.0] * 24,
            },
        })

    weather_mod._TZ_CACHE.clear()
    with mock.patch.object(weather_mod.requests, "get", side_effect=_fake_get), \
            mock.patch.object(weather_mod, "_cache", return_value={}), \
            mock.patch.object(weather_mod, "_save_cache"):
        result = weather_mod.fetch_weather(40.71, -74.01, when_unix)

    real = [c for c in calls if c.get("hourly") != "temperature_2m"]
    assert real, "expected a real fetch after the timezone probe"
    assert real[0]["start_date"] == "2024-01-01", real[0]
    assert real[0]["end_date"] == "2024-01-01", real[0]
    assert real[0]["timezone"] == "America/New_York", real[0]
    assert result["ride_hour"] == 21, result["ride_hour"]
    assert result["source"] == "open-meteo"
    print(f"non-UK weather date OK: fetched {real[0]['start_date']} "
          f"(ride local), ride_hour {result['ride_hour']}")


def test_tag_save_refreshes_stored_power():
    """Saving a manual 'pedal' tag must refresh the stored power points for
    that ride immediately — even when no wind-recovering loop fit succeeds
    (e.g. a single-direction descent). Regression: stored watts stayed 0 W /
    mode='coast' inside the tagged window until some unrelated recalculation
    happened to run, because the refresh was gated on wind recovery."""
    import pathlib
    import shutil
    import tempfile
    from unittest import mock

    from cycling import coast as coast_mod, config, metrics as metrics_mod
    from cycling import server, storage

    rider = {"weight_kg": 75.0, "max_hr": 180, "age": 40,
             "resting_hr": 55, "sex": "male"}
    bike = {"mass_kg": 9.0, "cdA": 0.35, "crr": 0.005,
            "drivetrain_efficiency": 0.97, "calibrated": False}
    weather = {"temp_c": 15.0, "pressure_hpa": 1013.0,
               "wind_speed_mps": 0.0, "wind_dir_deg": 0.0}

    # One climb + ONE descent: calibrate_loop needs >=2 segments, so no
    # authoritative fit can succeed here — exactly the stale-tag scenario.
    segs = [(0.05, 5.0, 90), (-0.02, 6.0, 80)]
    t0 = 1_700_000_000.0
    records = make_coast_ride(segs)
    for r in records:
        r["t"] += t0
        r["lat"] = 52.0 + (r["t"] - t0) * 1e-5   # single heading (north)
    for i in range(1, len(records)):
        a, b = records[i - 1], records[i]
        b["dist"] = a["dist"] + (a["speed"] + b["speed"]) / 2 * (b["t"] - a["t"])
        b["elev"] = a["elev"] + (a["grade"] + b["grade"]) / 2 * (
            b["speed"] * (b["t"] - a["t"]))

    powered = power_mod.compute_power([dict(r) for r in records], rider, bike, weather)
    elev_summary = {"gain_m": 10.0, "min_elev": 90.0, "max_elev": 110.0,
                    "elevation_source": "device", "snapped_ratio": 0.0}
    meta = {"total_distance": records[-1]["dist"],
            "duration_seconds": records[-1]["t"] - records[0]["t"]}
    mets = metrics_mod.compute_ride_metrics(powered, rider, bike, elev_summary, meta)

    tmp = pathlib.Path(tempfile.mkdtemp(prefix="cycling_tagpower_"))
    orig_db = config.DB_PATH
    try:
        config.DB_PATH = tmp / "cycling.db"
        storage._conn = None
        storage.init_db()
        rid = storage.insert_ride({
            "bike_id": 1, "filename": "tag-refresh.fit",
            "started_at": t0, "ended_at": records[-1]["t"], "tz": "UTC",
            "elevation_source": "device", "weather": weather,
            "metrics": mets, "records": powered,
            "file_hash": "tag-refresh-hash", "bike_calibrated": False,
        })

        desc = coast_mod.find_descent_segments(records)
        assert len(desc) == 1, desc
        d = desc[0]

        # The background pooled refit must not run during this unit test.
        with mock.patch.object(server, "_schedule_pooled_recalibration"):
            resp = server.save_coast_tag(rid, {
                "t_start": d["t_start"], "t_end": d["t_end"], "label": "pedal"})
        assert resp.get("ok"), resp

        rows = [dict(r) for r in storage._connect().execute(
            "SELECT t, watts_est, mode FROM power_point WHERE ride_id=? "
            "AND t>=? AND t<=?", (rid, d["t_start"], d["t_end"]))]
        assert rows, "no power points inside the tagged window"
        n_pedal = sum(1 for r in rows if r["mode"] == "pedal")
        assert n_pedal >= len(rows) * 0.8, \
            f"stored power not refreshed by tag save: {rows[:5]}"
        assert any((r["watts_est"] or 0) > 0 for r in rows), rows[:5]

        # And clearing the tag reverts the window to zeroed coasts again.
        with mock.patch.object(server, "_schedule_pooled_recalibration"):
            server.save_coast_tag(rid, {
                "t_start": d["t_start"], "t_end": d["t_end"], "label": None})
        rows2 = [dict(r) for r in storage._connect().execute(
            "SELECT watts_est, mode FROM power_point WHERE ride_id=? "
            "AND t>=? AND t<=?", (rid, d["t_start"], d["t_end"]))]
        assert all(r["mode"] == "coast" and (r["watts_est"] or 0) == 0.0
                   for r in rows2), rows2[:5]

        # Tag saves must not spam climb-diagnostic rows: the climb procedure
        # ignores tags entirely, so re-running it per click only duplicated
        # identical rows (19 of them once existed in a real database).
        n_climb = storage._connect().execute(
            "SELECT COUNT(*) FROM calibration WHERE ride_id=? AND type='climb'",
            (rid,)).fetchone()[0]
        assert n_climb == 0, f"tag save appended {n_climb} climb diagnostics"
        print("tag-save power refresh OK: pedal tag rewrote stored watts "
              "immediately without a wind-recovering fit, and no climb "
              "diagnostic rows were appended")
    finally:
        conn = getattr(storage, "_conn", None)
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
        storage._conn = None
        config.DB_PATH = orig_db
        shutil.rmtree(tmp, ignore_errors=True)


def test_both_segments_calibration_recorded():
    """A ride with both a valid loop and a climb diagnostic must record both,
    while the loop stays authoritative (get_ride_calibration) and only the
    loop marks the bike calibrated."""
    import pathlib
    import shutil
    import tempfile
    from unittest import mock
    from cycling import config, pipeline, storage

    tmp = pathlib.Path(tempfile.mkdtemp(prefix="cycling_test_"))
    orig_db = config.DB_PATH
    try:
        config.DB_PATH = tmp / "cycling.db"
        storage._conn = None
        storage.init_db()
        storage.save_profile(
            {"age": 40, "weight_kg": 75.0, "height_cm": 178.0,
             "bike_type": "road", "sex": None, "resting_hr": 55,
             "max_hr": 180, "hr_zones": None},
            {"id": 1, "name": "Road bike", "mass_kg": 9.0, "crr": 0.005,
             "cdA": 0.35, "drivetrain_efficiency": 0.97,
             "calibrated": False},
        )
        loop = {"type": "loop", "crr": 0.0045, "cdA": 0.33, "r2": 0.9,
                "n_segments": 4, "n_points": 400, "wind_recovered": True,
                "wind_mps": 3.0, "wind_dir_deg": 90.0,
                "crr_sigma": 0.0005, "cdA_sigma": 0.01}
        # 0.0105 is a climb echo that overshoots the assumed Crr (wind +
        # acceleration are ignored in the climb solve); it must still be
        # recorded because the diagnostic is never applied to the bike.
        climb = {"type": "climb", "crr": 0.0105, "cdA": 0.35, "r2": None,
                 "n_segments": 2, "n_points": 100, "crr_sigma": 0.0002,
                 "diagnostic": True}
        with mock.patch.object(pipeline.power_mod, "calibrate_loop",
                               return_value=loop), \
                mock.patch.object(pipeline.power_mod, "calibrate_climb",
                                  return_value=climb):
            results = pipeline.try_auto_calibrate(7, [], {}, {}, {})

        types = sorted(r["type"] for r in storage.list_calibrations())
        assert types == ["climb", "loop"], types
        assert sorted(r["type"] for r in results) == ["climb", "loop"]

        auth = storage.get_ride_calibration(7)
        assert auth is not None and auth["type"] == "loop", auth
        assert auth.get("wind_recovered") is True  # loop's wind is not shadowed

        _, bike = storage.get_profile()
        assert bike["calibrated"] == 1
        assert abs(bike["crr"] - 0.0045) < 1e-9  # loop applied, climb did not
        print(f"both-segments calibration OK: recorded {types}, "
              f"authoritative {auth['type']}")
    finally:
        conn = getattr(storage, "_conn", None)
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
        storage._conn = None
        config.DB_PATH = orig_db
        shutil.rmtree(tmp, ignore_errors=True)


def test_coast_classification_and_storage():
    """Phase 0 verified-coast: descents classify coast/pedal/ask with a
    caution bias, and manual tags round-trip through the coast_segment table
    and are removed with the ride."""
    import pathlib
    import shutil
    import tempfile

    from cycling import coast as coast_mod
    from cycling import config, storage

    def descent(hr, n=40):
        return [{"t": float(i), "lat": 52.0, "lon": -1.5, "elev": 100.0,
                 "grade": -0.05, "speed": 8.0, "dist": float(i) * 8.0,
                 "hr": hr} for i in range(n)]

    coast = coast_mod.classify_descents(descent(90.0), max_hr=180)
    pedal = coast_mod.classify_descents(descent(160.0), max_hr=180)
    ask = coast_mod.classify_descents(descent(125.0), max_hr=180)
    assert coast[0]["label"] == "coast" and coast[0]["score"] >= 0.9, coast
    assert pedal[0]["label"] == "pedal" and pedal[0]["score"] <= 0.1, pedal
    assert ask[0]["label"] == "ask", ask

    tmp = pathlib.Path(tempfile.mkdtemp(prefix="cycling_coast_"))
    orig_db = config.DB_PATH
    try:
        config.DB_PATH = tmp / "cycling.db"
        storage._conn = None
        storage.init_db()
        storage.save_coast_segments(7, [
            {"t_start": 100.0, "t_end": 150.0, "label": "coast", "source": "manual"},
            {"t_start": 300.0, "t_end": 340.0, "label": "pedal", "source": "manual"},
        ])
        rows = storage.get_coast_segments(7)
        assert [(r["label"], r["source"]) for r in rows] == \
            [("coast", "manual"), ("pedal", "manual")], rows
        storage.delete_ride(7)
        assert storage.get_coast_segments(7) == []
    finally:
        conn = getattr(storage, "_conn", None)
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
        storage._conn = None
        config.DB_PATH = orig_db
        shutil.rmtree(tmp, ignore_errors=True)
    print("coast classification + storage OK: coast/pedal/ask binning, "
          "manual tags round-trip and delete with the ride")


def test_manual_tag_changes_calibration_coasts():
    """A manual 'pedal' tag must remove that descent from the loop fit's
    coast set, and the tag must survive a profile recalculation."""
    import pathlib
    import shutil
    import tempfile

    from cycling import coast as coast_mod
    from cycling import config, pipeline, storage

    rider = {"weight_kg": 75.0, "max_hr": 180}
    bike = {"mass_kg": 9.0, "cdA": 0.35, "crr": 0.005}
    weather = {"temp_c": 15.0, "pressure_hpa": 1013.0, "wind_speed_mps": 0.0}
    rho = power_mod.weather_air_density(weather)
    true_crr, true_cdA = 0.005, 0.35

    def coast_grade(v):
        return -(true_crr + 0.5 * rho * true_cdA * v * v / (MASS * G))

    segs = [
        (0.05, 5.0, 90),
        (coast_grade(7.0), 7.0, 120),
        (0.05, 5.0, 90),
        (coast_grade(5.0), 5.0, 160),
        (0.04, 4.5, 80),
        (coast_grade(10.0), 10.0, 90),
        (0.05, 5.0, 90),
        (coast_grade(8.0), 8.0, 110),
    ]
    records = make_coast_ride(segs)
    for i in range(1, len(records)):
        a, b = records[i - 1], records[i]
        grade = (a["grade"] + b["grade"]) / 2.0
        v = (a["speed"] + b["speed"]) / 2.0
        dt = b["t"] - a["t"]
        b["dist"] = a["dist"] + v * dt
        b["elev"] = a["elev"] + grade * v * dt

    base = power_mod.calibrate_loop(records, rider, bike, weather)
    assert base is not None and base["n_segments"] >= 3, base

    desc = coast_mod.find_descent_segments(records)
    assert len(desc) >= 3, desc
    first = desc[0]
    coast_mod.apply_segment_overrides(records, [
        {"t_start": first["t_start"], "t_end": first["t_end"], "label": "pedal"}])

    tagged = power_mod.calibrate_loop(records, rider, bike, weather)
    assert tagged is not None and tagged["n_segments"] < base["n_segments"], tagged

    # Persist the tag and prove it survives a profile recalculation (the
    # coast_segment table is not rewritten by recalculate_rides).
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="cycling_tag_"))
    orig_db = config.DB_PATH
    try:
        config.DB_PATH = tmp / "cycling.db"
        storage._conn = None
        storage.init_db()
        storage.save_coast_segments(7, [
            {"t_start": first["t_start"], "t_end": first["t_end"],
             "label": "pedal", "source": "manual"}])
        pipeline.recalculate_rides(rider, bike)
        rows = storage.get_coast_segments(7)
        assert rows and rows[0]["label"] == "pedal", rows
    finally:
        conn = getattr(storage, "_conn", None)
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
        storage._conn = None
        config.DB_PATH = orig_db
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"manual tag OK: pedal tag excluded a descent "
          f"(n_segments {base['n_segments']} -> {tagged['n_segments']}) "
          f"and survived recalc")


def test_learned_classifier():
    """The classifier learns from manual tags, stays cautious, and cold
    start (no tags) is byte-for-byte the hand-tuned prior."""
    from cycling import coast as coast_mod

    def descent(hr, n=40):
        return [{"t": float(i), "lat": 52.0, "lon": -1.5, "elev": 100.0,
                 "grade": -0.05, "speed": 8.0, "dist": float(i) * 8.0, "hr": hr}
                for i in range(n)]

    # Cold start: no examples => the prior, unchanged.
    assert coast_mod.fit_classifier([]) == coast_mod.default_weights()

    # Coast tags move a pedal-classified descent out of the pedal bin.
    prior = coast_mod.classify_descents(descent(148.0), max_hr=180)[0]
    assert prior["label"] == "pedal", prior
    feats = coast_mod.segment_features({"records": descent(148.0)}, 180)
    weights = coast_mod.fit_classifier([(feats, 1.0)] * 8)
    after = coast_mod.classify_descents(descent(148.0), max_hr=180, weights=weights)[0]
    assert after["label"] != "pedal" and after["score"] > prior["score"], (prior, after)

    # An all-pedal history still asks on a coast-like (novel) descent.
    pedal_feats = coast_mod.segment_features({"records": descent(160.0)}, 180)
    w2 = coast_mod.fit_classifier([(pedal_feats, 0.0)] * 6)
    train = [pedal_feats] * 6
    coastlike = coast_mod.classify_descents(
        descent(90.0), max_hr=180, weights=w2, training_features=train)[0]
    pedal = coast_mod.classify_descents(
        descent(160.0), max_hr=180, weights=w2, training_features=train)[0]
    assert coastlike["label"] == "ask" and coastlike["reason"] == "novel", coastlike
    assert pedal["label"] == "pedal", pedal
    print(f"learned classifier OK: coast tags lifted a pedal descent "
          f"({prior['score']:.2f} -> {after['score']:.2f}); "
          f"all-pedal history kept a coast-like descent as {coastlike['label']}")


def test_pedal_tag_recovers_power():
    """Phase 3: a manual 'pedal' tag keeps a descent in the power estimate
    (mode='pedal', real watts) instead of zeroing it; untagged and
    'coast'-tagged descents still read 0 W, and the recovered descent stays
    low-confidence (user-asserted, not measured)."""
    rider = {"weight_kg": 75.0, "max_hr": 180}
    bike = {"mass_kg": 9.0, "cdA": 0.35, "crr": 0.005,
            "drivetrain_efficiency": 0.97}
    weather = {"temp_c": 15.0, "pressure_hpa": 1013.0, "wind_speed_mps": 0.0}
    records = []
    for i in range(60):
        records.append({"t": float(i), "lat": 52.0, "lon": -1.5,
                        "elev": 100.0 - 0.24 * i, "grade": -0.02,
                        "speed": 12.0, "dist": i * 12.0, "hr": 150.0})

    untagged = power_mod.compute_power([dict(r) for r in records],
                                       rider, bike, weather)
    assert all(r["mode"] == "coast" and r["watts_est"] == 0.0
               for r in untagged)

    tagged = power_mod.compute_power([dict(r, coast_label="pedal")
                                      for r in records], rider, bike, weather)
    assert all(r["mode"] == "pedal" for r in tagged)
    assert any(r["watts_est"] > 0 for r in tagged)
    assert all(r["confidence"] == "low" for r in tagged)

    coast_tag = power_mod.compute_power([dict(r, coast_label="coast")
                                         for r in records], rider, bike, weather)
    assert all(r["watts_est"] == 0.0 for r in coast_tag)
    print(f"pedal tag power OK: untagged 0 W, tagged "
          f"{max(r['watts_est'] for r in tagged):.0f} W (low confidence)")


def test_pooled_calibration_authoritative():
    """Phase 2: a pooled fit applies to the bike like a loop fit, per-ride
    winds are stored so each ride re-applies its own, and a later climb
    diagnostic never shadows the pooled result."""
    import pathlib
    import shutil
    import tempfile

    from cycling import config, pipeline, storage

    tmp = pathlib.Path(tempfile.mkdtemp(prefix="cycling_pooled_"))
    orig_db = config.DB_PATH
    try:
        config.DB_PATH = tmp / "cycling.db"
        storage._conn = None
        storage.init_db()
        storage.save_profile(
            {"age": 40, "weight_kg": 75.0, "height_cm": 178.0,
             "bike_type": "road", "sex": None, "resting_hr": 55,
             "max_hr": 180, "hr_zones": None},
            {"id": 1, "name": "Road bike", "mass_kg": 9.0, "crr": 0.005,
             "cdA": 0.35, "drivetrain_efficiency": 0.97,
             "calibrated": False},
        )
        pooled = {"type": "pooled", "crr": 0.0046, "cdA": 0.33, "r2": 0.9,
                  "n_segments": 6, "n_points": 900, "n_rides": 2,
                  "wind_recovered": True, "crr_sigma": 0.0004,
                  "cdA_sigma": 0.009,
                  "per_ride_wind": {1: {"wind_mps": 3.0, "wind_dir_deg": 45.0},
                                    2: {"wind_mps": 5.0, "wind_dir_deg": 180.0}}}
        pipeline._save_pooled_calibration(pooled)
        # A later climb diagnostic on ride 1 must not shadow its pooled row.
        storage.save_calibration(1, {"type": "climb", "crr": 0.0105,
                                     "cdA": 0.35, "r2": None,
                                     "n_segments": 2, "n_points": 100,
                                     "crr_sigma": 0.0002, "diagnostic": True})

        auth = storage.get_ride_calibration(1)
        assert auth is not None and auth["type"] == "pooled", auth
        assert auth.get("wind_recovered") is True
        assert auth.get("wind_mps") == 3.0  # ride 1's own wind, not a shared one
        auth2 = storage.get_ride_calibration(2)
        assert auth2 is not None and auth2.get("wind_mps") == 5.0, auth2

        _, bike = storage.get_profile()
        assert bike["calibrated"] == 1
        assert abs(bike["crr"] - 0.0046) < 1e-9  # pooled applied
        assert abs(bike["cdA"] - 0.33) < 1e-9
        print(f"pooled calibration OK: applied crr={bike['crr']:.4f} "
              f"cdA={bike['cdA']:.2f}; per-ride winds "
              f"{auth['wind_mps']} / {auth2['wind_mps']} m/s")
    finally:
        conn = getattr(storage, "_conn", None)
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
        storage._conn = None
        config.DB_PATH = orig_db
        shutil.rmtree(tmp, ignore_errors=True)


def test_weather_wind_unit_kmh_to_ms():
    """Open-Meteo's default wind unit is km/h; fetch_weather must return
    m/s. Regression: the archive's 20.7 km/h breeze was once stored as
    20.7 m/s, inflating aero power ~4x into headwinds."""
    import datetime
    from unittest import mock
    from cycling import weather as weather_mod

    when_unix = datetime.datetime(2026, 8, 19, 16, 33, 0,
                                  tzinfo=datetime.timezone.utc).timestamp()

    class _FakeResp:
        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            pass

        def json(self):
            return self._payload

    def _fake_get(url, params=None, timeout=None):
        params = dict(params or {})
        if params.get("hourly") == "temperature_2m":
            return _FakeResp({"timezone": "Europe/London",
                              "hourly": {"time": ["2020-01-01T00:00"],
                                         "temperature_2m": [0.0]}})
        return _FakeResp({
            "timezone": "Europe/London",
            # Deliberately Open-Meteo's DEFAULT unit: km/h.
            "hourly_units": {"wind_speed_10m": "km/h",
                             "wind_gusts_10m": "km/h"},
            "hourly": {
                "time": [f"2026-08-19T{i:02d}:00" for i in range(24)],
                "temperature_2m": [15.0] * 24,
                "wind_speed_10m": [36.0] * 24,   # 36 km/h = 10 m/s
                "wind_direction_10m": [180.0] * 24,
                "pressure_msl": [1013.0] * 24,
                "wind_gusts_10m": [54.0] * 24,   # 54 km/h = 15 m/s
            },
        })

    weather_mod._TZ_CACHE.clear()
    with mock.patch.object(weather_mod.requests, "get", side_effect=_fake_get), \
            mock.patch.object(weather_mod, "_cache", return_value={}), \
            mock.patch.object(weather_mod, "_save_cache"):
        result = weather_mod.fetch_weather(51.233, -1.134, when_unix)

    assert result["source"] == "open-meteo"
    assert abs(result["wind_speed_mps"] - 10.0) < 1e-9, result["wind_speed_mps"]
    assert abs(result["wind_speed_hourly"][0] - 10.0) < 1e-9
    # Gust margin: (15 - 10) m/s * 0.4 = 2.0 -> sigma at least that, capped <= 4.
    assert 2.0 - 1e-9 <= result["wind_sigma_mps"] <= 4.0, result["wind_sigma_mps"]
    print(f"weather wind units OK: 36 km/h fetched as "
          f"{result['wind_speed_mps']:.1f} m/s, sigma {result['wind_sigma_mps']:.2f}")


def test_tiff_lzw_roundtrip():
    """A spec-compliant LZW TIFF (as libtiff/PIL writes, with the TIFF
    'early change' code-width convention) must decode to its source pixels.

    Regression: the decoder once increased its LZW code width at GIF timing
    (512/1024/2048) instead of TIFF timing (511/1023/2047), so any real LZW
    raster decoded to garbage (or crashed) after the first width change.
    """
    import io

    import numpy as np
    from PIL import Image

    from cycling import tiffread

    rng = np.random.RandomState(0)
    for shape in ((40, 60), (64, 64), (33, 47)):
        h, w = shape
        src = (np.cumsum(np.cumsum(rng.rand(h, w), axis=0), axis=1) * 3.0
               + 12.5).astype(np.float32)
        buf = io.BytesIO()
        Image.fromarray(src, mode="F").save(buf, format="TIFF",
                                            compression="tiff_lzw")
        arr3, tw, th = tiffread.read_tiff(buf.getvalue())
        assert (tw, th) == (w, h), (tw, th, w, h)
        got = arr3[0].astype(np.float32)
        assert got.shape == (h, w), (got.shape, (h, w))
        err = float(np.max(np.abs(got - src)))
        assert err < 1e-2, f"LZW decode error {err} m at {shape}"
    print("TIFF LZW round-trip OK: libtiff-written LZW decodes exactly")


def test_wind_hour_wraps_midnight():
    """A ride crossing midnight must wrap onto the next day's early hours of
    the diurnal wind series, not clamp at 23:59. Regression: hours were
    np.clip'ed to 23.999, so after midnight every point used the last hour
    of the archive day instead of wrapping to hour 0."""
    from cycling import power as power_mod

    weather = {
        "wind_speed_mps": 2.0, "wind_dir_deg": 180.0,
        "ride_hour": 23,
        "wind_speed_hourly": [10.0] * 23 + [2.0],   # hour 23 calm, 00:xx windy
        "wind_dir_hourly": [180.0] * 24,
    }
    t0 = 1_700_000_000.0
    records = [{"t": t0 + i * 3600.0, "lat": 52.0, "lon": -1.5,
                "elev": 100.0, "grade": 0.0, "speed": 5.0, "dist": i * 5.0}
               for i in range(3)]  # 23:00, 00:00, 01:00

    ws_pts, _wd = power_mod._per_point_wind(records, weather, len(records))
    assert abs(ws_pts[0] - 2.0) < 1e-6, ws_pts          # ride starts in hour 23
    assert abs(ws_pts[1] - 10.0) < 1e-6, (              # midnight wraps to hour 0
        "post-midnight points clamped at 23:59 instead of wrapping", ws_pts)
    assert abs(ws_pts[2] - 10.0) < 1e-6, ws_pts
    print("midnight wind wrap OK: hourly series wraps instead of clamping")


def test_fit_timestamp_escape_resyncs():
    """A compressed-timestamp header whose 5-bit offset is 0x1F must treat
    the next byte as the LOW BYTE OF A NEW BASE TIMESTAMP (a resync), not as
    an additive offset. The payload still omits the 4-byte timestamp field,
    and later compressed messages offset from the new base."""
    import struct
    import tempfile
    from pathlib import Path

    from cycling import config, fit_parser

    defn = (bytes([0x40, 0x00, 0x00]) + struct.pack("<H", 20) + bytes([3])
            + bytes([0, 4, 0x05, 1, 4, 0x05, 253, 4, 0x0C]))
    lat, lon = 520000000, -15000000
    data_full = bytes([0x00]) + struct.pack("<iiI", lat, lon, 1000)
    data_comp_5 = bytes([0x80 | 0x05]) + struct.pack("<ii", lat, lon)
    data_escape = bytes([0x80 | 0x1F]) + bytes([56]) + struct.pack("<ii", lat, lon)
    data_trail = bytes([0x80 | 0x03]) + struct.pack("<ii", lat, lon)
    section = defn + data_full + data_comp_5 + data_escape + data_trail
    header = (bytes([14, 0x10]) + struct.pack("<H", 0x0010)
              + struct.pack("<I", len(section)) + b".FIT" + struct.pack("<H", 0))

    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "escape.fit"
        p.write_bytes(header + section)
        records, _ = fit_parser.parse_fit(p)

    assert len(records) == 4, f"message stream desynced: {len(records)}"
    fit_t = [r["t"] - config.FIT_EPOCH_UNIX for r in records]
    assert fit_t == [1000, 1005, 1080, 1083], (
        "escape byte must resync the base to (base & ~0xFF) + byte with "
        f"rollover, got {fit_t}")
    print(f"FIT timestamp escape OK: base resynced 1005 -> 1080 via one byte")


def test_server_hardening_caps():
    """The job dict must stay bounded (finished jobs dropped first), and the
    import-dir pruner must remove only stale uploads."""
    import pathlib
    import shutil
    import tempfile
    from unittest import mock

    from cycling import server

    # Job cap: fill with finished jobs, add one running job, overflow.
    original_jobs = dict(server._jobs)
    try:
        server._jobs.clear()
        for i in range(250):
            server._job(f"j{i:03d}", status="done", progress=100)
        server._job("keep-running", status="running", progress=10)
        server._job("fresh", status="queued", progress=0)
        assert len(server._jobs) <= 201, len(server._jobs)
        assert "keep-running" in server._jobs and "fresh" in server._jobs
        assert "j000" not in server._jobs  # oldest finished dropped first
    finally:
        server._jobs.clear()
        server._jobs.update(original_jobs)

    # Import-dir pruning: only files older than the cutoff disappear.
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="cycling_prune_"))
    orig_dir = server.config.IMPORT_DIR
    try:
        server.config.IMPORT_DIR = tmp
        old = tmp / "old.fit"
        new = tmp / "new.fit"
        old.write_bytes(b"x")
        new.write_bytes(b"y")
        two_days_ago = __import__("time").time() - 49 * 3600
        import os as _os
        _os.utime(old, (two_days_ago, two_days_ago))
        server._prune_import_dir()
        assert not old.exists() and new.exists()
    finally:
        server.config.IMPORT_DIR = orig_dir
        shutil.rmtree(tmp, ignore_errors=True)
    print("server hardening OK: job dict bounded (running kept), stale uploads pruned")


if __name__ == "__main__":
    test_compressed_timestamp_fit()
    test_route_detection()
    test_loop_calibration()
    test_cardiac_drift()
    test_calories()
    test_pause_moving_time_and_coverage()
    test_elevation_gain_shallow_climb()
    test_trimp_sex()
    test_wind_direction_wrap()
    test_non_uk_weather_date()
    test_weather_wind_unit_kmh_to_ms()
    test_tiff_lzw_roundtrip()
    test_tag_save_refreshes_stored_power()
    test_wind_hour_wraps_midnight()
    test_fit_timestamp_escape_resyncs()
    test_server_hardening_caps()
    test_both_segments_calibration_recorded()
    test_coast_classification_and_storage()
    test_manual_tag_changes_calibration_coasts()
    test_learned_classifier()
    test_pedal_tag_recovers_power()
    test_pooled_calibration_authoritative()
    print("\nAll backend checks passed.")
