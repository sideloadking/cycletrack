#!/usr/bin/env python3
"""
Basingstoke FIT Fixer v7

Fixes massive altitude spikes at pauses / resumes.

Major v7 fixes:
- Binary pre-pass and binary patcher use the same parser, so elevation indexing cannot desync.
- Pause points reuse the last valid moving elevation.
- All altitude fields are overwritten; old 10 km values are never preserved.
- Added FIT altitude fields use correct FIT base types:
    sint16 = 0x83, uint32 = 0x86.
- Original plausible altitude values are used as fallback if web elevation fails.
- Strong clamping/despiking for Basingstoke.

Tune these if used outside Basingstoke:
    MIN_ELEV, MAX_ELEV
"""

import os
import sys
import math
import json
import time
import pathlib
import threading
import io
import struct
import shutil
from typing import Optional

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

try:
    import numpy as np
except ImportError:
    print("pip install numpy")
    sys.exit(1)

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

try:
    from PIL import Image
    HAS_PIL = True
except ImportError:
    HAS_PIL = False


# ----------------------------------------------------------------------------
# User-tunable Basingstoke constraints
# ----------------------------------------------------------------------------

MIN_ELEV = 0.0
MAX_ELEV = 400.0

SPEED_MOVING_THRESHOLD = 0.6      # m/s
DIST_MOVING_THRESHOLD = 1.0       # metres of cumulative distance increase
MAX_JUMP = 20.0                   # max allowed elevation jump between records, metres
SMOOTH_WINDOW = 5

CACHE_ROOT = pathlib.Path.home() / ".basingstoke_lidar_cache"
CACHE_ROOT.mkdir(parents=True, exist_ok=True)

TERRARIUM_CACHE = CACHE_ROOT / "terrarium"
TERRARIUM_CACHE.mkdir(parents=True, exist_ok=True)

OPENTOPO_CACHE = CACHE_ROOT / "opentopo.json"


# ----------------------------------------------------------------------------
# FIT constants
# ----------------------------------------------------------------------------

RECORD_GLOBAL_NUM = 20

FIELD_LAT = 0
FIELD_LON = 1
FIELD_ALT = 2
FIELD_DIST = 5
FIELD_SPEED = 6
FIELD_ENH_SPEED = 73
FIELD_ENH_ALT = 78

ALT_INVALID_SINT16 = 0x7FFF
ALT_INVALID_UINT32 = 0xFFFFFFFF

BASE_TYPE_SINT16 = 0x83
BASE_TYPE_UINT32 = 0x86

SEMICIRCLE_TO_DEG = 180.0 / (2 ** 31)


# ----------------------------------------------------------------------------
# Cache helpers
# ----------------------------------------------------------------------------

def load_opentopo_cache():
    if OPENTOPO_CACHE.exists():
        try:
            return json.loads(OPENTOPO_CACHE.read_text())
        except Exception:
            return {}
    return {}


def save_opentopo_cache(cache):
    try:
        OPENTOPO_CACHE.write_text(json.dumps(cache))
    except Exception:
        pass


OPENTOPO_MEM_CACHE = load_opentopo_cache()


# ----------------------------------------------------------------------------
# Elevation sanity helpers
# ----------------------------------------------------------------------------

def is_uk(lat: float, lon: float) -> bool:
    return 49.0 <= lat <= 60.9 and -8.5 <= lon <= 2.0


def sanitize_provider_elev(elev, lat=None, lon=None) -> Optional[float]:
    """
    Loose provider-level sanity filter.
    Discards obvious corrupted tile/API values like 10 km.
    """
    try:
        elev = float(elev)
    except Exception:
        return None

    if not math.isfinite(elev):
        return None

    # Hard global sanity range.
    if elev < -100.0 or elev > 1000.0:
        return None

    # UK/Basingstoke extra protection.
    if lat is not None and lon is not None and is_uk(lat, lon) and elev > 700.0:
        return None

    return elev


def clamp_elev(elev) -> Optional[float]:
    """
    Final Basingstoke clamp.
    Returns None if unusable, so it can be interpolated.
    """
    try:
        elev = float(elev)
    except Exception:
        return None

    if not math.isfinite(elev):
        return None

    if elev < MIN_ELEV or elev > MAX_ELEV:
        return None

    return elev


# ----------------------------------------------------------------------------
# OpenTopo provider
# ----------------------------------------------------------------------------

