"""Geodesy and track-geometry helpers.

Contains a self-contained WGS84 -> OSGB36 (British National Grid) conversion so
we can query the EA National LIDAR WCS, which is gridded in EPSG:27700, without
pulling in pyproj/GDAL. The Helmert transform here is the standard 7-parameter
similarity (without the OSTN15 grid correction), good to ~5 m — plenty for
sampling terrain that is later smoothed over 15-25 m.
"""

import math

import numpy as np

# ---------------------------------------------------------------------------
# Basic spherical geometry
# ---------------------------------------------------------------------------

EARTH_RADIUS_M = 6371000.0


def in_uk(lat, lon):
    """Rough bounds check for the UK (used to keep elevation/weather sane)."""
    return 49.0 <= lat <= 60.9 and -8.5 <= lon <= 2.0


def haversine_m(lat1, lon1, lat2, lon2):
    """Great-circle distance in metres."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2.0) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2.0) ** 2
    return 2.0 * EARTH_RADIUS_M * math.asin(math.sqrt(max(0.0, min(1.0, a))))


def haversine_m_array(lats, lons):
    """Cumulative-distance profile along a track (metres from the first point)."""
    lats = np.asarray(lats, dtype=float)
    lons = np.asarray(lons, dtype=float)
    n = len(lats)
    if n == 0:
        return np.zeros(0)

    p = np.radians(lats)
    dp = np.radians(np.diff(lats))
    dl = np.radians(np.diff(lons))
    a = np.sin(dp / 2.0) ** 2 + np.cos(p[:-1]) * np.cos(p[1:]) * np.sin(dl / 2.0) ** 2
    a = np.clip(a, 0.0, 1.0)
    step = 2.0 * EARTH_RADIUS_M * np.arcsin(np.sqrt(a))
    return np.concatenate(([0.0], np.cumsum(step)))


def point_to_segment_dist(lat, lon, a, b):
    """Metres from a point to the segment (a, b), where a/b are (lat, lon)."""
    # Local equirectangular projection (fine at cycle-ride scales).
    y = (lat - a[0]) * 111320.0
    x = (lon - a[1]) * 111320.0 * math.cos(math.radians(a[0]))
    by = (b[0] - a[0]) * 111320.0
    bx = (b[1] - a[1]) * 111320.0 * math.cos(math.radians(a[0]))
    l2 = bx * bx + by * by
    if l2 <= 0.0:
        return math.hypot(x, y), (a[0], a[1])
    t = max(0.0, min(1.0, (x * bx + y * by) / l2))
    proj_y = a[0] + t * (b[0] - a[0])
    proj_x = a[1] + t * (b[1] - a[1])
    py = (lat - proj_y) * 111320.0
    px = (lon - proj_x) * 111320.0 * math.cos(math.radians(lat))
    return math.hypot(px, py), (proj_y, proj_x)


# ---------------------------------------------------------------------------
# OSGB36 / British National Grid
# ---------------------------------------------------------------------------

# Airy 1830 ellipsoid.
_AIRY_A = 6377563.396
_AIRY_B = 6356256.909
_F0 = 0.9996012717
_PHI0 = math.radians(49.0)
_LAM0 = math.radians(-2.0)
_N0 = -100000.0
_E0 = 400000.0

# WGS84 / ETRS89 ellipsoid.
_WGS_A = 6378137.0
_WGS_B = 6356752.314245

# 7-parameter Helmert, ETRS89 -> OSGB36 (approximate, no OSTN15).
_H_TX, _H_TY, _H_TZ = -446.448, 125.157, -542.060
_H_S = -20.4894e-6
_H_RX = math.radians(-0.1502 / 3600.0)
_H_RY = math.radians(-0.2470 / 3600.0)
_H_RZ = math.radians(-0.8421 / 3600.0)


def _ecef_from_geodetic(lat, lon, h, a, b):
    e2 = 1.0 - (b * b) / (a * a)
    nu = a / math.sqrt(1.0 - e2 * math.sin(lat) ** 2)
    x = (nu + h) * math.cos(lat) * math.cos(lon)
    y = (nu + h) * math.cos(lat) * math.sin(lon)
    z = (nu * (1.0 - e2) + h) * math.sin(lat)
    return x, y, z


def _geodetic_from_ecef(x, y, z, a, b):
    e2 = 1.0 - (b * b) / (a * a)
    ep2 = (a * a - b * b) / (b * b)
    p = math.hypot(x, y)
    lon = math.atan2(y, x)
    theta = math.atan2(z * a, p * b)
    lat = math.atan2(
        z + ep2 * b * math.sin(theta) ** 3,
        p - e2 * a * math.cos(theta) ** 3,
    )
    nu = a / math.sqrt(1.0 - e2 * math.sin(lat) ** 2)
    h = p / math.cos(lat) - nu
    return lat, lon, h


def wgs84_to_osgb36(lat_deg, lon_deg):
    """Return (easting, northing) in OSGB36 National Grid metres."""
    lat = math.radians(lat_deg)
    lon = math.radians(lon_deg)

    # WGS84 geodetic -> ECEF
    x, y, z = _ecef_from_geodetic(lat, lon, 0.0, _WGS_A, _WGS_B)

    # Helmert ETRS89 -> OSGB36
    x2 = _H_TX + (1.0 + _H_S) * (x - _H_RZ * y + _H_RY * z)
    y2 = _H_TY + (1.0 + _H_S) * (_H_RZ * x + y - _H_RX * z)
    z2 = _H_TZ + (1.0 + _H_S) * (-_H_RY * x + _H_RX * y + z)

    # ECEF -> OSGB36 geodetic
    plat, plon, _ = _geodetic_from_ecef(x2, y2, z2, _AIRY_A, _AIRY_B)

    # Transverse Mercator (6th-order series, OS "A guide to coordinate systems
    # in Great Britain", Annex C).
    e2 = 1.0 - (_AIRY_B * _AIRY_B) / (_AIRY_A * _AIRY_A)
    n = (_AIRY_A - _AIRY_B) / (_AIRY_A + _AIRY_B)
    nu = _AIRY_A * _F0 / math.sqrt(1.0 - e2 * math.sin(plat) ** 2)
    rho = _AIRY_A * _F0 * (1.0 - e2) / (1.0 - e2 * math.sin(plat) ** 2) ** 1.5
    eta2 = nu / rho - 1.0

    sin_p, cos_p = math.sin(plat), math.cos(plat)
    tan_p = math.tan(plat)

    dphi = plat - _PHI0
    dlam = plon - _LAM0

    n2, n3 = n * n, n * n * n
    M = _AIRY_B * _F0 * (
        (1.0 + n + 1.25 * n2 + 1.25 * n3) * dphi
        - (3.0 * n + 3.0 * n2 + 2.625 * n3) * math.sin(dphi) * math.cos(plat + _PHI0)
        + (1.875 * n2 + 1.875 * n3) * math.sin(2.0 * dphi) * math.cos(2.0 * (plat + _PHI0))
        - (35.0 / 24.0) * n3 * math.sin(3.0 * dphi) * math.cos(3.0 * (plat + _PHI0))
    )

    I = M + _N0
    II = (nu / 2.0) * sin_p * cos_p
    III = (nu / 24.0) * sin_p * cos_p ** 3 * (5.0 - tan_p ** 2 + 9.0 * eta2)
    IIIA = (nu / 720.0) * sin_p * cos_p ** 5 * (61.0 - 58.0 * tan_p ** 2 + tan_p ** 4)
    IV = nu * cos_p
    V = (nu / 6.0) * cos_p ** 3 * (nu / rho - tan_p ** 2)
    VI = (nu / 120.0) * cos_p ** 5 * (
        5.0 - 18.0 * tan_p ** 2 + tan_p ** 4 + 14.0 * eta2 - 58.0 * tan_p ** 2 * eta2
    )

    easting = _E0 + IV * dlam + V * dlam ** 3 + VI * dlam ** 5
    northing = I + II * dlam ** 2 + III * dlam ** 4 + IIIA * dlam ** 6

    return easting, northing


def wgs84_to_osgb36_array(lats, lons):
    """Vectorised convenience wrapper returning (eastings, northings)."""
    lats = np.asarray(lats, dtype=float).ravel()
    lons = np.asarray(lons, dtype=float).ravel()
    east = np.empty_like(lats)
    north = np.empty_like(lats)
    for i in range(len(lats)):
        east[i], north[i] = wgs84_to_osgb36(float(lats[i]), float(lons[i]))
    return east, north


# ---------------------------------------------------------------------------
# Track-derived arrays
# ---------------------------------------------------------------------------

def cumulative_distance(lats, lons):
    return haversine_m_array(lats, lons)


def smooth_over_distance(distance, values, window_m):
    """Plain moving average over a ~window_m metre boxcar.

    The window is defined in distance space: each point averages the points
    whose cumulative distance falls within +/- window_m/2. (Not
    distance-weighted — at 1-4 m point spacing the plain mean is equivalent
    to well under a centimetre of weighting error.)"""
    distance = np.asarray(distance, dtype=float)
    values = np.asarray(values, dtype=float)
    n = len(values)
    if n < 3 or window_m <= 0:
        return values.copy()

    out = np.empty_like(values)
    half = window_m / 2.0
    j = 0
    k = 0
    for i in range(n):
        lo = distance[i] - half
        hi = distance[i] + half
        while j < i and distance[j] < lo:
            j += 1
        k = max(k, i)
        while k < n - 1 and distance[k + 1] <= hi:
            k += 1
        if k <= j:
            out[i] = values[i]
        else:
            out[i] = np.mean(values[j:k + 1])
    return out


def grade_from_elevation(distance, elevation, baseline_m=60.0):
    """Grade (rise / run) as a least-squares slope over a distance window.

    Differentiating elevation directly is far too noisy at ~4 m point spacing;
    fitting the slope over a ~50-60 m baseline gives the grade the wheel
    actually experiences without inheriting GPS wander.
    """
    distance = np.asarray(distance, dtype=float)
    elevation = np.asarray(elevation, dtype=float)
    n = len(elevation)
    grade = np.zeros(n)
    if n < 3:
        return grade

    half = baseline_m / 2.0
    cs_x = np.concatenate(([0.0], np.cumsum(distance)))
    cs_y = np.concatenate(([0.0], np.cumsum(elevation)))
    cs_xx = np.concatenate(([0.0], np.cumsum(distance * distance)))
    cs_xy = np.concatenate(([0.0], np.cumsum(distance * elevation)))

    for i in range(n):
        d = distance[i]
        lo = max(0, int(np.searchsorted(distance, d - half, side="left")))
        hi = min(n - 1, int(np.searchsorted(distance, d + half, side="right")) - 1)
        if hi - lo + 1 < 3 or (distance[hi] - distance[lo]) <= 3.0:
            grade[i] = grade[i - 1] if i > 0 else 0.0
            continue
        cnt = hi - lo + 1
        sx = cs_x[hi + 1] - cs_x[lo]
        sy = cs_y[hi + 1] - cs_y[lo]
        sxx = cs_xx[hi + 1] - cs_xx[lo]
        sxy = cs_xy[hi + 1] - cs_xy[lo]
        denom = cnt * sxx - sx * sx
        if denom > 1e-9:
            grade[i] = (cnt * sxy - sx * sy) / denom
        else:
            grade[i] = grade[i - 1] if i > 0 else 0.0
    return grade


def median_filter_over_distance(distance, values, window_m=30.0):
    """Robust despike: median filter in distance space.

    Removes isolated GPS/lidar sampling spikes (single points 5-15 m off the
    local terrain) while preserving real hills. A distance window (rather than
    an index window) handles pauses correctly.
    """
    distance = np.asarray(distance, dtype=float)
    values = np.asarray(values, dtype=float)
    n = len(values)
    out = np.empty(n, dtype=float)
    half = window_m / 2.0
    for i in range(n):
        lo = int(np.searchsorted(distance, distance[i] - half, side="left"))
        hi = int(np.searchsorted(distance, distance[i] + half, side="right"))
        lo = max(0, lo)
        hi = min(n, hi)
        out[i] = float(np.median(values[lo:hi])) if hi > lo else float(values[i])
    return out
