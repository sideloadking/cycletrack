"""Physics-based power estimation with honest uncertainty bands.

``P = (rolling + aero + gravity + acceleration) * speed``

There is no power meter on the bike, so every watt is an estimate. The plan's
honest ceiling: on flat ground aero drag dominates and wind cannot be
separated from CdA using speed + grade alone, so uncalibrated flat-ride
estimates carry wide bands; on steep climbs gravity dominates and the band
tightens. Coasting downhill is reported as ~0 W with a "coast" mode rather
than "zero effort".
"""

import math

import numpy as np

from . import config

G = 9.80665
KIT_MASS_KG = 2.0  # clothing, helmet, bottles, computer...


# ---------------------------------------------------------------------------
# Per-point power + uncertainty
# ---------------------------------------------------------------------------

def _bearing(lats, lons):
    lats = np.radians(np.asarray(lats, dtype=float))
    lons = np.radians(np.asarray(lons, dtype=float))
    n = len(lats)
    brg = np.zeros(n)
    for i in range(n - 1):
        dl = lons[i + 1] - lons[i]
        y = math.sin(dl) * math.cos(lats[i + 1])
        x = math.cos(lats[i]) * math.sin(lats[i + 1]) - math.sin(lats[i]) * math.cos(lats[i + 1]) * math.cos(dl)
        brg[i] = (math.atan2(y, x) + 2 * math.pi) % (2 * math.pi)
    brg[-1] = brg[-2] if n > 1 else 0.0
    return brg


def _headwind(speeds, lats, lons, wind_speed, wind_dir_deg):
    """Headwind component (positive = into the wind), m/s."""
    if wind_speed <= 0:
        return np.zeros(len(speeds))
    brg = _bearing(lats, lons)
    wind_rad = math.radians(wind_dir_deg)
    comp = np.cos(wind_rad - brg)
    return wind_speed * comp


def _smoothed_speed(speeds):
    speeds = np.asarray(speeds, dtype=float)
    n = len(speeds)
    if n < 5:
        return speeds.copy()
    out = speeds.copy()
    for i in range(n):
        lo, hi = max(0, i - 2), min(n, i + 3)
        out[i] = np.mean(speeds[lo:hi])
    return out