class OpenTopoProvider:
    def __init__(self, dataset: str = "eudem25m"):
        if not HAS_REQUESTS:
            raise RuntimeError("requests is required for OpenTopo")
        self.dataset = dataset
        self.session = requests.Session()
        self.cache = OPENTOPO_MEM_CACHE

    def _fetch_chunk(self, chunk):
        loc_str = "|".join([f"{lat:.6f},{lon:.6f}" for lat, lon in chunk])
        url = f"https://api.opentopodata.org/v1/{self.dataset}?locations={loc_str}"

        for attempt in range(3):
            try:
                r = self.session.get(url, timeout=25)

                if r.status_code == 200:
                    data = r.json()
                    results = data.get("results")

                    if isinstance(results, list) and len(results) == len(chunk):
                        vals = []
                        for j, res in enumerate(results):
                            elev = res.get("elevation")
                            vals.append(
                                sanitize_provider_elev(
                                    elev,
                                    chunk[j][0],
                                    chunk[j][1],
                                )
                            )
                        return vals, True

                elif r.status_code == 429:
                    time.sleep(2.0 * (attempt + 1))

                else:
                    time.sleep(1.0 * (attempt + 1))

            except Exception as e:
                print(f"OpenTopo fail: {e}")
                time.sleep(1.0 * (attempt + 1))

        return [None] * len(chunk), False

    def get_elevations(self, lats, lons, progress_cb=None):
        all_ll = list(zip(lats, lons))
        out = [None] * len(all_ll)

        missing = []
        missing_idx = []

        for idx, (lat, lon) in enumerate(all_ll):
            key = f"{lat:.6f},{lon:.6f}:{self.dataset}"

            if key in self.cache:
                cached = self.cache[key]
                out[idx] = sanitize_provider_elev(cached, lat, lon) if cached is not None else None
            else:
                missing.append((lat, lon))
                missing_idx.append(idx)

        total = len(all_ll)

        if progress_cb and total:
            progress_cb(20, f"Auto {len(missing)}/{total} need OpenTopo {self.dataset}")

        for cs in range(0, len(missing), 90):
            chunk = missing[cs:cs + 90]
            idx_chunk = missing_idx[cs:cs + 90]

            vals, ok = self._fetch_chunk(chunk)

            if ok:
                for j, val in enumerate(vals):
                    idx = idx_chunk[j]
                    out[idx] = val

                    lat, lon = chunk[j]
                    key = f"{lat:.6f},{lon:.6f}:{self.dataset}"
                    self.cache[key] = val

                save_opentopo_cache(self.cache)
            else:
                print("OpenTopo chunk failed; leaving gaps for fallback")

            if progress_cb and total:
                done = min(total, cs + len(chunk))
                progress_cb(int(20 + 50 * done / total), f"OpenTopo {done}/{total}")

            time.sleep(1.1)

        return out


# ----------------------------------------------------------------------------
# Terrarium tile provider
# ----------------------------------------------------------------------------

class TerrariumProvider:
    def __init__(self):
        if not HAS_PIL or not HAS_REQUESTS:
            raise RuntimeError("Pillow and requests are required for Terrarium")
        self.session = requests.Session()

    @staticmethod
    def latlon_to_tile(lat: float, lon: float, zoom: int):
        lat_rad = math.radians(lat)
        n = 2.0 ** zoom
        x = (lon + 180.0) / 360.0 * n
        y = (1.0 - math.log(math.tan(lat_rad) + 1.0 / math.cos(lat_rad)) / math.pi) / 2.0 * n
        return x, y

    def get_tile_image(self, z: int, x: int, y: int):
        tp = TERRARIUM_CACHE / str(z) / str(x) / f"{y}.png"

        if tp.exists():
            try:
                img = Image.open(tp)
                if img.size != (256, 256):
                    tp.unlink()
                    raise ValueError("Bad tile size")
                return img.convert("RGB")
            except Exception:
                try:
                    tp.unlink()
                except Exception:
                    pass

        url = f"https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"

        try:
            r = self.session.get(url, timeout=20)
            if r.status_code == 200 and r.headers.get("Content-Type", "").startswith("image"):
                tp.parent.mkdir(parents=True, exist_ok=True)
                tp.write_bytes(r.content)

                img = Image.open(io.BytesIO(r.content))
                if img.size != (256, 256):
                    return None

                return img.convert("RGB")

        except Exception as e:
            print(f"Terrarium tile fail: {e}")

        return None

    @staticmethod
    def decode_elevation(r: int, g: int, b: int) -> float:
        return (r * 256.0 + g + b / 256.0) - 32768.0

    def get_elevation(self, lat: float, lon: float, zoom: int = 14) -> Optional[float]:
        if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
            return None

        x_f, y_f = self.latlon_to_tile(lat, lon, zoom)
        x = int(math.floor(x_f))
        y = int(math.floor(y_f))

        max_tile = 2 ** zoom
        if x < 0 or y < 0 or x >= max_tile or y >= max_tile:
            return None

        img = self.get_tile_image(zoom, x, y)
        if img is None:
            return None

        px = int((x_f - x) * 256)
        py = int((y_f - y) * 256)

        px = max(0, min(255, px))
        py = max(0, min(255, py))

        try:
            r, g, b = img.getpixel((px, py))[:3]
            elev = self.decode_elevation(r, g, b)
            return sanitize_provider_elev(elev, lat, lon)
        except Exception:
            return None

    def get_elevations(self, lats, lons, progress_cb=None):
        out = []
        total = len(lats)

        for i, (lat, lon) in enumerate(zip(lats, lons)):
            out.append(self.get_elevation(lat, lon))

            if progress_cb and total and i % 100 == 0:
                progress_cb(int(70 + 15 * i / total), f"Terrarium {i}/{total}")

        return out


# ----------------------------------------------------------------------------
# FIT binary helpers
# ----------------------------------------------------------------------------

def fit_crc16(data: bytes, crc: int = 0) -> int:
    for b in data:
        crc ^= b
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc & 0xFFFF


def parse_fit_file(input_path):
    file_bytes = pathlib.Path(input_path).read_bytes()

    if len(file_bytes) < 12:
        raise ValueError("File too small to be FIT")

    header_size = file_bytes[0]

    if header_size < 12 or len(file_bytes) < header_size + 2:
        raise ValueError("Bad FIT header size")

    data_size = struct.unpack_from("<I", file_bytes, 4)[0]
    data_section = file_bytes[header_size:header_size + data_size]

    if len(data_section) != data_size:
        raise ValueError("Truncated FIT data section")

    return file_bytes, header_size, data_size, data_section


