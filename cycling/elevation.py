"""The elevation stage of the pipeline.

map-match -> sample DTM (lidar, else DEM fallback) -> smooth 15-25 m -> grade.

This is the highest-risk piece of the whole plan: if map-match + smoothing do
not produce clean grade, every downstream number inherits the error. The stage
therefore never throws on missing data — it degrades gracefully and reports
which elevation source it actually used.
"""

import math

import numpy as np

from . import config, geo, lidar, mapmatch


def _fill_gaps(values, lats, lons, alt_raw):
    """Fill missing sampled elevations by interpolation, then device altitude."""
    values = np.asarray(values, dtype=float).copy()
    n = len(values)
    valid = ~np.isnan(values)

    # Interpolate gaps between valid samples.
    if valid.any() and not valid.all():
        idx = np.arange(n)
        values[~valid] = np.interp(idx[~valid], idx[valid], values[valid])

    # Remaining gaps (leading/trailing or no samples at all): device altitude.
    if alt_raw is not None:
        alt = np.asarray(alt_raw, dtype=float)
        for i in range(n):
            if not np.isfinite(values[i]) and np.isfinite(alt[i]):
                values[i] = alt[i]

    # Final gap fill.
    valid = np.isfinite(values)
    if valid.any() and not valid.all():
        idx = np.arange(n)
        values[~valid] = np.interp(idx[~valid], idx[valid], values[valid])
    return values


def _elevation_gain(distance, elevation, threshold=1.0, bin_m=None):
    """Total ascent: sum of positive deltas of the profile resampled to a
    fixed distance grid.

    Differencing raw ~4 m lidar points against a fixed absolute threshold is
    spacing-dependent (a 1 m step at 4 m spacing reads as a 25% grade).
    Resampling to ~25 m bins makes the threshold mean "rise per 25 m",
    independent of the source resolution, and a sub-1% threshold lets real
    1-2% climbs count while still rejecting smoothing noise.
    """
    distance = np.asarray(distance, dtype=float)
    elevation = np.asarray(elevation, dtype=float)
    if distance.size < 2:
        return 0.0
    total = float(distance[-1])
    if not np.isfinite(total) or total <= 0.0:
        return 0.0
    bin_m = bin_m or config.SMOOTH_DISTANCE_M
    nb = max(2, int(round(total / bin_m)))
    edges = np.linspace(0.0, total, nb + 1)
    idx = np.clip(np.digitize(distance, edges) - 1, 0, nb - 1)
    means = np.full(nb, np.nan)
    for b in range(nb):
        m = idx == b
        vals = elevation[m][np.isfinite(elevation[m])]
        if vals.size:
            means[b] = float(vals.mean())
    valid = np.isfinite(means)
    if not valid.any():
        return 0.0
    if not valid.all():
        means = np.interp(np.arange(nb), np.arange(nb)[valid], means[valid])
    de = np.diff(means)
    return float(de[de > threshold].sum())


def build_elevation(records, progress_cb=None):
    """Attach lidar-repaired elevation and grade to every record.

    Returns ``(records_out, summary)``. ``records_out`` is the input list with
    ``elev_raw`` (sampled), ``elev`` (smoothed) and ``grade`` added per point.
    ``summary`` carries the elevation source, gain, snapped ratio, etc.
    """
    n = len(records)
    lats = np.array([r["lat"] for r in records], dtype=float)
    lons = np.array([r["lon"] for r in records], dtype=float)
    alt_raw = np.array([r.get("alt_raw") for r in records], dtype=float)

    # 1) Map-match onto the road network (best effort).
    snapped_lats, snapped_lons, mm_info = mapmatch.map_match(lats, lons, progress_cb)

    # 2) Sample terrain at the snapped positions.
    if progress_cb:
        progress_cb(25, "Sampling elevation...")
    sampled, source = lidar.sample_elevations(
        snapped_lats, snapped_lons,
        prefer_lidar=config.PREFER_LIDAR,
        progress_cb=progress_cb,
    )

    # 3) Fill gaps and apply device altitude as the last resort.
    sampled_arr = np.array([v if v is not None else np.nan for v in sampled], dtype=float)
    elev = _fill_gaps(sampled_arr, snapped_lats, snapped_lons, alt_raw)

    if not np.isfinite(elev).all():
        # Nothing usable anywhere: leave elevation as device values or flat.
        elev = np.where(np.isfinite(elev), elev,
                        np.nanmedian(alt_raw) if np.isfinite(alt_raw).any() else 0.0)
        source = source or "device"

    # 4) Cumulative distance for distance-based smoothing.
    distance = geo.cumulative_distance(snapped_lats, snapped_lons)
    # Prefer the device's cumulative distance when present (it is more stable
    # than GPS point-to-point distance), but fall back to geodesy.
    device_dist = np.array([r.get("dist") for r in records], dtype=float)
    if np.isfinite(device_dist).sum() > n * 0.9:
        distance = np.where(np.isfinite(device_dist), device_dist, distance)
        distance = np.maximum.accumulate(np.maximum(distance, 0.0))

    # 5) Robust despike (median), then smooth over ~15-25 m, then grade.
    if progress_cb:
        progress_cb(48, "Despiking and smoothing elevation...")
    median = geo.median_filter_over_distance(
        distance, elev, config.ELEV_MEDIAN_WINDOW_M
    )
    smooth = geo.smooth_over_distance(distance, median, config.SMOOTH_DISTANCE_M)
    grade = geo.grade_from_elevation(distance, smooth, config.GRADE_BASELINE_M)
    grade = np.clip(grade, -config.MAX_GRADE, config.MAX_GRADE)

    records_out = []
    for i, r in enumerate(records):
        rec = dict(r)
        rec["elev_raw"] = float(sampled[i]) if sampled[i] is not None else None
        rec["elev"] = float(smooth[i])
        rec["grade"] = float(grade[i])
        records_out.append(rec)

    summary = {
        "elevation_source": source or "device",
        "gain_m": float(_elevation_gain(distance, smooth,
                                        threshold=config.ELEVATION_GAIN_THRESHOLD)),
        "min_elev": float(np.nanmin(smooth)),
        "max_elev": float(np.nanmax(smooth)),
        "mapmatch_segments": mm_info.get("segments", 0),
        "snapped_ratio": mm_info.get("snapped_ratio", 0.0),
    }
    if progress_cb:
        progress_cb(55, f"Elevation done ({source}); gain {summary['gain_m']:.0f} m")
    return records_out, summary