def compute_power(records, rider, bike, weather):
    """Add watts_lo/watts_est/watts_hi/confidence/mode to every record.

    Returns the same list with power fields attached.
    """
    unc = config.UNCERTAINTY
    mass = float(rider.get("weight_kg", 75.0)) + float(bike.get("mass_kg", 9.0)) + KIT_MASS_KG
    crr = float(bike.get("crr", 0.005))
    cdA = float(bike.get("cdA", 0.35))
    eff = float(bike.get("drivetrain_efficiency", 0.97))
    calibrated = bool(bike.get("calibrated", False))

    rho = weather_air_density(weather)
    wind_speed = float(weather.get("wind_speed_mps", 0.0))
    wind_dir = float(weather.get("wind_dir_deg", 0.0))

    n = len(records)
    speeds = np.array([r.get("speed") or 0.0 for r in records], dtype=float)
    grades = np.array([r.get("grade") or 0.0 for r in records], dtype=float)
    lats = np.array([r["lat"] for r in records], dtype=float)
    lons = np.array([r["lon"] for r in records], dtype=float)
    ts = np.array([r["t"] for r in records], dtype=float)

    v = _smoothed_speed(speeds)
    hw = _headwind(v, lats, lons, wind_speed, wind_dir)
    v_air = v + hw

    # Acceleration (m/s^2), clamped to physical-ish bounds.
    accel = np.zeros(n)
    dt = np.diff(ts)
    dv = np.diff(v)
    with np.errstate(divide="ignore", invalid="ignore"):
        a = np.where(dt > 0, dv / np.maximum(dt, 1e-6), 0.0)
    a = np.clip(a, -3.0, 3.0)
    accel[:-1] = a

    theta = np.arctan(np.clip(grades, -1.0, 1.0))
    f_roll = crr * mass * G * np.cos(theta)
    v_air_signed = v_air * np.abs(v_air)
    f_aero = 0.5 * rho * cdA * v_air_signed
    f_grav = mass * G * np.sin(theta)
    f_accel = mass * 1.05 * accel

    p_wheel = (f_roll + f_aero + f_grav + f_accel) * v
    p_leg = np.where(p_wheel > 0, p_wheel / eff, p_wheel)

    # Coasting detection: descending with effectively no pedalling required.
    coast = (grades < -0.01) & (p_leg < 25.0)

    # --- Uncertainty propagation -------------------------------------------------
    sigma_cdA = (unc["cdA_rel_sigma_cal"] if calibrated else unc["cdA_rel_sigma"]) * cdA
    sigma_crr = (unc["crr_rel_sigma_cal"] if calibrated else unc["crr_rel_sigma"]) * crr
    sigma_wind = unc["wind_sigma_mps"]
    sigma_grade = unc["grade_sigma"]
    sigma_mass = unc["mass_sigma_kg"]

    dp_cdA = 0.5 * rho * np.abs(v_air_signed) * v
    dp_wind = rho * cdA * np.abs(v_air) * v
    dp_crr = mass * G * np.cos(theta) * v
    dp_grade = mass * G * v
    dp_mass = G * (crr * np.cos(theta) + np.sin(theta)) * v

    sigma2 = (
        (dp_cdA * sigma_cdA) ** 2
        + (dp_wind * sigma_wind) ** 2
        + (dp_crr * sigma_crr) ** 2
        + (dp_grade * sigma_grade) ** 2
        + (dp_mass * sigma_mass) ** 2
    )
    sigma = np.sqrt(np.maximum(sigma2, 0.0))

    watts_est = np.maximum(p_leg, 0.0)
    watts_lo = np.maximum(watts_est - 2.0 * sigma, 0.0)
    watts_hi = watts_est + 2.0 * sigma

    # Gravity fraction drives the confidence tag.
    f_abs = np.abs(f_roll) + np.abs(f_aero) + np.abs(f_grav) + 1e-3
    fg = np.abs(f_grav) / f_abs
    conf = np.where(fg >= 0.5, 2, np.where(fg >= 0.2, 1, 0))  # 2 high, 1 med, 0 low

    conf_label = np.array(["low", "med", "high"])
    mode = np.where(coast, "coast", np.where(v < 0.3, "static", "pedal"))

    out = []
    for i, r in enumerate(records):
        rec = dict(r)
        if coast[i]:
            rec["watts_est"] = 0.0
            rec["watts_lo"] = 0.0
            rec["watts_hi"] = 0.0
            rec["confidence"] = "high"
        else:
            rec["watts_est"] = float(watts_est[i])
            rec["watts_lo"] = float(watts_lo[i])
            rec["watts_hi"] = float(watts_hi[i])
            rec["confidence"] = conf_label[conf[i]]
        rec["mode"] = str(mode[i])
        out.append(rec)
    return out


def weather_air_density(weather):
    temp_c = float(weather.get("temp_c", 15.0))
    pressure_pa = float(weather.get("pressure_hpa", 1013.0)) * 100.0
    return pressure_pa / (287.05 * (temp_c + 273.15))


# ---------------------------------------------------------------------------
# Calibration — energy-balance least-squares on coast/climb segments
# ---------------------------------------------------------------------------

def find_coast_segments(records, max_hr=None):
    """Contiguous descending, likely-coasting runs (speed + HR based)."""
    segs = []
    cur = []
    for r in records:
        grade = r.get("grade") or 0.0
        speed = r.get("speed") or 0.0
        hr = r.get("hr")
        coasting = (
            grade < -0.008
            and speed > 2.0
            and (max_hr is None or hr is None or hr < 0.78 * max_hr)
        )
        if coasting:
            cur.append(r)
        else:
            if len(cur) >= 10:
                segs.append(cur)
            cur = []
    if len(cur) >= 10:
        segs.append(cur)
    return segs


def find_climb_segments(records, max_hr=None):
    """Contiguous steep climbing runs at moderate speed."""
    segs = []
    cur = []
    for r in records:
        grade = r.get("grade") or 0.0
        speed = r.get("speed") or 0.0
        climbing = grade > 0.03 and 1.5 < speed < 9.0
        if climbing:
            cur.append(r)
        else:
            if len(cur) >= 20:
                segs.append(cur)
            cur = []
    if len(cur) >= 20:
        segs.append(cur)
    return segs