def invalid_signed(size: int) -> Optional[int]:
    if size <= 0:
        return None
    return (1 << (size * 8 - 1)) - 1


def invalid_unsigned(size: int) -> Optional[int]:
    if size <= 0:
        return None
    return (1 << (size * 8)) - 1


def read_scalar(buf: bytes, offset: int, size: int, arch: int, signed: bool):
    if offset < 0 or size <= 0 or offset + size > len(buf):
        return None

    if size == 1:
        fmt = "b" if signed else "B"
    elif size == 2:
        fmt = "h" if signed else "H"
    elif size == 4:
        fmt = "i" if signed else "I"
    elif size == 8:
        fmt = "q" if signed else "Q"
    else:
        return None

    prefix = "<" if arch == 0 else ">"

    try:
        return struct.unpack_from(prefix + fmt, buf, offset)[0]
    except Exception:
        return None


def parse_definition(data: bytes, pos: int, header: int, total_len: int):
    local_type = header & 0x0F
    has_dev = (header & 0x20) != 0

    if pos + 6 > total_len:
        raise ValueError("Truncated definition message")

    reserved = data[pos + 1]
    arch = data[pos + 2]

    global_num = struct.unpack_from(
        "<H" if arch == 0 else ">H",
        data,
        pos + 3,
    )[0]

    num_fields = data[pos + 5]
    off = pos + 6

    fields_old = []

    for _ in range(num_fields):
        if off + 3 > total_len:
            raise ValueError("Truncated field definition")

        fnum = data[off]
        fsize = data[off + 1]
        ftype = data[off + 2]

        fields_old.append({
            "num": fnum,
            "size": fsize,
            "type": ftype,
        })

        off += 3

    dev_fields = []

    if has_dev:
        if off + 1 > total_len:
            raise ValueError("Truncated developer field count")

        num_dev = data[off]
        off += 1

        for _ in range(num_dev):
            if off + 3 > total_len:
                raise ValueError("Truncated developer field definition")

            fnum = data[off]
            fsize = data[off + 1]
            fidx = data[off + 2]

            dev_fields.append({
                "num": fnum,
                "size": fsize,
                "idx": fidx,
            })

            off += 3

    old_regular_size = sum(f["size"] for f in fields_old)
    old_dev_size = sum(d["size"] for d in dev_fields)
    old_payload_size = old_regular_size + old_dev_size

    field_map_old = {}
    cur = 0

    for f in fields_old:
        f["offset"] = cur
        field_map_old[f["num"]] = f
        cur += f["size"]

    for d in dev_fields:
        d["offset"] = cur
        field_map_old[256 + d["num"]] = d
        cur += d["size"]

    defn = {
        "local_type": local_type,
        "reserved": reserved,
        "arch": arch,
        "global_num": global_num,
        "has_dev": has_dev,
        "fields_old": fields_old,
        "dev_fields": dev_fields,
        "field_map_old": field_map_old,
        "old_regular_size": old_regular_size,
        "old_dev_size": old_dev_size,
        "old_payload_size": old_payload_size,
    }

    return off, defn


def augment_definition(defn: dict) -> dict:
    """
    Add altitude and enhanced altitude fields to record messages if missing.
    Uses correct FIT base types.
    """
    fields_new = [dict(f) for f in defn["fields_old"]]

    if defn["global_num"] == RECORD_GLOBAL_NUM:
        if FIELD_ALT not in defn["field_map_old"]:
            fields_new.append({
                "num": FIELD_ALT,
                "size": 2,
                "type": BASE_TYPE_SINT16,
            })

        if FIELD_ENH_ALT not in defn["field_map_old"]:
            fields_new.append({
                "num": FIELD_ENH_ALT,
                "size": 4,
                "type": BASE_TYPE_UINT32,
            })

    new_regular_size = sum(f["size"] for f in fields_new)
    new_payload_size = new_regular_size + defn["old_dev_size"]

    field_map_new = {}
    cur = 0

    for f in fields_new:
        f["offset"] = cur
        field_map_new[f["num"]] = f
        cur += f["size"]

    for d in defn["dev_fields"]:
        nd = dict(d)
        nd["offset"] = cur
        field_map_new[256 + nd["num"]] = nd
        cur += nd["size"]

    defn = dict(defn)
    defn.update({
        "fields_new": fields_new,
        "field_map_new": field_map_new,
        "new_regular_size": new_regular_size,
        "new_payload_size": new_payload_size,
    })

    return defn


def extract_gps_from_payload(defn: dict, payload: bytes):
    """
    Returns (lat, lon) if this record has valid GPS.
    This exact same function is used by both pre-pass and patch pass,
    preventing elevation index desync.
    """
    fmap = defn["field_map_old"]

    if FIELD_LAT not in fmap or FIELD_LON not in fmap:
        return None

    flat = fmap[FIELD_LAT]
    flon = fmap[FIELD_LON]

    # FIT position fields are sint32 semicircles.
    if flat["size"] != 4 or flon["size"] != 4:
        return None

    lat_raw = read_scalar(payload, flat["offset"], flat["size"], defn["arch"], True)
    lon_raw = read_scalar(payload, flon["offset"], flon["size"], defn["arch"], True)

    if lat_raw is None or lon_raw is None:
        return None

    if lat_raw == invalid_signed(4) or lon_raw == invalid_signed(4):
        return None

    if lat_raw == 0 and lon_raw == 0:
        return None

    lat = lat_raw * SEMICIRCLE_TO_DEG
    lon = lon_raw * SEMICIRCLE_TO_DEG

    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        return None

    return lat, lon


