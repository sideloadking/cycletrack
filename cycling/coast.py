"""Descent detection and coast-vs-pedal classification.

Phase 0 of the verified-coast plan splits the old ``find_coast_segments``
into two concerns:

- ``find_descent_segments`` — candidate detection, geometry only (grade +
  speed, no HR), so pedalled descents still appear as candidates to review.
- ``classify_descents`` — a caution-first logistic that only auto-labels a
  descent "coast" or "pedal" when confident, otherwise returns "ask".

The fit path is deliberately untouched in this phase; these functions feed
the tagging UI and the ``/api/rides/{id}/descents`` endpoint.
"""

import math

import numpy as np

from . import config

# Decision rule (caution-asymmetric). Auto-label only when confident; the
# band between is "ask" and the thresholds are deliberately wide.
COAST_P_HI = 0.90
PEDAL_P_LO = 0.10
MIN_POINTS = 10        # minimum points in a descent run
MIN_SPEED_MPS = 2.0    # moving faster than this
MIN_HR_TREND_S = 30.0  # need ~30 s of HR before trusting a trend

# Learning (phase 4) — the model refits from manual tags only and is blended
# toward the cold-start prior so a small one-sided history cannot overwrite it.
PRIOR_STRENGTH = 5.0        # ridge toward the prior, in pseudo-observations
MAX_WEIGHT_DEV_FRAC = 0.6   # learned weights may move at most this fraction off the prior
MAX_WEIGHT_DEV_ABS = 0.5    # ...plus a small absolute slack (covers near-zero weights)
NOVELTY_RADIUS = 3.0        # scaled distance beyond which a descent is "novel" => ask
# Fixed per-feature scales for the novelty distance (meaningful unit per
# feature, not data-dependent, so the threshold is stable on tiny histories).
_FEATURE_SCALES = {
    "hr_margin": 0.15,   # ≈ 27 bpm of normalized HR margin at max HR 180
    "hr_trend": 0.4,     # a clear HR fall in the same unit as the feature
    "speed_jitter": 0.4, # a clear pedalling stroke jitter
    "frac_low": 0.4,     # a large share of points below the threshold
}

_FEATURES = ("hr_margin", "hr_trend", "speed_jitter", "frac_low")


def default_weights():
    """Cold-start prior for the coast classifier.

    Dominated by ``hr_margin`` (how far the run's mean HR sits below the old
    0.78·max heuristic), with light trend/jitter tiebreakers. Calibrated so a
    resting descent (HR ≈ 50% max) scores ~0.98, a hard-pedalled descent
    (HR ≈ 86% max) scores ~0.03, and the band between is "ask". The learned
    model replaces these weights in phase 4.
    """
    return {
        "hr_margin": 16.356,
        "hr_trend": 0.8,
        "speed_jitter": -0.8,
        "frac_low": 1.0,
        "bias": -1.636,
    }


def find_descent_segments(records):
    """Contiguous descending, moving runs — geometry only, no HR.

    Returns a list of segment dicts ``{t_start, t_end, records}``. Unlike the
    old coast finder, HR is deliberately ignored so a pedalled descent
    (elevated HR) still appears as a candidate to review.
    """
    segs = []
    cur = []
    for r in records:
        grade = r.get("grade") or 0.0
        speed = r.get("speed") or 0.0
        if grade < config.DOWNHILL_GRADE and speed > MIN_SPEED_MPS:
            cur.append(r)
        else:
            if len(cur) >= MIN_POINTS:
                segs.append(cur)
            cur = []
    if len(cur) >= MIN_POINTS:
        segs.append(cur)
    return [
        {"t_start": float(s[0]["t"]), "t_end": float(s[-1]["t"]), "records": s}
        for s in segs
    ]


