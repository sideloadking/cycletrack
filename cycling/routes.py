"""Route detection and same-route comparison.

Repeated routes are where the plan gets its *context* fitness signals: watts
at a fixed HR on the same roads is a fairer comparison than across different
terrain. This module fingerprints each ride's track and groups rides that
follow the same route, tolerating the 3-10 m wander of consumer GPS and both
directions of travel (out-and-back, or the same loop ridden in reverse —
exactly the situation the loop-CdA calibration needs).
"""

import math

import numpy as np

from . import geo

FP_N_POINTS = 120          # fingerprint resolution: points evenly spaced along the track
ROUTE_MATCH_M = 25.0       # mean symmetric nearest-distance counted as "same route" (m)
ROUTE_P95_M = 45.0         # 95th-percentile nearest-distance cap (m)
LENGTH_RATIO_TOL = 0.25    # allow ±25% length difference (start/stop points vary ride to ride)
BBOX_PREFILTER_M = 60.0    # bbox padding used to skip obviously-different routes


def fingerprint(lats, lons, n=FP_N_POINTS):
    """Resample a track to ``n`` evenly-spaced points by cumulative distance.

    Returns ``(pts, length_m, bbox)`` where ``pts`` is a list of (lat, lon)
    tuples, ``length_m`` the total track length, and ``bbox`` is
    [lat_min, lon_min, lat_max, lon_max]. Returns None for degenerate tracks.
    """
    lats = np.asarray(lats, dtype=float)
    lons = np.asarray(lons, dtype=float)
    if lats.size < 2:
        return None
    dist = geo.haversine_m_array(lats, lons)
    total = float(dist[-1])
    if not np.isfinite(total) or total <= 1.0:
        return None

    if lats.size <= n:
        pts = [(float(la), float(lo)) for la, lo in zip(lats, lons)]
    else:
        targets = np.linspace(0.0, total, n)
        pts = list(zip(
            np.interp(targets, dist, lats).tolist(),
            np.interp(targets, dist, lons).tolist(),
        ))
    bbox = [
        float(np.min(lats)), float(np.min(lons)),
        float(np.max(lats)), float(np.max(lons)),
    ]
    return pts, total, bbox


def _local(lat0, lon0, pts):
    """Project fingerprint points into a local equirectangular metre frame."""
    x = np.array([(lon - lon0) * 111320.0 * math.cos(math.radians(lat0))
                  for lat, lon in pts], dtype=float)
    y = np.array([(lat - lat0) * 111320.0 for lat, lon in pts], dtype=float)
    return x, y


def _nearest_stats(xa, ya, xb, yb):
    """(mean, p95) of the distance from each point of A to the nearest point of B."""
    n = len(xa)
    if n == 0:
        return 0.0, 0.0
    d = np.empty(n, dtype=float)
    for i in range(n):
        d[i] = float(np.min(np.hypot(xa[i] - xb, ya[i] - yb)))
    return float(d.mean()), float(np.percentile(d, 95))


def route_similarity(fp_a, fp_b):
    """Compare two fingerprints in both directions.

    Returns a dict with forward/reversed ``(mean, p95)`` nearest distances in
    metres, or None when the tracks are too different in length to bother.
    """
    pts_a, len_a, _ = fp_a
    pts_b, len_b, _ = fp_b
    if min(len_a, len_b) <= 0.0:
        return None
    if max(len_a, len_b) / min(len_a, len_b) > 1.0 + LENGTH_RATIO_TOL:
        return None
    lat0, lon0 = pts_a[0]
    xa, ya = _local(lat0, lon0, pts_a)
    xb, yb = _local(lat0, lon0, pts_b)
    fwd = _nearest_stats(xa, ya, xb, yb)
    rev = _nearest_stats(xa, ya, xb[::-1], yb[::-1])
    return {"fwd": fwd, "rev": rev}


def is_same_route(sim):
    """True when either travel direction matches within the tolerance."""
    if sim is None:
        return False
    for mean, p95 in (sim["fwd"], sim["rev"]):
        if mean <= ROUTE_MATCH_M and p95 <= ROUTE_P95_M:
            return True
    return False


def _bbox_overlap_passes(bbox_a, bbox_b, pad_m=BBOX_PREFILTER_M):
    """Rough prefilter: do the two bounding boxes overlap once padded?"""
    lat0 = (bbox_a[0] + bbox_a[2]) / 2.0
    pad_lat = pad_m / 111320.0
    pad_lon = pad_m / (111320.0 * max(0.05, math.cos(math.radians(lat0))))
    if bbox_a[0] - pad_lat > bbox_b[2] + pad_lat or bbox_b[0] - pad_lat > bbox_a[2] + pad_lat:
        return False
    if bbox_a[1] - pad_lon > bbox_b[3] + pad_lon or bbox_b[1] - pad_lon > bbox_a[3] + pad_lon:
        return False
    return True


def group_rides(ride_fps):
    """Group rides into routes.

    ``ride_fps`` is an iterable of ``(ride_id, fp)`` in chronological order.
    Returns a list of routes, each::

        {
            "ref_ride_id": int, "ref_fp": fp, "bbox": [...],
            "length_m": float, "ride_ids": [int, ...],
        }

    Each new ride is compared against the reference ride of every existing
    route (a connected-component approximation — good enough here, where rides
    of the same loop always match the first ride of that loop).
    """
    routes = []
    for ride_id, fp in ride_fps:
        if fp is None:
            continue
        pts, length, bbox = fp
        best = None
        for r in routes:
            if not _bbox_overlap_passes(bbox, r["bbox"]):
                continue
            sim = route_similarity(fp, r["ref_fp"])
            if is_same_route(sim):
                score = min(sim["fwd"][0], sim["rev"][0])
                if best is None or score < best[0]:
                    best = (score, r)
        if best is None:
            routes.append({
                "ref_ride_id": ride_id,
                "ref_fp": fp,
                "bbox": bbox,
                "length_m": length,
                "ride_ids": [ride_id],
            })
        else:
            best[1]["ride_ids"].append(ride_id)
    return routes