def get_field_raw(defn: dict, payload: bytes, field_num: int, signed: bool):
    f = defn["field_map_old"].get(field_num)
    if not f:
        return None

    val = read_scalar(payload, f["offset"], f["size"], defn["arch"], signed)
    if val is None:
        return None

    inv = invalid_signed(f["size"]) if signed else invalid_unsigned(f["size"])
    if inv is not None and val == inv:
        return None

    return val


def extract_record_info(defn: dict, payload: bytes):
    gps = extract_gps_from_payload(defn, payload)
    if gps is None:
        return None

    lat, lon = gps

    speed = None

    raw = get_field_raw(defn, payload, FIELD_SPEED, False)
    if raw is not None:
        speed = raw / 1000.0
    else:
        raw = get_field_raw(defn, payload, FIELD_ENH_SPEED, False)
        if raw is not None:
            speed = raw / 1000.0

    distance = None
    raw = get_field_raw(defn, payload, FIELD_DIST, False)
    if raw is not None:
        distance = raw / 100.0

    orig_alt = None

    raw = get_field_raw(defn, payload, FIELD_ALT, True)
    if raw is not None:
        orig_alt = raw / 5.0 - 500.0
    else:
        raw = get_field_raw(defn, payload, FIELD_ENH_ALT, False)
        if raw is not None:
            orig_alt = raw / 5.0 - 500.0

    return {
        "lat": lat,
        "lon": lon,
        "speed": speed,
        "dist": distance,
        "orig_alt": orig_alt,
    }


def collect_records_binary(input_path):
    """
    Pre-pass over the FIT binary.
    Returns one entry for every valid-GPS record message.
    This must match the patch pass exactly.
    """
    _, _, _, data_section = parse_fit_file(input_path)

    local_defs = {}
    records = []

    pos = 0
    total = len(data_section)

    while pos < total:
        header = data_section[pos]

        # Compressed timestamp data message
        if header & 0x80:
            local_type = (header >> 5) & 0x03
            defn = local_defs.get(local_type)

            if defn is None:
                raise ValueError("Compressed timestamp message references undefined local definition")

            old_sz = defn["old_payload_size"]
            payload = data_section[pos + 1:pos + 1 + old_sz]

            if len(payload) < old_sz:
                payload += b"\xFF" * (old_sz - len(payload))

            if defn["global_num"] == RECORD_GLOBAL_NUM:
                info = extract_record_info(defn, payload)
                if info is not None:
                    records.append(info)

            pos += 1 + old_sz

        else:
            local_type = header & 0x0F
            is_def = (header & 0x40) != 0

            if is_def:
                off, defn = parse_definition(data_section, pos, header, total)
                local_defs[local_type] = defn
                pos = off

            else:
                defn = local_defs.get(local_type)

                if defn is None:
                    raise ValueError("Data message references undefined local definition")

                old_sz = defn["old_payload_size"]
                payload = data_section[pos + 1:pos + 1 + old_sz]

                if len(payload) < old_sz:
                    payload += b"\xFF" * (old_sz - len(payload))

                if defn["global_num"] == RECORD_GLOBAL_NUM:
                    info = extract_record_info(defn, payload)
                    if info is not None:
                        records.append(info)

                pos += 1 + old_sz

    return records


def build_patched_definition(header: int, defn: dict) -> bytes:
    out = bytearray()

    out.append(header)
    out.append(defn["reserved"])
    out.append(defn["arch"])

    out.extend(struct.pack(
        "<H" if defn["arch"] == 0 else ">H",
        defn["global_num"],
    ))

    out.append(len(defn["fields_new"]))

    for f in defn["fields_new"]:
        out.append(f["num"])
        out.append(f["size"])
        out.append(f["type"])

    if defn["has_dev"]:
        out.append(len(defn["dev_fields"]))

        for d in defn["dev_fields"]:
            out.append(d["num"])
            out.append(d["size"])
            out.append(d["idx"])

    return bytes(out)


def write_altitude_fields(defn: dict, new_payload: bytearray, elevation: Optional[float]):
    """
    Writes both standard altitude and enhanced altitude.
    If elevation is None, writes invalid sentinel values.
    This is critical: never leave old spike bytes in place.
    """
    arch = defn["arch"]
    fmap = defn.get("field_map_new", defn["field_map_old"])

    # Standard altitude: sint16, scale 5, offset 500
    f2 = fmap.get(FIELD_ALT)
    if f2 is not None and f2["size"] == 2:
        fmt = "<h" if arch == 0 else ">h"

        if elevation is None:
            val = ALT_INVALID_SINT16
        else:
            val = int(round((float(elevation) + 500.0) * 5.0))
            val = max(-32768, min(32767, val))

        try:
            struct.pack_into(fmt, new_payload, f2["offset"], val)
        except Exception:
            pass

    # Enhanced altitude: uint32, scale 5, offset 500
    f78 = fmap.get(FIELD_ENH_ALT)
    if f78 is not None and f78["size"] == 4:
        fmt = "<I" if arch == 0 else ">I"

        if elevation is None:
            val = ALT_INVALID_UINT32
        else:
            val = int(round((float(elevation) + 500.0) * 5.0))
            val = max(0, min(ALT_INVALID_UINT32 - 1, val))

        try:
            struct.pack_into(fmt, new_payload, f78["offset"], val)
        except Exception:
            pass


