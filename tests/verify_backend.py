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


if __name__ == "__main__":
    test_compressed_timestamp_fit()
    test_route_detection()
    test_loop_calibration()
    test_cardiac_drift()
    print("\nAll backend checks passed.")
