"""Concrete and estimated per-ride metrics.

Concrete (no estimation): distance, duration, elevation gain, HR zones, TRIMP,
VAM, climb stats. Estimated (from the power model, with bands): average power,
normalised power, W/kg at fixed HRs, best-N-minute power, VO2max.
"""

import math

import numpy as np

from . import config

# ---------------------------------------------------------------------------
# HR zones
# ---------------------------------------------------------------------------


def default_max_hr(age):
    return max(120, 220 - int(age or 40))


def default_hr_zones(max_hr, resting_hr=None):
    """Five HR zones from % of max, or Karvonen heart-rate-reserve when a
    resting HR is known. HRR accounts for a low/high resting rate, so an
    aerobic (nasal-breathing) effort lands in Z1-Z2 instead of Z3-Z4."""
    edges = [0.50, 0.60, 0.70, 0.80, 0.90, 1.00]
    base = float(resting_hr) if resting_hr else 0.0
    span = max(float(max_hr) - base, 1.0)
    return [
        {"lo": round(base + edges[i] * span), "hi": round(base + edges[i + 1] * span)}
        for i in range(5)
    ]


def hr_zone_index(hr, zones):
    for i, z in enumerate(zones):
        if z["lo"] <= hr < z["hi"]:
            return i
    if hr >= zones[-1]["hi"]:
        return len(zones) - 1
    return None


# ---------------------------------------------------------------------------
# TRIMP (Banister) and HR stats
# ---------------------------------------------------------------------------

def _hr_metrics(records, rider):
    hrs = [r.get("hr") for r in records if r.get("hr") is not None]
    if not hrs:
        return {
            "avg_hr": None, "max_hr": None, "min_hr": None,
            "trimp": 0.0, "time_in_zone": [0.0] * 5,
        }

    max_hr = rider.get("max_hr") or default_max_hr(rider.get("age"))
    rest_hr = float(rider.get("resting_hr", 55) or 55)
    zones = rider.get("hr_zones") or default_hr_zones(max_hr, rest_hr)
    # Banister's TRIMP constants are sex-specific; default to the male
    # weighting only when the rider's sex is unknown.
    if str(rider.get("sex") or "").strip().lower() == "female":
        trimp_coef, trimp_exp = config.TRIMP_HR_COEF_FEMALE, config.TRIMP_HR_EXP_FEMALE
    else:
        trimp_coef, trimp_exp = config.TRIMP_HR_COEF, config.TRIMP_HR_EXP

    trimp = 0.0
    zone_seconds = [0.0] * 5
    prev_t = None

    for r in records:
        hr = r.get("hr")
        if hr is None:
            continue
        t = r["t"]
        if prev_t is not None:
            dt = max(0.0, min(t - prev_t, 10.0))
            hrr = max(0.0, min(1.0, (hr - rest_hr) / max(1, max_hr - rest_hr)))
            trimp += (dt / 60.0) * hrr * trimp_coef * math.exp(trimp_exp * hrr)
            zi = hr_zone_index(hr, zones)
            if zi is not None:
                zone_seconds[zi] += dt
        prev_t = t

    return {
        "avg_hr": float(np.mean(hrs)),
        "max_hr": float(np.max(hrs)),
        "min_hr": float(np.min(hrs)),
        "trimp": float(trimp),
        "time_in_zone": zone_seconds,
    }


# ---------------------------------------------------------------------------
# Power-derived metrics
# ---------------------------------------------------------------------------