def build_patched_data(header: int, defn: dict, payload: bytes, elevations, elev_idx: int):
    old_sz = defn["old_payload_size"]
    new_sz = defn["new_payload_size"]

    if len(payload) < old_sz:
        payload = payload + b"\xFF" * (old_sz - len(payload))
    elif len(payload) > old_sz:
        payload = payload[:old_sz]

    new_payload = bytearray(new_sz)

    old_regular_size = defn["old_regular_size"]
    old_dev_size = defn["old_dev_size"]
    new_regular_size = defn["new_regular_size"]

    if old_regular_size > 0:
        reg = payload[:old_regular_size]
        if len(reg) < old_regular_size:
            reg += b"\xFF" * (old_regular_size - len(reg))
        new_payload[0:old_regular_size] = reg

    if defn["has_dev"] and old_dev_size > 0:
        dev = payload[old_regular_size:old_regular_size + old_dev_size]

        if len(dev) < old_dev_size:
            dev += b"\xFF" * (old_dev_size - len(dev))
        elif len(dev) > old_dev_size:
            dev = dev[:old_dev_size]

        new_payload[new_regular_size:new_regular_size + old_dev_size] = dev

    patched = 0
    invalid = 0

    if defn["global_num"] == RECORD_GLOBAL_NUM:
        gps = extract_gps_from_payload(defn, payload)

        elevation = None

        if gps is not None:
            if elev_idx < len(elevations):
                elevation = elevations[elev_idx]

            elev_idx += 1
            patched = 1
        else:
            # For no-GPS record messages, write invalid altitude instead of
            # preserving possible old corrupted altitude.
            invalid = 1

        write_altitude_fields(defn, new_payload, elevation)

    msg = bytearray()
    msg.append(header)
    msg.extend(new_payload)

    return bytes(msg), elev_idx, patched, invalid


def patch_fit_binary(input_path, elevations, output_path, progress_cb=None):
    file_bytes, header_size, data_size, data_section = parse_fit_file(input_path)

    local_defs = {}
    new_data = bytearray()

    pos = 0
    total = len(data_section)

    elev_idx = 0
    patched = 0
    invalid_no_gps = 0

    msg_count = 0

    while pos < total:
        header = data_section[pos]

        # Compressed timestamp data message
        if header & 0x80:
            local_type = (header >> 5) & 0x03
            defn = local_defs.get(local_type)

            if defn is None:
                raise ValueError("Compressed timestamp message references undefined local definition")

            old_sz = defn["old_payload_size"]
            payload = data_section[pos + 1:pos + 1 + old_sz]

            msg, elev_idx, p, inv = build_patched_data(
                header,
                defn,
                payload,
                elevations,
                elev_idx,
            )

            new_data.extend(msg)

            patched += p
            invalid_no_gps += inv

            pos += 1 + old_sz

        else:
            local_type = header & 0x0F
            is_def = (header & 0x40) != 0

            if is_def:
                off, defn = parse_definition(data_section, pos, header, total)
                defn = augment_definition(defn)
                local_defs[local_type] = defn

                new_data.extend(build_patched_definition(header, defn))

                pos = off

            else:
                defn = local_defs.get(local_type)

                if defn is None:
                    raise ValueError("Data message references undefined local definition")

                old_sz = defn["old_payload_size"]
                payload = data_section[pos + 1:pos + 1 + old_sz]

                msg, elev_idx, p, inv = build_patched_data(
                    header,
                    defn,
                    payload,
                    elevations,
                    elev_idx,
                )

                new_data.extend(msg)

                patched += p
                invalid_no_gps += inv

                pos += 1 + old_sz

        msg_count += 1

        if progress_cb and msg_count % 5000 == 0:
            pct = 92 + int(6 * pos / max(1, total))
            progress_cb(min(98, pct), f"Patching {pos}/{total}")

    new_header = bytearray(file_bytes[:header_size])

    struct.pack_into("<I", new_header, 4, len(new_data))

    if header_size == 14:
        hcrc = fit_crc16(bytes(new_header[:12]))
        struct.pack_into("<H", new_header, 12, hcrc)

    file_crc = fit_crc16(bytes(new_header) + bytes(new_data))

    final = bytes(new_header) + bytes(new_data) + struct.pack("<H", file_crc)

    pathlib.Path(output_path).write_bytes(final)

    return {
        "patched": patched,
        "invalid_no_gps": invalid_no_gps,
        "consumed": elev_idx,
        "expected_elevations": len(elevations),
        "old_size": len(file_bytes),
        "new_size": len(final),
    }


# ----------------------------------------------------------------------------
# Track processing helpers
# ----------------------------------------------------------------------------

def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371000.0

    p1 = math.radians(lat1)
    p2 = math.radians(lat2)

    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)

    a = math.sin(dp / 2.0) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2.0) ** 2

    return 2.0 * R * math.asin(math.sqrt(a))