def segment_features(seg, max_hr):
    """Feature vector for one descent segment, or None when HR is unusable.

    ``seg`` is a segment dict from ``find_descent_segments``. Features:

    - ``hr_margin``: (0.78·max_hr − mean_hr) / max_hr — positive means the
      run sits below the old coasting threshold (coast-like).
    - ``hr_trend``: falling-HR rate, positive when HR is falling (coast-like).
    - ``speed_jitter``: std of acceleration — pedalling adds stroke jitter.
    - ``frac_low``: fraction of HR points below 0.78·max_hr.
    """
    records = seg["records"] if isinstance(seg, dict) else seg
    hrs = [r.get("hr") for r in records if r.get("hr") is not None]
    if not hrs or not max_hr:
        return None

    mean_hr = float(np.mean(hrs))
    hr_margin = (0.78 * max_hr - mean_hr) / max_hr
    frac_low = float(np.mean([1.0 if h < 0.78 * max_hr else 0.0 for h in hrs]))

    # HR trend over the run: falling HR => coast-like => positive feature.
    hr_trend = 0.0
    pts = [(r["t"], r["hr"]) for r in records if r.get("hr") is not None]
    if len(pts) >= 3:
        t = np.array([p[0] for p in pts], dtype=float)
        h = np.array([p[1] for p in pts], dtype=float)
        span = t[-1] - t[0]
        if span > MIN_HR_TREND_S:
            slope_bpm_min = float(np.polyfit(t, h, 1)[0]) * 60.0
            hr_trend = float(np.clip(-slope_bpm_min / 10.0, -1.0, 1.0))

    # Speed jitter: std of acceleration, normalised so ~0.3 m/s² ≈ 1.0.
    speeds = np.array([r.get("speed") or 0.0 for r in records], dtype=float)
    ts = np.array([r["t"] for r in records], dtype=float)
    dt = np.diff(ts)
    dv = np.diff(speeds)
    with np.errstate(divide="ignore", invalid="ignore"):
        acc = np.where(dt > 0, dv / np.maximum(dt, 1e-6), 0.0)
    acc = acc[np.isfinite(acc)]
    speed_jitter = float(np.std(acc)) / 0.3 if acc.size >= 2 else 0.0

    return {
        "hr_margin": hr_margin,
        "hr_trend": hr_trend,
        "speed_jitter": speed_jitter,
        "frac_low": frac_low,
    }


def coast_probability(features, weights=None):
    """P(coast | features) under a logistic model."""
    weights = weights if weights is not None else default_weights()
    z = float(weights.get("bias", 0.0))
    for name in _FEATURES:
        z += float(weights.get(name, 0.0)) * float(features[name])
    if z > 40.0:
        return 1.0
    if z < -40.0:
        return 0.0
    return 1.0 / (1.0 + math.exp(-z))


def classify_segment(seg, max_hr, weights=None, training_features=None):
    """Classify one descent segment, erring toward "ask".

    Returns ``{label, score, reason}``. ``score`` is P(coast), or None when
    HR is unavailable — in which case the label is "ask" rather than a guess.
    A descent whose features are far from every labelled example
    (``training_features``) is always "ask" (novelty / out-of-distribution),
    regardless of the model's confidence.
    """
    feats = segment_features(seg, max_hr)
    if feats is None:
        return {"label": "ask", "score": None, "reason": "no_hr"}
    if training_features and _novel(feats, training_features):
        return {"label": "ask", "score": coast_probability(feats, weights),
                "reason": "novel"}
    p = coast_probability(feats, weights)
    if p >= COAST_P_HI:
        label = "coast"
    elif p <= PEDAL_P_LO:
        label = "pedal"
    else:
        label = "ask"
    return {"label": label, "score": p, "reason": None}


def classify_descents(records, max_hr=None, weights=None, training_features=None):
    """Classify every descending run in a ride.

    Returns a list of dicts ``{t_start, t_end, n_points, label, score,
    reason}``. When ``max_hr`` is unknown every run is "ask" (caution: no
    HR reference means no confident coast/pedal call). ``weights`` and
    ``training_features`` come from ``fit_classifier`` and its inputs.
    """
    segs = find_descent_segments(records)
    out = []
    for seg in segs:
        cls = classify_segment(seg, max_hr, weights, training_features)
        out.append({
            "t_start": seg["t_start"],
            "t_end": seg["t_end"],
            "n_points": len(seg["records"]),
            "label": cls["label"],
            "score": cls["score"],
            "reason": cls["reason"],
        })
    return out


def _weight_vector(weights):
    return np.array([float(weights[n]) for n in _FEATURES]
                    + [float(weights.get("bias", 0.0))], dtype=float)


def _weights_from_vector(theta):
    out = {n: float(theta[i]) for i, n in enumerate(_FEATURES)}
    out["bias"] = float(theta[-1])
    return out