def _power_metrics(records, rider):
    w = np.array([r.get("watts_est") or 0.0 for r in records], dtype=float)
    wl = np.array([r.get("watts_lo") or 0.0 for r in records], dtype=float)
    wh = np.array([r.get("watts_hi") or 0.0 for r in records], dtype=float)
    ts = np.array([r["t"] for r in records], dtype=float)
    hrs = np.array([r.get("hr") for r in records], dtype=float)
    conf = [r.get("confidence") for r in records]
    mode = [r.get("mode") for r in records]
    weight = float(rider.get("weight_kg", 75.0))

    # Power is only estimated off-downhill (compute_power zeroes every point
    # below config.DOWNHILL_GRADE and tags it "coast"), and those zeroes
    # must not pollute the aggregates. All power metrics here run over
    # *pedalling* points only; time is re-based onto cumulative pedalling
    # time so a coasting descent contributes no time to NP or the
    # best-N-minute windows either.
    pedalling = np.array([m == "pedal" for m in mode], dtype=bool)
    if pedalling.any():
        w_ped, wl_ped, wh_ped = w[pedalling], wl[pedalling], wh[pedalling]
        t_eff = _effort_time(ts, pedalling)
    else:
        w_ped = wl_ped = wh_ped = np.array([], dtype=float)
        t_eff = np.array([], dtype=float)

    avg_watts = float(np.mean(w_ped)) if len(w_ped) else 0.0
    avg_lo = float(np.mean(wl_ped)) if len(wl_ped) else 0.0
    avg_hi = float(np.mean(wh_ped)) if len(wh_ped) else 0.0

    # Normalised power: 4th root of the mean of a 30 s rolling mean of w^4.
    np_val = _normalised_power(w_ped, t_eff)
    np_lo = _normalised_power(wl_ped, t_eff)
    np_hi = _normalised_power(wh_ped, t_eff)

    power_curve = {}
    for minutes in config.POWER_CURVE_MINUTES:
        best = _best_power(w_ped, t_eff, minutes * 60)
        best_lo = _best_power(wl_ped, t_eff, minutes * 60)
        best_hi = _best_power(wh_ped, t_eff, minutes * 60)
        power_curve[str(minutes)] = {
            "watts": best, "lo": best_lo, "hi": best_hi,
        }

    w5 = power_curve.get("5", {}).get("watts", 0.0)
    vo2max = 10.8 * (w5 / max(weight, 1.0)) + 7.0

    watts_at_hr = _watts_at_fixed_hr(w, hrs, conf, mode)

    return {
        "avg_watts": avg_watts,
        "avg_watts_lo": avg_lo,
        "avg_watts_hi": avg_hi,
        "normalized_power": np_val,
        "normalized_power_lo": np_lo,
        "normalized_power_hi": np_hi,
        "power_curve": power_curve,
        "vo2max": float(vo2max),
        "watts_at_hr": watts_at_hr,
        "pedalling_ratio": float(pedalling.mean()) if len(pedalling) else 0.0,
    }


def _effort_time(ts, pedalling):
    """Cumulative *pedalling* time at each pedalling point.

    An interval between two consecutive records counts only when both ends
    are pedalling, so a coasting descent between them adds zero time. Best-
    N-minute windows and normalized power then measure sustained effort
    rather than wall-clock time polluted by descents.
    """
    dt = np.maximum(np.diff(ts), 0.0)
    interval = np.where(pedalling[:-1] & pedalling[1:], dt, 0.0)
    t_eff = np.concatenate(([0.0], np.cumsum(interval)))
    return t_eff[pedalling]


def _normalised_power(w, ts):
    n = len(w)
    if n < 2:
        return 0.0
    # Coggan normalised power: a 30 s rolling *time* mean of power, raised to
    # the 4th power, averaged over time, then the 4th root. Smoothing comes
    # BEFORE the 4th power (not after): NP should emphasise sustained surges,
    # not single-second spikes. The window is time-based so "30 s" stays
    # meaningful across FIT gaps and the pedalling-time rebase in _power_metrics.
    w = np.asarray(w, dtype=float)
    ts = np.asarray(ts, dtype=float)
    w_smooth = _rolling_mean_time(ts, w, 30.0)
    w4 = w_smooth ** 4
    if ts[-1] <= ts[0]:
        return float(np.mean(w4) ** 0.25)
    dt = np.maximum(np.diff(ts), 0.0)
    integral = float(np.sum(dt * 0.5 * (w4[:-1] + w4[1:])))
    span = float(ts[-1] - ts[0])
    return float((integral / span) ** 0.25)