def classify_moving(records):
    """
    Classify records as moving or stationary.

    Primary:
      - speed

    Secondary:
      - cumulative distance delta

    Fallback:
      - GPS displacement, but with a conservative threshold to avoid
        treating GPS jitter at traffic lights as moving.
    """
    moving = []

    last_dist = None
    prev_lat = None
    prev_lon = None

    for rec in records:
        speed = rec.get("speed")
        dist = rec.get("dist")

        dist_delta = None

        if dist is not None and last_dist is not None:
            dd = dist - last_dist

            # Ignore negative resets / absurd jumps.
            if 0.0 <= dd < 100000.0:
                dist_delta = dd

        gps_delta = None

        if prev_lat is not None:
            try:
                gps_delta = haversine_m(prev_lat, prev_lon, rec["lat"], rec["lon"])
            except Exception:
                gps_delta = None

        if speed is not None and math.isfinite(speed):
            m = speed >= SPEED_MOVING_THRESHOLD

            # If speed says stationary but distance clearly increased,
            # trust distance. This helps resume points.
            if not m and dist_delta is not None and dist_delta >= DIST_MOVING_THRESHOLD:
                m = True

        elif dist_delta is not None:
            m = dist_delta >= DIST_MOVING_THRESHOLD

        elif gps_delta is not None:
            # Conservative: do not let pause jitter become moving.
            m = gps_delta >= 5.0

        else:
            m = False

        moving.append(m)

        if dist is not None:
            last_dist = dist

        prev_lat = rec["lat"]
        prev_lon = rec["lon"]

    return moving


def interpolate_nan(arr):
    arr = np.asarray(arr, dtype=float)
    n = len(arr)

    if n == 0:
        return arr

    valid = np.isfinite(arr)

    if not valid.any():
        return arr

    if valid.all():
        return arr

    x = np.arange(n)
    arr[~valid] = np.interp(x[~valid], x[valid], arr[valid])

    return arr


def smooth_and_despike_arr(arr, window: int = 5, max_jump: float = 20.0):
    arr = np.asarray(arr, dtype=float)
    n = len(arr)

    if n == 0:
        return arr

    med = arr.copy()

    if window > 1:
        half = window // 2

        for i in range(n):
            s = max(0, i - half)
            e = min(n, i + half + 1)
            med[i] = np.median(arr[s:e])

    # Limit unrealistic second-to-second jumps.
    for i in range(1, n):
        if abs(med[i] - med[i - 1]) > max_jump:
            med[i] = med[i - 1]

    if window > 1:
        sm = med.copy()
        half = window // 2

        for i in range(n):
            s = max(0, i - half)
            e = min(n, i + half + 1)
            sm[i] = np.mean(med[s:e])

        return sm

    return med


# ----------------------------------------------------------------------------
# Main processing
# ----------------------------------------------------------------------------