def _segment_integrals(seg, rho):
    dh = 0.0
    ds = 0.0
    v3dt = 0.0
    for i in range(len(seg) - 1):
        a, b = seg[i], seg[i + 1]
        d_elev = b.get("elev", 0.0) - a.get("elev", 0.0)
        if d_elev < 0:
            dh += -d_elev
        d_dist = (b.get("dist") or 0.0) - (a.get("dist") or 0.0)
        if d_dist > 0:
            ds += d_dist
        dt = b["t"] - a["t"]
        va = a.get("speed") or 0.0
        vb = b.get("speed") or 0.0
        v = (va + vb) / 2.0
        v3dt += (v ** 3) * dt
    return dh, ds, v3dt


def calibrate_loop(records, rider, bike, weather):
    """Effective-CdA fit from coasting descents (wind baked in, per plan)."""
    mass = float(rider.get("weight_kg", 75.0)) + float(bike.get("mass_kg", 9.0)) + KIT_MASS_KG
    rho = weather_air_density(weather)
    max_hr = rider.get("max_hr") or _default_max_hr(rider)
    segs = find_coast_segments(records, max_hr)
    if len(segs) < 2:
        return None

    rows = []
    for seg in segs:
        dh, ds, v3dt = _segment_integrals(seg, rho)
        if ds > 50 and v3dt > 0:
            # Δh = Crr·Δs + (0.5·ρ·CdA / (m·g))·∫v³dt
            rows.append((ds, v3dt / (mass * G), dh))

    if len(rows) < 2:
        return None

    x1 = np.array([r[0] for r in rows])
    x2 = np.array([r[1] for r in rows])
    y = np.array([r[2] for r in rows])
    X = np.column_stack([x1, x2])
    try:
        (crr, aero_coeff), *_ = np.linalg.lstsq(X, y, rcond=None)
    except np.linalg.LinAlgError:
        return None
    # The aero coefficient is 0.5·ρ·CdA (the 0.5·ρ lives in the physics, not
    # the regression column), so recover CdA explicitly.
    cdA = aero_coeff / (0.5 * rho)

    pred = X @ np.array([crr, aero_coeff])
    ss_res = float(np.sum((y - pred) ** 2))
    ss_tot = float(np.sum((y - y.mean()) ** 2)) or 1e-9
    r2 = 1.0 - ss_res / ss_tot
    # Return raw fitted values; acceptance bounds are checked by the caller
    # (braking on descents inflates the rolling term, so out-of-range fits
    # must be rejected rather than silently clipped).
    crr = float(crr)
    cdA = float(cdA)

    return {
        "type": "loop",
        "crr": crr,
        "cdA": cdA,
        "r2": r2,
        "n_segments": len(rows),
        "n_points": sum(len(s) for s in segs),
        "wind_mps": weather.get("wind_speed_mps"),
    }


def calibrate_climb(records, rider, bike, weather):
    """Rolling-resistance fit from steep climbs (aero is small there)."""
    mass = float(rider.get("weight_kg", 75.0)) + float(bike.get("mass_kg", 9.0)) + KIT_MASS_KG
    rho = weather_air_density(weather)
    cdA = float(bike.get("cdA", 0.35))
    segs = find_climb_segments(records, rider.get("max_hr") or _default_max_hr(rider))
    if len(segs) < 1:
        return None

    crr_est = []
    for seg in segs:
        dh, ds, v3dt = _segment_integrals(seg, rho)
        # On a climb we *gain* height; energy balance against gravity.
        climb = 0.0
        for i in range(len(seg) - 1):
            d = seg[i + 1].get("elev", 0.0) - seg[i].get("elev", 0.0)
            if d > 0:
                climb += d
        if ds > 50 and climb > 1.0:
            # climb = Crr·ds + (0.5 ρ CdA /(m g))·∫v³dt  => solve Crr.
            crr = (climb - 0.5 * rho * cdA * v3dt / (mass * G)) / ds
            crr_est.append(crr)

    if not crr_est:
        return None
    crr = float(np.median(crr_est))
    return {
        "type": "climb",
        "crr": crr,
        "cdA": cdA,
        "r2": None,
        "n_segments": len(crr_est),
        "n_points": sum(len(s) for s in segs),
    }


def _default_max_hr(rider):
    age = int(rider.get("age", 40))
    return 220 - age
