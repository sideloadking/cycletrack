"""Map-match: snap the GPS track onto the road network.

This is the v1 of the plan's "map-match track onto road network" stage. A full
HMM map-matcher is overkill for the goal — keeping lidar sampling on the road
surface rather than in ditches and verges — so we do a projection snap: fetch
OpenStreetMap ``highway`` ways around the track (cached per bounding box), then
snap each point to its nearest segment within a tolerance. Points further away
than the tolerance are left as-is (off-road riding, bad GPS). If the network
is unavailable the track passes through untouched and the pipeline flags it.
"""

import json
import math
import pathlib
import time
from typing import Optional

import numpy as np
import requests

from . import config, geo

# Highway classes we consider "the road network" for a bike.
_HIGHWAY_RE = (
    r"^(motorway|trunk|primary|secondary|tertiary|unclassified|residential|"
    r"service|living_street|track|cycleway|path|bridleway|road)$"
)

_OVERPASS_URL = "https://overpass-api.de/api/interpreter"


def _bbox_key(south, west, north, east):
    return f"{south:.4f}_{west:.4f}_{north:.4f}_{east:.4f}"


def _load_road_network(south, west, north, east):
    """Return a list of (lat1, lon1, lat2, lon2) segments from the cache or
    Overpass. Returns None when the network cannot be fetched."""
    key = _bbox_key(south, west, north, east)
    cache_file = config.ROAD_CACHE / f"{key}.json"
    if cache_file.exists():
        try:
            data = json.loads(cache_file.read_text())
            return _segments_from_json(data)
        except Exception:
            pass

    query = (
        f'[out:json][timeout:60];'
        f'way["highway"~"{_HIGHWAY_RE}"]({south:.5f},{west:.5f},{north:.5f},{east:.5f});'
        f'out body;>;out skel qt;'
    )
    headers = {
        "Accept": "application/json",
        "User-Agent": "CyclingProgressTracker/0.1 (local, private)",
    }
    # Overpass instances can rate-limit or transiently 406; try several and
    # fall back to the next one rather than silently skipping map-match.
    instances = [_OVERPASS_URL, "https://overpass.kumi.systems/api/interpreter"]
    for url in instances:
        for _attempt in range(2):
            try:
                r = requests.post(url, data={"data": query}, headers=headers,
                                  timeout=config.HTTP_TIMEOUT * 3)
                r.raise_for_status()
                data = r.json()
                segments = _segments_from_json(data)
                if not segments:
                    raise ValueError("empty road network")
                config.ROAD_CACHE.mkdir(parents=True, exist_ok=True)
                cache_file.write_text(json.dumps(data))
                return segments
            except Exception:
                time.sleep(0.6)
    return None


def _segments_from_json(data):
    nodes = {}
    ways = []
    for el in data.get("elements", []):
        if el.get("type") == "node":
            nodes[el["id"]] = (el["lat"], el["lon"])
        elif el.get("type") == "way":
            ways.append(el.get("nodes", []))
    segments = []
    for way in ways:
        for i in range(len(way) - 1):
            a = nodes.get(way[i])
            b = nodes.get(way[i + 1])
            if a and b:
                segments.append((a[0], a[1], b[0], b[1]))
    return segments


def _snap_points(lats, lons, segments, max_dist):
    """Vectorised projection of each point onto its nearest segment."""
    lats = np.asarray(lats, dtype=float)
    lons = np.asarray(lons, dtype=float)
    n = len(lats)
    out_lat = lats.copy()
    out_lon = lons.copy()
    snapped = np.zeros(n, dtype=bool)

    if not segments:
        return out_lat, out_lon, snapped

    seg = np.array(segments, dtype=float)  # (m, 4): lat1 lon1 lat2 lon2
    # Local metres around the track centre.
    clat = np.radians(lats.mean())
    cosf = math.cos(clat)
    kx = 111320.0 * cosf
    ky = 111320.0

    ax = seg[:, 1] * kx
    ay = seg[:, 0] * ky
    bx = seg[:, 3] * kx
    by = seg[:, 2] * ky
    abx = bx - ax
    aby = by - ay
    ab2 = abx * abx + aby * aby
    ab2 = np.where(ab2 == 0, 1e-9, ab2)

    for i in range(n):
        px = lons[i] * kx
        py = lats[i] * ky
        t = ((px - ax) * abx + (py - ay) * aby) / ab2
        t = np.clip(t, 0.0, 1.0)
        qx = ax + t * abx
        qy = ay + t * aby
        dx = px - qx
        dy = py - qy
        dist2 = dx * dx + dy * dy
        j = int(np.argmin(dist2))
        if dist2[j] <= max_dist * max_dist:
            out_lat[i] = ay[j] / ky + t[j] * (aby[j] / ky)
            out_lon[i] = ax[j] / kx + t[j] * (abx[j] / kx)
            snapped[i] = True
    return out_lat, out_lon, snapped


def map_match(lats, lons, progress_cb=None):
    """Snap a track to the road network.

    Returns ``(snapped_lats, snapped_lons, info)`` where ``info`` is a dict
    with ``segments`` (road segments fetched) and ``snapped_ratio``.
    """
    lats = np.asarray(lats, dtype=float)
    lons = np.asarray(lons, dtype=float)
    if len(lats) < 2:
        return lats.copy(), lons.copy(), {"segments": 0, "snapped_ratio": 0.0}

    pad = 0.005  # ~500 m
    south, north = float(lats.min() - pad), float(lats.max() + pad)
    west, east = float(lons.min() - pad), float(lons.max() + pad)

    if progress_cb:
        progress_cb(15, "Fetching road network for map-match...")

    segments = _load_road_network(south, west, north, east) or []
    if not segments:
        return lats.copy(), lons.copy(), {"segments": 0, "snapped_ratio": 0.0}

    if progress_cb:
        progress_cb(20, f"Snapping {len(lats)} points to {len(segments)} road segments")

    slats, slons, snapped = _snap_points(
        lats, lons, segments, config.MAPMATCH_MAX_DISTANCE_M
    )
    info = {
        "segments": len(segments),
        "snapped_ratio": float(snapped.mean()) if len(snapped) else 0.0,
    }
    return slats, slons, info