def process_fit_auto(
    input_fit,
    output_fit,
    source: str = "auto",
    lidar_path=None,
    smooth_window: int = SMOOTH_WINDOW,
    progress_cb=None,
):
    def prog(pct: int, msg: str):
        if progress_cb:
            progress_cb(int(pct), msg)

    prog(0, "Reading FIT records with binary pre-pass...")

    records = collect_records_binary(input_fit)

    if not records:
        raise ValueError("No valid GPS record messages found")

    prog(5, f"Found {len(records)} GPS record points")

    moving_flags = classify_moving(records)
    moving_idx = [i for i, m in enumerate(moving_flags) if m]

    if not moving_idx:
        prog(6, "No moving points detected; using all points as elevation candidates")
        moving_idx = list(range(len(records)))

    prog(7, f"{len(moving_idx)} moving/candidate points")

    moving_lats = [records[i]["lat"] for i in moving_idx]
    moving_lons = [records[i]["lon"] for i in moving_idx]

    moving_elevs = [None] * len(moving_idx)

    use_opentopo = source in ("auto", "eudem25m", "srtm90m")
    use_terrarium = source in ("auto", "terrarium")

    dataset = "srtm90m" if source == "srtm90m" else "eudem25m"

    # 1) OpenTopo / EU-DEM / SRTM
    if use_opentopo:
        try:
            prog(15, f"Downloading {dataset} via OpenTopo...")

            op = OpenTopoProvider(dataset=dataset)

            fetched = op.get_elevations(
                moving_lats,
                moving_lons,
                progress_cb=lambda p, m: prog(max(15, min(70, p)), m),
            )

            for i, e in enumerate(fetched):
                e = clamp_elev(e)
                if e is not None:
                    moving_elevs[i] = e

        except Exception as e:
            print(f"OpenTopo failed: {e}")
            prog(70, f"OpenTopo failed: {e}")

    # 2) Terrarium fallback
    if use_terrarium:
        rem = [i for i, e in enumerate(moving_elevs) if e is None]

        if rem:
            try:
                prog(72, f"Terrarium fallback for {len(rem)} points")

                terr = TerrariumProvider()

                rem_lats = [moving_lats[i] for i in rem]
                rem_lons = [moving_lons[i] for i in rem]

                fetched = terr.get_elevations(
                    rem_lats,
                    rem_lons,
                    progress_cb=lambda p, m: prog(max(72, min(84, p)), m),
                )

                for idx, e in zip(rem, fetched):
                    e = clamp_elev(e)
                    if e is not None:
                        moving_elevs[idx] = e

            except Exception as e:
                print(f"Terrarium failed: {e}")
                prog(84, f"Terrarium failed: {e}")

    # 3) Original altitude fallback, but only plausible values.
    # This is very useful if networking fails, because moving altitude in the
    # original file is often already correct (~150 m), while pause altitude is bad.
    for j, i in enumerate(moving_idx):
        if moving_elevs[j] is None:
            e = clamp_elev(records[i].get("orig_alt"))
            if e is not None:
                moving_elevs[j] = e

    # Interpolate missing moving elevations.
    arr = np.array(
        [np.nan if e is None else float(e) for e in moving_elevs],
        dtype=float,
    )

    if not np.isfinite(arr).any():
        orig_vals = []

        for rec in records:
            e = clamp_elev(rec.get("orig_alt"))
            if e is not None:
                orig_vals.append(e)

        if orig_vals:
            arr[:] = float(np.median(orig_vals))
            prog(85, "Using median of plausible original altitudes as fallback")
        else:
            raise RuntimeError(
                "No usable elevation source. "
                "OpenTopo/Terrarium failed and original altitudes are all implausible."
            )
    else:
        arr = interpolate_nan(arr)

    # Map moving elevations back to all records.
    moving_map = {idx: float(arr[j]) for j, idx in enumerate(moving_idx)}

    full = [None] * len(records)
    last = None

    for i in range(len(records)):
        if i in moving_map:
            last = moving_map[i]

        # Stationary records reuse last valid moving elevation.
        full[i] = last

    full_arr = np.array(
        [np.nan if e is None else float(e) for e in full],
        dtype=float,
    )

    # Leading stationary points before first moving fix get first valid value.
    # Trailing stationary points get last valid value.
    full_arr = interpolate_nan(full_arr)

    if not np.isfinite(full_arr).all():
        raise RuntimeError("Failed to create a complete elevation track.")

    prog(87, "Despiking and smoothing...")

    full_arr = smooth_and_despike_arr(
        full_arr,
        window=int(smooth_window or SMOOTH_WINDOW),
        max_jump=MAX_JUMP,
    )

    full_arr = np.clip(full_arr, MIN_ELEV, MAX_ELEV)

    elevations = [float(v) for v in full_arr]

    prog(92, f"Patching {len(elevations)} GPS records...")

    result = patch_fit_binary(
        input_fit,
        elevations,
        output_fit,
        progress_cb=lambda p, m: prog(max(92, min(99, p)), m),
    )

    prog(
        100,
        f"Done. Patched {result['patched']} GPS records; "
        f"{result['invalid_no_gps']} no-GPS records set invalid."
    )

    return {
        "status": "ok",
        "points": len(records),
        "fixed": result["patched"],
        "invalid_no_gps": result["invalid_no_gps"],
        "old_size": result["old_size"],
        "new_size": result["new_size"],
        "output": str(output_fit),
    }


# ----------------------------------------------------------------------------
# GUI
# ----------------------------------------------------------------------------