def build_training_examples(records, segments, max_hr):
    """Build ``(features, y)`` pairs from a ride's records and manual tags.

    ``segments`` are the stored manual coast segments for one ride. ``y`` is
    1 for a "coast" tag and 0 for "pedal"/"brake". Segments with no usable HR
    cannot train the HR-based model and are skipped.
    """
    examples = []
    for seg in segments:
        label = seg.get("label")
        if label not in ("coast", "pedal", "brake"):
            continue
        sub = [r for r in records if seg["t_start"] <= r["t"] <= seg["t_end"]]
        if len(sub) < MIN_POINTS:
            continue
        feats = segment_features({"records": sub}, max_hr)
        if feats is None:
            continue
        examples.append((feats, 1.0 if label == "coast" else 0.0))
    return examples


def fit_classifier(examples, prior_weights=None, prior_strength=PRIOR_STRENGTH):
    """Fit a caution-blended logistic to manual tags, or return the prior.

    ``examples`` is a list of ``(features_dict, y)`` with y=1 for coast and
    0 for pedal/brake. The MAP objective ridges every weight toward
    ``prior_weights`` (skeptical prior floor: the coast class keeps its prior
    weight even when no coast has ever been labelled), and the fitted weights
    are clipped to a box around the prior so the ask band cannot be trained
    away (ask floor). No examples => the cold-start prior unchanged.
    """
    prior = dict(prior_weights if prior_weights is not None else default_weights())
    if not examples:
        return prior
    X = np.array([[ex[0][n] for n in _FEATURES] + [1.0] for ex in examples],
                 dtype=float)
    y = np.array([1.0 if ex[1] else 0.0 for ex in examples], dtype=float)
    theta0 = _weight_vector(prior)

    def loss(theta):
        z = X @ theta
        nll = -float(np.sum(y * z - np.logaddexp(0.0, z)))
        reg = 0.5 * prior_strength * float(np.sum((theta - theta0) ** 2))
        return nll + reg

    from scipy.optimize import minimize
    try:
        res = minimize(loss, theta0, method="L-BFGS-B", options={"maxiter": 500})
        theta = np.asarray(res.x, dtype=float)
        if not np.isfinite(theta).all():
            return prior
    except Exception:
        return prior

    dev = MAX_WEIGHT_DEV_FRAC * np.abs(theta0) + MAX_WEIGHT_DEV_ABS
    theta = np.clip(theta, theta0 - dev, theta0 + dev)
    return _weights_from_vector(theta)


def _novel(features, training_features, radius=NOVELTY_RADIUS):
    """True when a feature vector is far from every labelled example.

    Distance is Euclidean with fixed per-feature scales (see
    ``_FEATURE_SCALES``), so the threshold stays meaningful on a tiny or
    one-sided history.
    """
    if not training_features:
        return False
    query = np.array([features[n] for n in _FEATURES], dtype=float)
    best = float("inf")
    for f in training_features:
        d = 0.0
        for i, n in enumerate(_FEATURES):
            d += ((f[n] - query[i]) / _FEATURE_SCALES[n]) ** 2
        best = min(best, math.sqrt(d))
    return best > radius


def effective_coast_segments(records, max_hr, weights=None, training_features=None):
    """Descent segments the fit may trust as zero-power coasts.

    A manual ``coast_label == "coast"`` always qualifies; a manual
    ``pedal``/``brake`` never does. Untagged descents qualify only when the
    caution-first classifier is confident (``P(coast) >= COAST_P_HI``) — the
    "ask" band is deliberately *not* fed to the fit. Returns a list of
    segment record-lists (the same shape ``find_coast_segments`` returns).
    """
    out = []
    for seg in find_descent_segments(records):
        labels = {r.get("coast_label") for r in seg["records"]}
        if "pedal" in labels or "brake" in labels:
            continue
        if "coast" in labels:
            out.append(seg["records"])
            continue
        cls = classify_segment(seg, max_hr, weights, training_features)
        if cls["label"] == "coast":
            out.append(seg["records"])
    return out


def apply_segment_overrides(records, segments):
    """Tag records with ``coast_label`` from stored coast segments.

    ``segments`` is the list returned by ``storage.get_coast_segments``.
    Each record whose ``t`` falls inside a segment gets that segment's label;
    the downstream coast finder treats a manual "coast" as trusted zero-power
    and a manual "pedal"/"brake" as never coasting. Records are mutated in
    place and returned for convenience.
    """
    for seg in segments:
        label = seg["label"]
        for r in records:
            if seg["t_start"] <= r["t"] <= seg["t_end"]:
                r["coast_label"] = label
    return records
