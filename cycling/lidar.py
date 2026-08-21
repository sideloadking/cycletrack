"""Elevation sampling.

Three tiers, in order of preference:

1. **EA National LIDAR** composite DTM (1 m, England, OGL) via WCS. Queried
   in the native EPSG:27700 grid using our own WGS84->OSGB36 conversion, then
   downsampled to a bounded raster and cached on disk per bounding box.
2. **OpenTopoData EU-DEM** (25 m) — the reliable fallback used by the original
   prototype, cached per point.
3. **Terrarium** (~30 m global DEM tiles) — final web fallback.

Every provider is best-effort: any failure or timeout degrades to the next
tier rather than aborting the import. This mirrors the plan's risk table
("lidar tiles too large to sample cheaply -> cache + pyramid; fall back to
25 m DEM with heavier smoothing").
"""

import io
import json
import math
import pathlib
import time
from typing import Callable, Optional

import numpy as np
import requests

from . import config, geo
from .tiffread import read_tiff

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _session():
    s = requests.Session()
    s.headers["User-Agent"] = "CyclingProgressTracker/0.1 (local, private)"
    return s


def _get_capped(session, url, params, cap_bytes, timeout):
    """Stream a GET and abort if the body exceeds ``cap_bytes``."""
    with session.get(url, params=params, timeout=timeout, stream=True) as r:
        r.raise_for_status()
        chunks = []
        total = 0
        for chunk in r.iter_content(65536):
            total += len(chunk)
            if total > cap_bytes:
                raise RuntimeError("Lidar response exceeded size cap")
            chunks.append(chunk)
        return b"".join(chunks)


def _plausible(elev, lat=None, lon=None):
    try:
        elev = float(elev)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(elev):
        return None
    if elev < -200.0 or elev > 5000.0:
        return None
    if lat is not None and lon is not None and geo.in_uk(lat, lon) and elev > 1500.0:
        return None
    return elev


# ---------------------------------------------------------------------------
# Tier 1 — EA National LIDAR (DTM)
# ---------------------------------------------------------------------------


class LidarProvider:
    """Samples the EA 1 m DTM by downloading a downsampled GeoTIFF per bbox."""

    def __init__(self, cache_dir=None):
        self.cache_dir = pathlib.Path(cache_dir or config.LIDAR_CACHE)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.session = _session()

    def _cache_path(self, emin, emax, nmin, nmax, w, h):
        key = f"{emin:.0f}_{emax:.0f}_{nmin:.0f}_{nmax:.0f}_{w}_{h}.npy"
        return self.cache_dir / key

    def _fetch_raster(self, emin, emax, nmin, nmax):
        """Download a (padded) bbox as a 2D elevation array + its bounds."""
        span_e = emax - emin
        span_n = nmax - nmin
        long_side = max(span_e, span_n)
        # ~1 px per metre up to the cap; beyond that we downsample.
        size = int(min(long_side, config.LIDAR_MAX_PIXELS))
        size = max(size, 2)
        w = int(round(span_e / long_side * size))
        h = int(round(span_n / long_side * size))
        w, h = max(w, 2), max(h, 2)

        cache = self._cache_path(emin, emax, nmin, nmax, w, h)
        if cache.exists():
            try:
                arr = np.load(cache)
                return arr, emin, emax, nmin, nmax
            except Exception:
                pass

        # WCS 2.0.1 KVP. Both `subset` parameters must be preserved, so we
        # pass a list of tuples (a dict would collapse the duplicate keys).
        params = [
            ("request", "GetCoverage"),
            ("service", "WCS"),
            ("version", "2.0.1"),
            ("coverageId", config.LIDAR_COVERAGE_ID),
            ("format", "image/tiff;application=geotiff"),
            ("subset", f"E({emin:.0f},{emax:.0f})"),
            ("subset", f"N({nmin:.0f},{nmax:.0f})"),
            ("scalesize", f"i({w}),j({h})"),
        ]
        # Stream with a hard size cap: if the server ever ignores the subset
        # and tries to send a full national coverage, we abort and fall back to
        # the 25 m DEM instead of filling the disk.
        content = _get_capped(self.session, config.LIDAR_WCS_URL, params,
                              cap_bytes=32 * 1024 * 1024,
                              timeout=max(config.HTTP_TIMEOUT, 40.0))
        arr3, tw, th = read_tiff(content)
        arr = arr3[0].astype(np.float32)
        if arr.shape != (h, w) and arr.size == h * w:
            arr = arr.reshape(h, w)
        # Mask nodata: the EA sentinel (-FLT_MAX) and 0.0 fill used where the
        # composite has coverage gaps.
        arr[(arr <= -1000.0) | (arr == 0.0)] = np.nan
        np.save(cache, arr)
        return arr, emin, emax, nmin, nmax

    def sample(self, lats, lons, progress_cb=None):
        """Return elevations (or None) for each point. Raises on total failure."""
        lats = np.asarray(lats, dtype=float)
        lons = np.asarray(lons, dtype=float)
        east, north = geo.wgs84_to_osgb36_array(lats, lons)

        in_england = (
            (lats >= config.LIDAR_LAT_MIN) & (lats <= config.LIDAR_LAT_MAX) &
            (lons >= config.LIDAR_LON_MIN) & (lons <= config.LIDAR_LON_MAX)
        )
        e = east[in_england]
        n = north[in_england]
        if len(e) == 0:
            raise RuntimeError("Track is outside EA LIDAR coverage")

        emin, emax = float(e.min()), float(e.max())
        nmin, nmax = float(n.min()), float(n.max())
        pad = config.LIDAR_BBOX_PAD_M
        emin -= pad
        emax += pad
        nmin -= pad
        nmax += pad

        if progress_cb:
            progress_cb(30, "Downloading EA National LIDAR DTM (1 m)...")

        arr, b_emin, b_emax, b_nmin, b_nmax = self._fetch_raster(emin, emax, nmin, nmax)
        h, w = arr.shape

        # Raster row 0 is the northern edge (grid origin is the NW corner).
        cols = (e - b_emin) / (b_emax - b_emin) * (w - 1)
        rows = (b_nmax - n) / (b_nmax - nmin) * (h - 1)
        cols = np.clip(cols, 0, w - 1)
        rows = np.clip(rows, 0, h - 1)

        c0 = np.floor(cols).astype(int)
        r0 = np.floor(rows).astype(int)
        c1 = np.minimum(c0 + 1, w - 1)
        r1 = np.minimum(r0 + 1, h - 1)
        fc = cols - c0
        fr = rows - r0

        top = arr[r0, c0] * (1 - fc) + arr[r0, c1] * fc
        bot = arr[r1, c0] * (1 - fc) + arr[r1, c1] * fc
        vals = top * (1 - fr) + bot * fr

        out = np.full(len(lats), np.nan, dtype=float)
        out[in_england] = vals
        return [(_plausible(v) if math.isfinite(v) else None) for v in out]