class App(tk.Tk):
    def __init__(self):
        super().__init__()

        self.title("Basingstoke FIT Fixer v7")
        self.geometry("900x800")
        self.minsize(800, 700)

        self.input_var = tk.StringVar()
        self.output_var = tk.StringVar()
        self.source_var = tk.StringVar(value="auto")
        self.smooth_var = tk.IntVar(value=SMOOTH_WINDOW)

        self.status = tk.StringVar(value="Ready - v7 fixes pause altitude spikes")

        self.build()

    def build(self):
        pad = {"padx": 12, "pady": 4}

        top = ttk.Frame(self)
        top.pack(fill="x", side="top")

        ttk.Label(
            top,
            text="Basingstoke FIT Fixer v7 - fixes 10 km altitude cliffs",
            font=("Segoe UI", 13, "bold"),
        ).pack(anchor="w", **pad)

        ttk.Label(
            top,
            text=(
                "Rebuilds altitude for moving points, reuses it at pauses, "
                "and overwrites all altitude fields."
            ),
            font=("Segoe UI", 9),
        ).pack(anchor="w", padx=12, pady=(0, 6))

        bottom_bar = ttk.Frame(self, height=62)
        bottom_bar.pack(fill="x", side="bottom", padx=12, pady=10)
        bottom_bar.pack_propagate(False)

        fix_btn = ttk.Button(
            bottom_bar,
            text="FIX MY FIT (v7)",
            command=self.start,
        )
        fix_btn.pack(side="right", padx=5, fill="y", ipadx=18)

        try:
            style = ttk.Style()
            style.configure("Big.TButton", font=("Segoe UI", 11, "bold"))
            fix_btn.configure(style="Big.TButton")
        except Exception:
            pass

        ttk.Button(
            bottom_bar,
            text="Clear cache",
            command=self.clear_cache,
        ).pack(side="left", padx=5)

        ttk.Label(
            bottom_bar,
            textvariable=self.status,
            font=("Segoe UI", 8, "italic"),
            wraplength=420,
        ).pack(side="left", padx=15)

        self.prog = ttk.Progressbar(self, mode="determinate")
        self.prog.pack(fill="x", side="bottom", padx=12, pady=(0, 2))

        middle = ttk.Frame(self)
        middle.pack(fill="both", expand=True, side="top")

        f1 = ttk.Frame(middle)
        f1.pack(fill="x", **pad)

        ttk.Label(f1, text="Input .FIT:", width=14).pack(side="left")
        ttk.Entry(f1, textvariable=self.input_var, width=52).pack(
            side="left",
            padx=5,
            fill="x",
            expand=True,
        )
        ttk.Button(f1, text="Browse", command=self.browse_in).pack(side="left", padx=2)

        f2 = ttk.Frame(middle)
        f2.pack(fill="x", **pad)

        ttk.Label(f2, text="Output .FIT:", width=14).pack(side="left")
        ttk.Entry(f2, textvariable=self.output_var, width=52).pack(
            side="left",
            padx=5,
            fill="x",
            expand=True,
        )
        ttk.Button(f2, text="Save As", command=self.browse_out).pack(side="left", padx=2)

        f3 = ttk.Frame(middle)
        f3.pack(fill="x", **pad)

        ttk.Label(f3, text="Source:", width=14).pack(side="left")

        cb = ttk.Combobox(
            f3,
            textvariable=self.source_var,
            values=["auto", "eudem25m", "srtm90m", "terrarium"],
            width=18,
            state="readonly",
        )
        cb.pack(side="left", padx=5)

        ttk.Label(f3, text="Smooth window:").pack(side="left", padx=(20, 2))

        tk.Spinbox(
            f3,
            from_=1,
            to=15,
            textvariable=self.smooth_var,
            width=4,
        ).pack(side="left")

        log_frame = ttk.LabelFrame(middle, text="Log - v7")
        log_frame.pack(fill="both", expand=True, padx=12, pady=6)

        self.log = tk.Text(log_frame, height=14, font=("Consolas", 8), wrap="word")

        sb = ttk.Scrollbar(log_frame, orient="vertical", command=self.log.yview)
        self.log.configure(yscrollcommand=sb.set)

        self.log.pack(side="left", fill="both", expand=True, padx=(5, 0), pady=5)
        sb.pack(side="right", fill="y", pady=5, padx=(0, 5))

        self.log.insert(
            "end",
            """v7 fixes:
- Binary pre-pass and patcher use the same parser, so elevation indices cannot desync.
- Stationary/pause points reuse last valid moving elevation; no Terrarium fetch at pauses.
- All altitude fields are overwritten. Old 10 km values are never preserved.
- Added altitude fields use correct FIT base types (0x83/0x86).
- Original plausible altitude is used as fallback if web elevation fails.
- Clamp/despike/smooth to Basingstoke range (edit MIN_ELEV/MAX_ELEV if needed).
""",
        )

    def browse_in(self):
        p = filedialog.askopenfilename(
            filetypes=[("FIT", "*.fit"), ("All", "*.*")]
        )

        if p:
            self.input_var.set(p)

            if not self.output_var.get():
                pp = pathlib.Path(p)
                self.output_var.set(str(pp.with_name(pp.stem + "_fixed_v7.fit")))

    def browse_out(self):
        p = filedialog.asksaveasfilename(
            defaultextension=".fit",
            filetypes=[("FIT", "*.fit")],
        )

        if p:
            self.output_var.set(p)

    def clear_cache(self):
        global OPENTOPO_MEM_CACHE

        try:
            root = pathlib.Path.home() / ".basingstoke_lidar_cache"

            if root.exists():
                shutil.rmtree(root)

            root.mkdir(parents=True, exist_ok=True)
            (root / "terrarium").mkdir(parents=True, exist_ok=True)

            OPENTOPO_MEM_CACHE = {}

            self.log_msg("Cache cleared - will re-download tiles")

        except Exception as e:
            messagebox.showerror("Cache", str(e))

    def log_msg(self, msg: str):
        self.log.insert("end", msg + "\n")
        self.log.see("end")
        self.update_idletasks()

    def prog_cb(self, pct: int, msg: str):
        def _u():
            self.prog["value"] = pct
            self.status.set(msg)
            self.log_msg(f"[{pct}%] {msg}")

        self.after(0, _u)

    def start(self):
        inp = self.input_var.get().strip()
        out = self.output_var.get().strip()

        if not inp or not pathlib.Path(inp).exists():
            messagebox.showerror("Error", "Select input FIT")
            return

        if not out:
            messagebox.showerror("Error", "Select output FIT")
            return

        self.prog["value"] = 0
        self.log_msg(f"\n=== v7 START ===\nInput: {inp}")

        def worker():
            try:
                res = process_fit_auto(
                    inp,
                    out,
                    source=self.source_var.get(),
                    smooth_window=self.smooth_var.get(),
                    progress_cb=self.prog_cb,
                )

                self.after(
                    0,
                    lambda r=res: messagebox.showinfo(
                        "Success",
                        (
                            f"Fixed {r['fixed']}/{r['points']} GPS records\n"
                            f"Old {r['old_size']} -> {r['new_size']} bytes\n"
                            f"Saved {r['output']}\n"
                            "No more 10 km cliffs."
                        ),
                    ),
                )

            except Exception as e:
                import traceback
                tb = traceback.format_exc()
                err = str(e)

                self.after(0, lambda: self.log_msg(tb))
                self.after(0, lambda: messagebox.showerror("Failed", err))

        threading.Thread(target=worker, daemon=True).start()


# ----------------------------------------------------------------------------
# Entry point
# ----------------------------------------------------------------------------

if __name__ == "__main__":
    if len(sys.argv) >= 3:
        res = process_fit_auto(
            sys.argv[1],
            sys.argv[2],
            progress_cb=lambda p, m: print(f"{p}% {m}"),
        )
        print(res)
    else:
        app = App()
        app.mainloop()