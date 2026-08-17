"""Physics-based power estimation with honest uncertainty bands.

``P = (rolling + aero + gravity + acceleration) * speed``

There is no power meter on the bike, so every watt is an estimate. The plan's
honest ceiling: on flat ground aero drag dominates and wind cannot be
separated from CdA using speed + grade alone, so uncalibrated flat-ride
estimates carry wide bands; on steep climbs gravity dominates and the band
tightens. Downhill sections are removed from the estimate entirely: on a
descent speed + grade cannot separate pedalling, coasting and braking, so
every point below ``config.DOWNHILL_GRADE`` is reported as ~0 W with a
"coast" mode rather than a fake number, and excluded from power aggregates.
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


def _smoothed_speed(speeds):
    """Zero-phase speed smoothing before differentiating into acceleration.

    A 5-point mean is far too weak: 1% GPS speed jitter becomes ~40 W of
    acceleration noise at 9 m/s. Savitzky-Golay (11 pts, polyorder 2) is a
    symmetric low-pass with no phase lag, cutting that noise ~3x while
    preserving genuine surges. Short rides fall back to the plain mean."""
    speeds = np.asarray(speeds, dtype=float)
    n = len(speeds)
    if n < 11:
        out = speeds.copy()
        for i in range(n):
            lo, hi = max(0, i - 2), min(n, i + 3)
            out[i] = np.mean(speeds[lo:hi])
        return out
    from scipy.signal import savgol_filter
    return savgol_filter(speeds, window_length=11, polyorder=2)


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

    rho = elevation_air_density(weather, [r.get("elev") or 0.0 for r in records])

    n = len(records)
    speeds = np.array([r.get("speed") or 0.0 for r in records], dtype=float)
    grades = np.array([r.get("grade") or 0.0 for r in records], dtype=float)
    lats = np.array([r["lat"] for r in records], dtype=float)
    lons = np.array([r["lon"] for r in records], dtype=float)
    ts = np.array([r["t"] for r in records], dtype=float)

    v = _smoothed_speed(speeds)
    brg = _bearing(lats, lons)
    ws_pts, wd_pts = _per_point_wind(records, weather, n)
    phi = np.radians(wd_pts) - brg
    hw = ws_pts * np.cos(phi)
    cw = ws_pts * np.sin(phi)
    v_along = v + hw          # along-track component of relative airspeed
    v_air = np.hypot(v_along, cw)  # relative airspeed magnitude (crosswind counts)

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
    f_aero = 0.5 * rho * cdA * v_air * v_along
    f_grav = mass * G * np.sin(theta)
    f_accel = mass * 1.05 * accel

    p_wheel = (f_roll + f_aero + f_grav + f_accel) * v
    p_leg = np.where(p_wheel > 0, p_wheel / eff, p_wheel)

    # Downhill exclusion: no power estimate below config.DOWNHILL_GRADE.
    # On a descent the gravity term is negative and aero drag is unknowable
    # (wind), so the estimate cannot separate pedalling from coasting or
    # braking — a noisy tens-of-watts residual is not effort. Every downhill
    # point is reported as ~0 W with a "coast" mode rather than a fake
    # number; metrics.py keeps these points out of all power aggregates.
    coast = grades < config.DOWNHILL_GRADE

    # --- Uncertainty propagation -------------------------------------------------
    calib = bike.get("calibration") if isinstance(bike.get("calibration"), dict) else None
    if calibrated and calib and calib.get("crr_sigma") and calib.get("cdA_sigma"):
        # The calibration fit reports real parameter covariance; use it,
        # floored so a single clean ride cannot claim impossible precision.
        sigma_cdA = max(float(calib["cdA_sigma"]), 0.012)
        sigma_crr = max(float(calib["crr_sigma"]), 0.0003)
    else:
        sigma_cdA = (unc["cdA_rel_sigma_cal"] if calibrated else unc["cdA_rel_sigma"]) * cdA
        sigma_crr = (unc["crr_rel_sigma_cal"] if calibrated else unc["crr_rel_sigma"]) * crr
    # Per-ride wind sigma from the weather (hourly spread + gusts); fall
    # back to the flat default when weather has no wind detail.
    sigma_wind = float(weather.get("wind_sigma_mps") or unc["wind_sigma_mps"])
    sigma_grade = unc["grade_sigma"]
    sigma_mass = unc["mass_sigma_kg"]
    sigma_rho = unc["rho_rel_sigma"] * float(np.mean(rho))

    dp_cdA = 0.5 * rho * np.abs(v_air * v_along) * v
    dp_rho = 0.5 * float(bike.get("cdA", 0.35)) * np.abs(v_air * v_along) * v
    # Analytic derivative of the aero force w.r.t. wind speed (direction
    # fixed): d/dw[|v_air|*v_along] with hw=w*cos(phi), cw=w*sin(phi).
    cos_phi, sin_phi = np.cos(phi), np.sin(phi)
    d_aero_dw = 0.5 * rho * cdA * (
        v_along * (v_along * cos_phi + cw * sin_phi) / np.maximum(v_air, 1e-9)
        + v_air * cos_phi
    )
    dp_wind = np.abs(d_aero_dw) * v
    dp_crr = mass * G * np.cos(theta) * v
    dp_grade = mass * G * v
    dp_mass = G * (crr * np.cos(theta) + np.sin(theta)) * v

    sigma2 = (
        (dp_cdA * sigma_cdA) ** 2
        + (dp_wind * sigma_wind) ** 2
        + (dp_crr * sigma_crr) ** 2
        + (dp_grade * sigma_grade) ** 2
        + (dp_mass * sigma_mass) ** 2
        + (dp_rho * sigma_rho) ** 2
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


def _per_point_wind(records, weather, n):
    """Wind speed/direction per point, interpolating the hourly weather
    series across the ride when available.

    A long ride spans hours; holding the start-hour wind biases the aero
    term at the end. Returns (speed, direction) arrays, wrapping the
    direction on the circle."""
    ws = float(weather.get("wind_speed_mps", 0.0))
    wd = float(weather.get("wind_dir_deg", 0.0))
    hourly_speed = weather.get("wind_speed_hourly")
    hourly_dir = weather.get("wind_dir_hourly")
    ride_hour = weather.get("ride_hour")
    if not (isinstance(hourly_speed, (list, tuple)) and len(hourly_speed) >= 2
            and isinstance(hourly_dir, (list, tuple)) and len(hourly_dir) >= 2
            and ride_hour is not None and n > 1):
        return np.full(n, ws), np.full(n, wd)
    spd = np.asarray(hourly_speed, dtype=float)
    drc = np.asarray(hourly_dir, dtype=float)
    # The weather filter drops None entries independently, so the arrays can
    # differ in length; interpolate only over the common prefix.
    k = min(len(spd), len(drc))
    if k < 2:
        return np.full(n, ws), np.full(n, wd)
    spd = spd[:k]
    drc = drc[:k]
    t0 = records[0]["t"]
    hours = np.array([ride_hour + (r["t"] - t0) / 3600.0 for r in records], dtype=float)
    hours = np.clip(hours, 0.0, 23.999)
    ws_pts = np.interp(hours, np.arange(len(spd)), spd)
    # Circular interpolation of direction (shortest arc).
    d0 = np.radians(drc)
    x = np.cos(d0)
    y = np.sin(d0)
    frac = hours - np.floor(hours)
    idx = np.floor(hours).astype(int)
    idx = np.clip(idx, 0, len(spd) - 2)
    x_i = x[idx] * (1 - frac) + x[idx + 1] * frac
    y_i = y[idx] * (1 - frac) + y[idx + 1] * frac
    wd_pts = np.degrees(np.arctan2(y_i, x_i)) % 360.0
    return ws_pts, wd_pts


def weather_air_density(weather):
    temp_c = float(weather.get("temp_c", 15.0))
    pressure_pa = float(weather.get("pressure_hpa", 1013.0)) * 100.0
    return pressure_pa / (287.05 * (temp_c + 273.15))


def elevation_air_density(weather, elevs):
    """Per-point air density (kg/m^3) from the barometric formula.

    Density falls ~1.2% per 100 m. Using one surface value for a ride with
    400 m of climbing biases the aero term ~5% on the high parts, which is
    exactly where descents make aero dominant. Surface temperature/pressure
    come from weather; lapse rate 0.0065 K/m (ISA)."""
    elevs = np.asarray(elevs, dtype=float)
    t0 = float(weather.get("temp_c", 15.0)) + 273.15
    p0 = float(weather.get("pressure_hpa", 1013.0)) * 100.0
    z = np.maximum(elevs, 0.0)
    tz = t0 - 0.0065 * z
    pz = p0 * (1.0 - 0.0065 * z / t0) ** 5.2559
    return pz / (287.05 * tz)


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


def _irls_solve(A, b, iters=6):
    """Iteratively reweighted least squares (Huber-like).

    A braked descent makes a handful of coast points look like huge losses;
    plain least squares lets them drag the (crr, CdA) fit. IRLS downweights
    large residuals so the fit is driven by the genuine coasting points."""
    x, *_ = np.linalg.lstsq(A, b, rcond=None)
    for _ in range(iters):
        r = A @ x - b
        s = 1.4826 * np.median(np.abs(r))
        s = max(s, 1e-9)
        w = 1.0 / np.sqrt(np.maximum(1.0, (r / s) ** 2))
        xw, *_ = np.linalg.lstsq(A * w[:, None], b * w, rcond=None)
        x = xw
    return x


def _fit_loop_wind(rows, mass, rho):
    """Joint (crr, CdA, wind speed, wind direction) fit over coast points.

    A true coast has zero leg power, so the per-point wheel power
    ``(roll + aero + gravity + accel) * v`` must be ~0 at *every* point.
    That is hundreds of equations in (crr, CdA, wind speed, direction) —
    well-identified on a loop, where each descent faces a different wind
    angle. The outer optimizer sweeps (wind speed, direction); the inner
    least squares solves (crr, CdA). Returns (crr, cdA, wind_mps,
    wind_dir_deg, r2) or None when not identifiable."""
    if len(rows) < 3:
        return None
    bearings = []
    for seg, _, _ in rows:
        lats = np.array([p["lat"] for p in seg], dtype=float)
        lons = np.array([p["lon"] for p in seg], dtype=float)
        bearings.append(float(np.median(_bearing(lats, lons))))
    # Circular bearing spread: a linear max-min misjudges headings that
    # straddle north (359° and 1° are nearly parallel). Wind is identifiable
    # only when the headings cover the circle without a gap over 180°.
    # (_bearing returns radians, so wrap with 2*pi.)
    brgs = sorted(bearings)
    gaps = [brgs[(i + 1) % len(brgs)] - brgs[i] for i in range(len(brgs))]
    gaps[-1] += 2 * math.pi
    if max(gaps) > math.pi:
        return None  # single-direction descent: wind is not identifiable

    # Assemble the per-point design data across all coast segments.
    v_all, th_all, brg_all, b_all = [], [], [], []
    for seg, _, _ in rows:
        speeds = np.array([p.get("speed") or 0.0 for p in seg], dtype=float)
        vs = _smoothed_speed(speeds)
        lats = np.array([p["lat"] for p in seg], dtype=float)
        lons = np.array([p["lon"] for p in seg], dtype=float)
        brg = _bearing(lats, lons)
        ts = np.array([p["t"] for p in seg], dtype=float)
        acc = np.zeros(len(seg))
        dt = np.diff(ts)
        dv = np.diff(vs)
        with np.errstate(divide="ignore", invalid="ignore"):
            a = np.where(dt > 0, dv / np.maximum(dt, 1e-6), 0.0)
        acc[:-1] = np.clip(a, -3.0, 3.0)
        for i in range(len(seg)):
            grade = seg[i].get("grade") or 0.0
            theta = math.atan(max(-1.0, min(1.0, grade)))
            v_all.append(vs[i])
            th_all.append(theta)
            brg_all.append(brg[i])
            # Gravity + inertia must be exactly balanced by roll + aero.
            b_all.append(-(mass * G * math.sin(theta) + mass * 1.05 * acc[i]) * vs[i])
    v_all = np.array(v_all)
    th_all = np.array(th_all)
    brg_all = np.array(brg_all)
    b_all = np.array(b_all)

    def cost(params):
        w, phi = float(params[0]), float(params[1]) % 360.0
        ph = math.radians(phi) - brg_all
        hw = w * np.cos(ph)
        cw = w * np.sin(ph)
        v_along = v_all + hw
        v_air = np.hypot(v_along, cw)
        A = np.column_stack([
            mass * G * np.cos(th_all) * v_all,
            0.5 * rho * v_air * v_along * v_all,
        ])
        try:
            crr, cdA = _irls_solve(A, b_all)
        except np.linalg.LinAlgError:
            return 1e18
        resid = A @ np.array([crr, cdA]) - b_all
        # Soft physical-box penalty guides the outer sweep away from
        # degenerate (huge CdA, huge wind) combinations.
        pen = 0.0
        for val, lo, hi, wgt in ((crr, 0.001, 0.012, 1e6),
                                 (cdA, 0.15, 0.6, 1e4)):
            if val < lo:
                pen += wgt * (lo - val) ** 2
            elif val > hi:
                pen += wgt * (val - hi) ** 2
        return float(resid @ resid) + pen

    from scipy.optimize import minimize
    best = None
    for w0, p0 in ((0.0, 0.0), (3.0, 90.0), (5.0, 45.0), (5.0, 180.0),
                   (7.0, 270.0), (10.0, 135.0), (12.0, 315.0)):
        res = minimize(cost, (w0, p0), method="Nelder-Mead",
                       options={"maxiter": 600, "xatol": 1e-4, "fatol": 1e-10})
        if best is None or res.fun < best.fun:
            best = res
    if best is None or not np.isfinite(best.fun) or best.fun > 1e12:
        return None
    w = float(best.x[0])
    phi = float(best.x[1]) % 360.0
    if not (0.0 <= w <= 20.0):
        return None
    ph = math.radians(phi) - brg_all
    hw = w * np.cos(ph)
    cw = w * np.sin(ph)
    v_along = v_all + hw
    v_air = np.hypot(v_along, cw)
    A = np.column_stack([
        mass * G * np.cos(th_all) * v_all,
        0.5 * rho * v_air * v_along * v_all,
    ])
    try:
        crr, cdA = _irls_solve(A, b_all)
    except np.linalg.LinAlgError:
        return None
    pred = A @ np.array([crr, cdA])
    ss_res = float(np.sum((b_all - pred) ** 2))
    ss_tot = float(np.sum((b_all - b_all.mean()) ** 2)) or 1e-9
    r2 = 1.0 - ss_res / ss_tot
    # Parameter covariance from the weighted LS; a jackknife over the
    # coast segments captures how much the segments disagree.
    sigma2 = ss_res / max(1, len(b_all) - 2)
    cov = sigma2 * np.linalg.pinv(A.T @ A)
    crr_sigma = float(np.sqrt(max(cov[0, 0], 0.0)))
    cdA_sigma = float(np.sqrt(max(cov[1, 1], 0.0)))
    return float(crr), float(cdA), w, phi, r2, crr_sigma, cdA_sigma


def calibrate_loop(records, rider, bike, weather):
    """Effective-CdA fit from coasting descents, recovering the wind too.

    The energy balance on a coasting descent is
    ``m·g·Δh = Crr·m·g·Δs + ½·ρ·CdA·∫|v_air|·v_along·v dt``; on a loop or
    out-and-back the segments face different directions, so the wind that
    was "baked in" becomes measurable: (crr, CdA, wind speed, wind
    direction) are fitted jointly. Falls back to the wind-free least
    squares when the wind is not identifiable (single-direction descent)
    or the fit is degenerate.
    """
    mass = float(rider.get("weight_kg", 75.0)) + float(bike.get("mass_kg", 9.0)) + KIT_MASS_KG
    rho = weather_air_density(weather)
    max_hr = rider.get("max_hr") or _default_max_hr(rider)
    segs = find_coast_segments(records, max_hr)
    if len(segs) < 2:
        return None

    rows = []
    for seg in segs:
        dh, ds, v3dt = _segment_integrals(seg, rho)
        if ds > 50 and v3dt > 0 and dh > 0:
            # Δh = Crr·Δs + (0.5·ρ·CdA / (m·g))·∫v³dt
            rows.append((seg, ds, dh))

    if len(rows) < 2:
        return None

    wind = _fit_loop_wind(rows, mass, rho)
    if wind is not None:
        crr, cdA, w_mps, w_dir, r2, crr_sigma, cdA_sigma = wind
        return {
            "type": "loop",
            "crr": crr,
            "cdA": cdA,
            "r2": r2,
            "n_segments": len(rows),
            "n_points": sum(len(s) for s in segs),
            "wind_mps": w_mps,
            "wind_dir_deg": w_dir,
            "wind_recovered": True,
            "crr_sigma": crr_sigma,
            "cdA_sigma": cdA_sigma,
        }

    # Wind-free fallback (plain least squares over the v^3 integral).
    x1 = np.array([ds for _, ds, _ in rows])
    x2 = np.array([_segment_integrals(seg, rho)[2] / (mass * G)
                   for seg, _, _ in rows])
    y = np.array([dh for _, _, dh in rows])
    X = np.column_stack([x1, x2])
    try:
        (crr, aero_coeff), *_ = np.linalg.lstsq(X, y, rcond=None)
    except np.linalg.LinAlgError:
        return None
    cdA = aero_coeff / (0.5 * rho)

    pred = X @ np.array([crr, aero_coeff])
    ss_res = float(np.sum((y - pred) ** 2))
    ss_tot = float(np.sum((y - y.mean()) ** 2)) or 1e-9
    r2 = 1.0 - ss_res / ss_tot
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
        "wind_recovered": False,
    }


def calibrate_climb(records, rider, bike, weather):
    """Rolling-resistance fit from steep climbs (aero is small there).

    The rider's own power input is the missing term: on a sustained climb
    ``W_rider = m·g·Δh + Crr·m·g·Δs + ½·ρ·CdA·∫v³dt``. W_rider comes from
    the model's estimated watts — trustworthy on steep climbs, where
    gravity dominates — so Crr is solved by difference. (The old formula
    omitted W_rider and always over-estimated Crr, so it could never pass
    acceptance.)"""
    mass = float(rider.get("weight_kg", 75.0)) + float(bike.get("mass_kg", 9.0)) + KIT_MASS_KG
    rho = weather_air_density(weather)
    cdA = float(bike.get("cdA", 0.35))
    segs = find_climb_segments(records, rider.get("max_hr") or _default_max_hr(rider))
    if len(segs) < 1:
        return None

    crr_est = []
    for seg in segs:
        dh, ds, v3dt = _segment_integrals(seg, rho)
        if ds > 50 and not any("watts_est" in p for p in seg):
            continue
        # Rider work from estimated watts (trapezoid over time).
        w_work = 0.0
        for i in range(len(seg) - 1):
            a, b = seg[i], seg[i + 1]
            dt = b["t"] - a["t"]
            w_work += 0.5 * ((a.get("watts_est") or 0.0) + (b.get("watts_est") or 0.0)) * dt
        climb = 0.0
        for i in range(len(seg) - 1):
            d = seg[i + 1].get("elev", 0.0) - seg[i].get("elev", 0.0)
            if d > 0:
                climb += d
        if ds > 50 and climb > 1.0 and w_work > 0:
            # W_rider = m g Δh + Crr m g Δs + 0.5 ρ CdA ∫v³dt  => solve Crr.
            aero = 0.5 * rho * cdA * v3dt
            crr = (w_work - mass * G * climb - aero) / (mass * G * ds)
            crr_est.append(crr)

    if not crr_est:
        return None
    crr = float(np.median(crr_est))
    # Robust spread: MAD of the per-segment estimates.
    crr_sigma = float(1.4826 * np.median(np.abs(np.array(crr_est) - crr)))
    return {
        "type": "climb",
        "crr": crr,
        "cdA": cdA,
        "r2": None,
        "n_segments": len(crr_est),
        "n_points": sum(len(s) for s in segs),
        "crr_sigma": crr_sigma,
    }


def _default_max_hr(rider):
    age = int(rider.get("age", 40))
    return 220 - age