# ---------------------------------------------------------------------------
# Tier 2 — OpenTopoData (EU-DEM 25 m / SRTM 90 m)
# ---------------------------------------------------------------------------

# OpenTopoData documents "max 1 call per second" (and 1000 calls/day). Sleep
# just over a second between batches so the DEM fallback never trips the
# limit — a 429 is swallowed by the batch handler, so exceeding it would
# silently punch holes in the ride's elevation.
OPENTOPO_MIN_INTERVAL_S = 1.05


class OpenTopoProvider:
    def __init__(self, dataset=None):
        self.dataset = dataset or config.OPENTOPO_DATASET
        self.session = _session()
        self.cache = self._load_cache()

    @staticmethod
    def _load_cache():
        try:
            return json.loads(config.OPENTOPO_CACHE.read_text())
        except Exception:
            return {}

    def _save_cache(self):
        try:
            config.OPENTOPO_CACHE.write_text(json.dumps(self.cache))
        except Exception:
            pass

    def sample(self, lats, lons, progress_cb=None):
        all_ll = list(zip(lats, lons))
        out = [None] * len(all_ll)
        missing, missing_idx = [], []
        for idx, (lat, lon) in enumerate(all_ll):
            key = f"{lat:.6f},{lon:.6f}:{self.dataset}"
            if key in self.cache and self.cache[key] is not None:
                out[idx] = _plausible(self.cache[key], lat, lon)
            else:
                missing.append((lat, lon))
                missing_idx.append(idx)

        if progress_cb:
            progress_cb(35, f"EU-DEM 25 m: {len(missing)}/{len(all_ll)} points to fetch")

        for cs in range(0, len(missing), 90):
            chunk = missing[cs:cs + 90]
            idx_chunk = missing_idx[cs:cs + 90]
            loc = "|".join(f"{lat:.6f},{lon:.6f}" for lat, lon in chunk)
            try:
                r = self.session.get(
                    f"https://api.opentopodata.org/v1/{self.dataset}",
                    params={"locations": loc},
                    timeout=config.HTTP_TIMEOUT,
                )
                r.raise_for_status()
                results = r.json().get("results") or []
                for j, res in enumerate(results[:len(chunk)]):
                    idx = idx_chunk[j]
                    val = _plausible(res.get("elevation"), chunk[j][0], chunk[j][1])
                    out[idx] = val
                    key = f"{chunk[j][0]:.6f},{chunk[j][1]:.6f}:{self.dataset}"
                    self.cache[key] = val
                self._save_cache()
            except Exception:
                pass
            time.sleep(OPENTOPO_MIN_INTERVAL_S)
        return out