def _best_power(w, ts, seconds):
    """Best sustained power over a rolling *time* window of ``seconds``.

    Index-based windows assume uniform 1 Hz sampling; real FIT data has
    gaps (stops, dropped records), so the window is integrated over time
    with the cumulative trapezoid integral instead."""
    w = np.asarray(w, dtype=float)
    ts = np.asarray(ts, dtype=float)
    n = len(w)
    if n == 0:
        return 0.0
    if seconds <= 0 or n == 1:
        return float(np.max(w))
    if ts[-1] <= ts[0]:
        return float(np.mean(w))
    if ts[-1] - ts[0] <= seconds:
        # Window longer than the ride: the best N-minute power is the
        # time-weighted ride mean (matches the old index-based behaviour
        # of averaging when the window covers everything).
        cum = np.concatenate(([0.0], np.cumsum(np.diff(ts) * 0.5 * (w[:-1] + w[1:]))))
        return float(cum[-1] / (ts[-1] - ts[0]))
    cum = np.concatenate(([0.0], np.cumsum(np.diff(ts) * 0.5 * (w[:-1] + w[1:]))))
    best = 0.0
    for i in range(n):
        t_hi = ts[i]
        t_lo = t_hi - seconds
        c_hi = cum[i]
        if t_lo <= ts[0]:
            c_lo = 0.0
            span = t_hi - ts[0]
        else:
            c_lo = float(np.interp(t_lo, ts, cum))
            span = t_hi - t_lo
        # Only full-length windows count: a partial window at the start of
        # the ride would average a few high seconds over a short span and
        # inflate the best-N-minute value.
        if span < seconds:
            continue
        avg = (c_hi - c_lo) / span
        if avg > best:
            best = avg
    return float(best)


def _watts_at_fixed_hr(watts, hrs, confidence, mode):
    """Linear watts-vs-HR fit over confident *pedalling* points; predict at
    fixed HRs. Coasting/static points (watts ~ 0 by definition) are excluded so
    they don't flatten the slope."""
    conf_ok = np.array([c in ("high", "med") for c in confidence], dtype=bool)
    pedal_ok = np.array([m == "pedal" for m in mode], dtype=bool)
    valid = conf_ok & pedal_ok & (hrs > 60) & (watts > 0) & np.isfinite(hrs)
    result = {}
    if valid.sum() < 20:
        for hr in config.WATTS_AT_HR:
            result[str(hr)] = {"watts": None, "lo": None, "hi": None, "n": 0, "r2": None}
        return result

    x = hrs[valid]
    y = watts[valid]
    A = np.column_stack([x, np.ones_like(x)])
    # Robust fit: a few mis-estimated points (gust noise) must not drag the
    # slope; IRLS downweights the worst residuals.
    from . import power as power_mod
    slope, intercept = power_mod._irls_solve(A, y)
    pred = A @ np.array([slope, intercept])
    ss_res = float(np.sum((y - pred) ** 2))
    ss_tot = float(np.sum((y - y.mean()) ** 2)) or 1e-9
    r2 = 1.0 - ss_res / ss_tot
    resid_std = float(np.std(y - pred))

    for hr in config.WATTS_AT_HR:
        est = slope * hr + intercept
        # Prediction band grows as we extrapolate away from the observed mean.
        spread = resid_std * (1.0 + abs(hr - float(x.mean())) / 20.0)
        result[str(hr)] = {
            "watts": round(float(est), 1),
            "lo": round(max(0.0, float(est) - 2.0 * spread), 1),
            "hi": round(float(est) + 2.0 * spread, 1),
            "n": int(valid.sum()),
            "r2": round(r2, 3),
        }
    return result


# ---------------------------------------------------------------------------
# Cardiac drift
# ---------------------------------------------------------------------------

def _rolling_mean_time(ts, values, window_s):
    """Trapezoid-weighted rolling mean over a fixed time window (robust to
    irregular sampling). Returns a smoothed copy of ``values``."""
    values = np.asarray(values, dtype=float)
    n = len(values)
    if n == 0:
        return values.copy()
    half = window_s / 2.0
    out = np.empty(n, dtype=float)
    # Cumulative integrals of value and 1 over time, padded by repeating the
    # final value so cum[i+1] - cum[j] is the integral over points j..i even
    # when the window ends at the last point.
    dt = np.diff(ts)
    v_mid = 0.5 * (values[:-1] + values[1:])
    cum_v = np.concatenate(([0.0], np.cumsum(dt * v_mid)))
    cum_t = np.concatenate(([0.0], np.cumsum(dt)))
    cum_v = np.concatenate((cum_v, [cum_v[-1]]))
    cum_t = np.concatenate((cum_t, [cum_t[-1]]))
    for i in range(n):
        lo = np.searchsorted(ts, ts[i] - half, side="left")
        hi = np.searchsorted(ts, ts[i] + half, side="right") - 1
        lo, hi = max(0, lo), min(n - 1, hi)
        span_t = cum_t[hi + 1] - cum_t[lo]
        if span_t <= 0:
            out[i] = values[i]
        else:
            out[i] = (cum_v[hi + 1] - cum_v[lo]) / span_t
    return out