# ---------------------------------------------------------------------------
# Tier 3 — Terrarium global DEM tiles
# ---------------------------------------------------------------------------


class TerrariumProvider:
    def __init__(self):
        self.session = _session()
        self.cache_dir = config.DEM_CACHE / "terrarium"
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _tile(lat, lon, zoom=14):
        n = 2.0 ** zoom
        x = (lon + 180.0) / 360.0 * n
        lat_rad = math.radians(lat)
        y = (1.0 - math.log(math.tan(lat_rad) + 1.0 / math.cos(lat_rad)) / math.pi) / 2.0 * n
        return zoom, int(math.floor(x)), int(math.floor(y)), x - math.floor(x), y - math.floor(y)

    def _tile_elev(self, lat, lon):
        from PIL import Image
        zoom, tx, ty, fx, fy = self._tile(lat, lon)
        tp = self.cache_dir / str(zoom) / str(tx) / f"{ty}.png"
        img = None
        if tp.exists():
            try:
                img = Image.open(tp).convert("RGB")
            except Exception:
                img = None
        if img is None:
            url = f"https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{zoom}/{tx}/{ty}.png"
            try:
                r = self.session.get(url, timeout=config.HTTP_TIMEOUT)
                r.raise_for_status()
                tp.parent.mkdir(parents=True, exist_ok=True)
                tp.write_bytes(r.content)
                img = Image.open(io.BytesIO(r.content)).convert("RGB")
            except Exception:
                return None
        px = max(0, min(255, int(fx * 256)))
        py = max(0, min(255, int(fy * 256)))
        r, g, b = img.getpixel((px, py))[:3]
        return (r * 256.0 + g + b / 256.0) - 32768.0

    def sample(self, lats, lons, progress_cb=None):
        out = []
        for i, (lat, lon) in enumerate(zip(lats, lons)):
            out.append(_plausible(self._tile_elev(lat, lon), lat, lon))
            if progress_cb and i % 200 == 0:
                progress_cb(45, f"Terrarium fallback {i}/{len(lats)}")
        return out


# ---------------------------------------------------------------------------
# Unified entry point
# ---------------------------------------------------------------------------


def sample_elevations(lats, lons, prefer_lidar=True, progress_cb=None):
    """Best-effort elevation for every point, degrading tier by tier.

    Returns (elevations, source) where ``source`` is one of
    'lidar', 'eudem25m', 'terrarium'. ``elevations`` contains None for any
    point no provider could fill.
    """
    lats = list(lats)
    lons = list(lons)
    n = len(lats)
    elevations = [None] * n
    source = None

    if prefer_lidar:
        try:
            provider = LidarProvider()
            vals = provider.sample(lats, lons, progress_cb)
            filled = sum(1 for v in vals if v is not None)
            if filled >= max(1, int(n * 0.5)):
                elevations = vals
                source = "lidar"
        except Exception as exc:
            if progress_cb:
                progress_cb(32, f"LIDAR unavailable ({type(exc).__name__}); using 25 m DEM")

    if source != "lidar":
        # Lidar failed to cover enough of the track: fall back tier by tier.
        try:
            provider = OpenTopoProvider()
            vals = provider.sample(lats, lons, progress_cb)
            for i, v in enumerate(vals):
                if v is not None:
                    elevations[i] = v
            if any(v is not None for v in elevations):
                source = "eudem25m"
        except Exception:
            pass

        remaining = [i for i, v in enumerate(elevations) if v is None]
        if remaining:
            try:
                provider = TerrariumProvider()
                vals = provider.sample(
                    [lats[i] for i in remaining],
                    [lons[i] for i in remaining],
                    progress_cb,
                )
                for i, v in zip(remaining, vals):
                    if v is not None:
                        elevations[i] = v
                if source is None and any(v is not None for v in elevations):
                    source = "terrarium"
            except Exception:
                pass
    # When lidar IS the source, the (few) coverage-gap points are left as
    # None: the elevation stage interpolates them from neighbouring 1 m lidar
    # rather than mixing in a coarse 30 m DEM at the gap edges.

    return elevations, source