def cardiac_drift(records, rider):
    """Measure the rise in HR during steady-effort windows (cardiac drift).

    Cardiac drift is the progressive upward creep of heart rate during a
    prolonged effort at constant workload. There is no power meter, so
    "constant workload" means steady *estimated* power: we find windows of at
    least DRIFT_MIN_MINUTES where a smoothed power signal stays within a tight
    coefficient of variation, then fit HR against time. A positive slope at
    steady estimated watts is the drift signal; the result is reported as
    bpm per hour and % of starting HR per hour.

    The power signal is smoothed over DRIFT_SMOOTH_S before checking
    steadiness — single-point wind noise in the estimate is not "effort
    change" — and brief gaps (stops at junctions) up to DRIFT_MERGE_GAP_S are
    bridged. Returns a dict describing the strongest qualifying window, or
    None when the ride has no HR data or no steady-effort windows (a flat,
    gusty ride often genuinely has none — we don't invent drift).
    """
    ts = np.array([r["t"] for r in records], dtype=float)
    hrs = np.array([r.get("hr") for r in records], dtype=float)
    watts = np.array([r.get("watts_est") or 0.0 for r in records], dtype=float)
    speed = np.array([r.get("speed") or 0.0 for r in records], dtype=float)
    mode = np.array([r.get("mode") for r in records])
    if not np.isfinite(hrs).any():
        return None

    max_hr = rider.get("max_hr") or default_max_hr(rider.get("age"))
    watts_sm = _rolling_mean_time(ts, watts, config.DRIFT_SMOOTH_S)

    # Steady-effort candidates: pedalling at a real workload, HR in the
    # aerobic band (below threshold-ish), and past the first few minutes so a
    # warm-up HR rise is not mistaken for drift.
    warmup = ts - ts[0] >= config.DRIFT_SKIP_START_S
    candidate = (
        warmup
        & (mode == "pedal")
        & (watts_sm >= config.DRIFT_MIN_WATTS)
        & np.isfinite(hrs)
        & (hrs >= config.DRIFT_HR_MIN)
        & (hrs <= 0.94 * max_hr)
    )

    runs = []
    start = None
    for i, ok in enumerate(candidate):
        if ok and start is None:
            start = i
        elif not ok and start is not None:
            runs.append((start, i - 1))
            start = None
    if start is not None:
        runs.append((start, len(candidate) - 1))

    # Bridge short gaps (a junction stop is not the end of the effort).
    windows = []
    if runs:
        a, b = runs[0]
        for na, nb in runs[1:]:
            if ts[na] - ts[b] <= config.DRIFT_MERGE_GAP_S:
                b = nb
            else:
                windows.append((a, b))
                a, b = na, nb
        windows.append((a, b))

    best = None
    for a, b in windows:
        if ts[b] - ts[a] < config.DRIFT_MIN_MINUTES * 60:
            continue
        # A bridged window may contain junction-stop points (mode != pedal,
        # watts ~ 0, HR dip); the effort itself is only the candidate points,
        # so steadiness and the HR fit must use those and not the gap.
        sel = np.flatnonzero(candidate[a:b + 1]) + a
        if sel.size < 2:
            continue
        seg_w = watts_sm[sel]
        seg_s = speed[sel]
        seg_h = hrs[sel]
        cv_power = float(np.std(seg_w) / max(np.mean(seg_w), 1e-9))
        cv_speed = float(np.std(seg_s) / max(np.mean(seg_s), 1e-9))
        if cv_power > config.DRIFT_CV_POWER or cv_speed > config.DRIFT_CV_SPEED:
            continue
        # Linear fit HR ~ time over the steady points only.
        x = ts[sel]
        y = seg_h
        A = np.column_stack([x - x[0], np.ones_like(x)])
        (slope, intercept), *_ = np.linalg.lstsq(A, y, rcond=None)
        pred = A @ np.array([slope, intercept])
        ss_res = float(np.sum((y - pred) ** 2))
        ss_tot = float(np.sum((y - y.mean()) ** 2)) or 1e-9
        r2 = 1.0 - ss_res / ss_tot
        drift_bpm_hr = float(slope * 3600.0)
        start_hr = float(intercept)
        if start_hr <= 0:
            continue
        win = {
            "drift_bpm_per_hr": round(drift_bpm_hr, 1),
            "drift_pct_per_hr": round(100.0 * drift_bpm_hr / start_hr, 1),
            "duration_min": round((x[-1] - x[0]) / 60.0, 1),
            "start_min": round((x[0] - ts[0]) / 60.0, 1),
            "mean_power": round(float(np.mean(seg_w)), 0),
            "mean_hr": round(float(np.mean(seg_h)), 0),
            "start_hr": round(start_hr, 0),
            "end_hr": round(float(pred[-1]), 0),
            "r2": round(r2, 3),
            "n_points": int(len(x)),
        }
        # Prefer the strongest drift among qualifying windows; ties -> longest.
        if best is None or (
            abs(win["drift_bpm_per_hr"]) > abs(best["drift_bpm_per_hr"]) + 0.5
            or (abs(win["drift_bpm_per_hr"]) - abs(best["drift_bpm_per_hr"]) <= 0.5
                and win["n_points"] > best["n_points"])
        ):
            best = win

    if best is None:
        return None
    best["n_windows"] = len(windows)
    return best


# ---------------------------------------------------------------------------
# Climb stats
# ---------------------------------------------------------------------------

def _climb_metrics(records):
    grades = np.array([r.get("grade") or 0.0 for r in records])
    climbing = grades > config.CLIMB_GRADE
    vam = 0.0
    if climbing.any():
        climb_idx = np.where(climbing)[0]
        # vertical speed on climbing segments (m/h)
        segments = np.split(climb_idx, np.where(np.diff(climb_idx) > 1)[0] + 1)
        rates = []
        for seg in segments:
            if len(seg) < 2:
                continue
            de = records[seg[-1]].get("elev", 0.0) - records[seg[0]].get("elev", 0.0)
            dt = records[seg[-1]]["t"] - records[seg[0]]["t"]
            if dt > 5 and de > 1:
                rates.append(de / dt * 3600.0)
        if rates:
            vam = float(np.median(rates))

    # Gradient distribution histogram (percent).
    hist, edges = np.histogram(
        np.clip(grades * 100.0, -20, 20),
        bins=np.linspace(-20, 20, 21),
    )
    distribution = [
        {"from": round(edges[i], 1), "to": round(edges[i + 1], 1), "count": int(hist[i])}
        for i in range(len(hist))
    ]
    return {"vam_mph": vam, "grade_distribution": distribution}


# ---------------------------------------------------------------------------
# Calorie expenditure
# ---------------------------------------------------------------------------

# One food kilocalorie = 4.184 kJ of work. The human body is only ~20-25%
# efficient at turning food energy into pedal power; the widely used "1 kJ of
# power-meter work ≈ 1 kcal burned" rule assumes ~23.9% gross efficiency
# (Wahoo's convention, mid-range of the published 20-25% gross efficiency).
KJ_PER_KCAL = 4.184
GROSS_EFFICIENCY = 0.239

# HR -> EE prediction error: Keytel et al. 2005 (Med. Sci. Sports Exerc.)
# reports r^2 = 0.86 against indirect calorimetry, i.e. roughly +-15% on an
# individual ride. Without a known sex the wrong equation is used, so the
# band widens to cover the male/female spread.
HR_EE_UNCERTAINTY = 0.15
HR_EE_UNCERTAINTY_UNKNOWN_SEX = 0.22

# Minimum fraction of ride time covered by a HR signal before the HR-based
# estimate is trusted as the headline number.
HR_ENERGY_MIN_COVERAGE = 0.5

# Gross efficiency is not 23.9% for everyone; the published range for cycling
# is roughly 20-25%. A HR-derived *power* cross-check converts metabolic
# energy through that efficiency, so it carries this extra asymmetric
# uncertainty on top of the HR -> EE equation error.
HR_EFFICIENCY_LO = 0.20
HR_EFFICIENCY_HI = 0.25

# Keytel et al. 2005 prediction equations for energy expenditure during
# submaximal exercise: EE (kJ/min) = a + b*HR + c*weight_kg + d*age.
_KEYTEL = {
    "male": (-55.0969, 0.6309, 0.1988, 0.2017),
    "female": (-20.4022, 0.4472, -0.1263, 0.074),
}


def _integrate_work_kj(records, key="watts_est"):
    """Trapezoid-integrate a per-point power series into kJ of mechanical work.

    Gaps longer than 30 s are treated as pauses (stopped at a junction the
    model reads ~0 W anyway; a mid-climb dropout must not invent a minute of
    effort from the two bracketing samples).
    """
    ts = np.array([r["t"] for r in records], dtype=float)
    w = np.array([r.get(key) or 0.0 for r in records], dtype=float)
    if len(ts) < 2:
        return 0.0
    dt = np.maximum(np.minimum(np.diff(ts), 30.0), 0.0)
    return float(np.sum(dt * 0.5 * (w[:-1] + w[1:]))) / 1000.0


def _hr_energy_kcal(records, rider):
    """Gross kcal from per-second HR, using the Keytel 2005 equations.

    Returns (kcal, seconds_covered). HR is sampled at the current record and
    applied to the interval since the previous record, exactly like TRIMP.
    The equations already include the resting component of metabolism, so no
    basal rate is added on top.
    """
    sex = str(rider.get("sex") or "").strip().lower()
    a, b, c, d = _KEYTEL.get(sex, _KEYTEL["male"])
    weight = float(rider.get("weight_kg", 75.0))
    age = float(rider.get("age", 40))

    kcal = 0.0
    covered = 0.0
    prev_t = None
    for r in records:
        t = r["t"]
        if prev_t is not None:
            dt = max(0.0, min(t - prev_t, 10.0))
            hr = r.get("hr")
            if hr is not None:
                covered += dt
                kj_min = a + b * hr + c * weight + d * age
                kcal += max(0.0, kj_min) * dt / 60.0 / KJ_PER_KCAL
        prev_t = t
    return kcal, covered


def estimate_calories(records, rider):
    """Estimate gross energy expenditure (kcal) for a ride.

    Two independent methods, both reported:

    * ``power`` — mechanical work from the estimated power series, divided by
      gross metabolic efficiency. Exactly the "kJ \u2248 kcal" rule, with the
      band propagated from the power model's own watts_lo/watts_hi.
    * ``hr`` — Keytel et al. 2005 equations applied per second of recorded
      heart rate. HR sees the whole ride, including coasts and descents the
      power model reports as ~0 W, so it is the headline whenever the signal
      covers at least half the ride.

    Returns a dict (or None for an empty ride): kcal/lo/hi headline plus the
    cross-checks and a plain-language note.
    """
    if not records:
        return None
    duration = max(records[-1]["t"] - records[0]["t"], 0.0)

    power_kcal = _integrate_work_kj(records, "watts_est") / KJ_PER_KCAL / GROSS_EFFICIENCY
    power_lo = _integrate_work_kj(records, "watts_lo") / KJ_PER_KCAL / GROSS_EFFICIENCY
    power_hi = _integrate_work_kj(records, "watts_hi") / KJ_PER_KCAL / GROSS_EFFICIENCY

    hr_kcal, hr_covered = _hr_energy_kcal(records, rider)
    coverage = hr_covered / duration if duration > 0 else 0.0
    sex_known = bool(str(rider.get("sex") or "").strip())

    if hr_kcal > 0 and coverage >= HR_ENERGY_MIN_COVERAGE:
        frac = HR_EE_UNCERTAINTY if sex_known else HR_EE_UNCERTAINTY_UNKNOWN_SEX
        kcal, lo, hi = hr_kcal, hr_kcal * (1.0 - frac), hr_kcal * (1.0 + frac)
        method = "hr"
        note = ("From heart rate (Keytel equations): this sees the whole ride, "
                "including coasts and descents the power model reads as 0 W.")
    else:
        kcal, lo, hi = power_kcal, power_lo, power_hi
        method = "power"
        note = ("From estimated mechanical work at ~24% gross metabolic "
                "efficiency (the kJ \u2248 kcal rule). No usable HR signal, so "
                "coasting effort is not counted.")

    # HR-derived average mechanical power cross-check: invert the calorie
    # estimate through the same 4.184 kJ/kcal and gross efficiency. Only
    # reported when the HR signal is trusted enough to headline the calories
    # (same coverage rule), and only for the whole ride - HR has no
    # second-by-second resolution, so this is an average, not a series.
    hr_power = None
    if hr_kcal > 0 and duration > 0 and coverage >= HR_ENERGY_MIN_COVERAGE:
        watts_hr = hr_kcal * KJ_PER_KCAL * GROSS_EFFICIENCY * 1000.0 / duration
        eff_lo_frac = (GROSS_EFFICIENCY - HR_EFFICIENCY_LO) / GROSS_EFFICIENCY
        eff_hi_frac = (HR_EFFICIENCY_HI - GROSS_EFFICIENCY) / GROSS_EFFICIENCY
        lo_frac = math.hypot(frac, eff_lo_frac)
        hi_frac = math.hypot(frac, eff_hi_frac)
        hr_power = {
            "watts": round(watts_hr),
            "lo": round(watts_hr * (1.0 - lo_frac)),
            "hi": round(watts_hr * (1.0 + hi_frac)),
        }

    return {
        "kcal": round(kcal),
        "lo": round(max(0.0, lo)),
        "hi": round(max(0.0, hi)),
        "method": method,
        "hr_kcal": round(hr_kcal) if hr_kcal > 0 else None,
        "hr_coverage": round(coverage, 2),
        "hr_power": hr_power,
        "power_kcal": round(power_kcal),
        "power_lo": round(max(0.0, power_lo)),
        "power_hi": round(max(0.0, power_hi)),
        "note": note,
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def compute_ride_metrics(records, rider, bike, elev_summary, meta):
    """Aggregate per-ride metrics from fully-processed records."""
    hr = _hr_metrics(records, rider)
    power = _power_metrics(records, rider)
    climb = _climb_metrics(records)

    distance = meta.get("total_distance") or _track_distance(records)
    duration = meta.get("duration_seconds") or (records[-1]["t"] - records[0]["t"])

    has_hr = hr["avg_hr"] is not None
    drift = cardiac_drift(records, rider) if has_hr else None
    calories = estimate_calories(records, rider)
    return {
        "distance_m": float(distance),
        "duration_s": float(duration),
        "elevation_gain_m": float(elev_summary["gain_m"]),
        "min_elev": float(elev_summary["min_elev"]),
        "max_elev": float(elev_summary["max_elev"]),
        "elevation_source": elev_summary["elevation_source"],
        "snapped_ratio": float(elev_summary.get("snapped_ratio", 0.0)),
        "avg_speed_mps": float(distance / duration) if duration > 0 else 0.0,
        "max_speed_mps": float(max((r.get("speed") or 0.0) for r in records)) if records else 0.0,
        "avg_hr": hr["avg_hr"],
        "max_hr": hr["max_hr"],
        "min_hr": hr["min_hr"],
        "has_hr": has_hr,
        "trimp": hr["trimp"],
        "time_in_zone": hr["time_in_zone"],
        "vam_mph": climb["vam_mph"],
        "grade_distribution": climb["grade_distribution"],
        "avg_watts": power["avg_watts"],
        "avg_watts_lo": power["avg_watts_lo"],
        "avg_watts_hi": power["avg_watts_hi"],
        "normalized_power": power["normalized_power"],
        "normalized_power_lo": power["normalized_power_lo"],
        "normalized_power_hi": power["normalized_power_hi"],
        "power_curve": power["power_curve"],
        "vo2max": power["vo2max"],
        "watts_at_hr": power["watts_at_hr"],
        "pedalling_ratio": power["pedalling_ratio"],
        "avg_grade": float(np.mean([r.get("grade") or 0.0 for r in records])) if records else 0.0,
        "cardiac_drift": drift,
        "calories": calories,
    }


def _track_distance(records):
    import numpy as _np
    return float(_np.sum(_np.diff([r.get("dist") or 0.0 for r in records]).clip(min=0)))
