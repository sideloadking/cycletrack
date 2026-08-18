"""Minimal, dependency-free FIT binary parser.

Extracts the fields the engine cares about (GPS, speed, distance, heart rate,
altitude, timestamp) plus session/file metadata. Modelled on the well-tested
parser in the original ``qwenv.py``, extended with timestamps (including FIT's
compressed-timestamp message scheme) and heart rate.
"""

import pathlib
import struct
from typing import Optional

from .config import FIT_EPOCH_UNIX

# Global message numbers.
FILE_ID_GLOBAL = 0
SPORT_GLOBAL = 12
SESSION_GLOBAL = 18
LAP_GLOBAL = 19
RECORD_GLOBAL = 20

# Record fields.
FIELD_LAT = 0
FIELD_LON = 1
FIELD_ALT = 2
FIELD_HR = 3
FIELD_CADENCE = 4
FIELD_DIST = 5
FIELD_SPEED = 6
FIELD_GRADE = 9
FIELD_ENH_SPEED = 73
FIELD_ENH_ALT = 78
FIELD_TIMESTAMP = 253

# Session fields.
SESSION_START_TIME = 2
SESSION_SPORT = 5
SESSION_TOTAL_ELAPSED = 7
SESSION_TOTAL_TIMER = 8
SESSION_TOTAL_DIST = 9
SESSION_AVG_SPEED = 14
SESSION_MAX_SPEED = 15
SESSION_AVG_HR = 16
SESSION_MAX_HR = 17
SESSION_TOTAL_ASCENT = 22
SESSION_TOTAL_DESCENT = 23

SEMICIRCLE_TO_DEG = 180.0 / (2 ** 31)


def _invalid_signed(size):
    if size <= 0:
        return None
    return (1 << (size * 8 - 1)) - 1


def _invalid_unsigned(size):
    if size <= 0:
        return None
    return (1 << (size * 8)) - 1


def read_scalar(buf, offset, size, arch, signed):
    if offset is None or size is None or size <= 0 or offset + size > len(buf):
        return None
    fmt = {1: "b" if signed else "B", 2: "h" if signed else "H",
           4: "i" if signed else "I", 8: "q" if signed else "Q"}.get(size)
    if fmt is None:
        return None
    try:
        return struct.unpack_from(("<" if arch == 0 else ">") + fmt, buf, offset)[0]
    except Exception:
        return None


def _parse_definition(data, pos, header, total_len):
    has_dev = (header & 0x20) != 0
    if pos + 6 > total_len:
        raise ValueError("Truncated definition message")
    arch = data[pos + 2]
    global_num = struct.unpack_from("<H" if arch == 0 else ">H", data, pos + 3)[0]
    num_fields = data[pos + 5]
    off = pos + 6
    fields = []
    for _ in range(num_fields):
        if off + 3 > total_len:
            raise ValueError("Truncated field definition")
        fields.append({"num": data[off], "size": data[off + 1], "type": data[off + 2]})
        off += 3
    dev_sizes = 0
    if has_dev:
        if off + 1 > total_len:
            raise ValueError("Truncated developer field count")
        num_dev = data[off]
        off += 1
        for _ in range(num_dev):
            if off + 3 > total_len:
                raise ValueError("Truncated developer field")
            dev_sizes += data[off + 1]
            off += 3
    field_map = {}
    cur = 0
    for f in fields:
        f["offset"] = cur
        field_map[f["num"]] = f
        cur += f["size"]
    # Developer fields also occupy payload bytes; they must be included in the
    # record size or the message stream desynchronises.
    return off, {
        "arch": arch,
        "global_num": global_num,
        "field_map": field_map,
        "payload_size": cur + dev_sizes,
    }


def _get_field(defn, payload, num, signed):
    f = defn["field_map"].get(num)
    if not f:
        return None
    val = read_scalar(payload, f["offset"], f["size"], defn["arch"], signed)
    if val is None:
        return None
    inv = _invalid_signed(f["size"]) if signed else _invalid_unsigned(f["size"])
    if inv is not None and val == inv:
        return None
    return val


def _latlon(defn, payload):
    flat = defn["field_map"].get(FIELD_LAT)
    flon = defn["field_map"].get(FIELD_LON)
    if not flat or not flon or flat["size"] != 4 or flon["size"] != 4:
        return None
    lat_raw = read_scalar(payload, flat["offset"], 4, defn["arch"], True)
    lon_raw = read_scalar(payload, flon["offset"], 4, defn["arch"], True)
    if lat_raw is None or lon_raw is None:
        return None
    if lat_raw == _invalid_signed(4) or lon_raw == _invalid_signed(4):
        return None
    if lat_raw == 0 and lon_raw == 0:
        return None
    lat = lat_raw * SEMICIRCLE_TO_DEG
    lon = lon_raw * SEMICIRCLE_TO_DEG
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        return None
    return lat, lon


def _decode_record(defn, payload, timestamp):
    ll = _latlon(defn, payload)
    if ll is None:
        return None
    lat, lon = ll

    speed = None
    raw = _get_field(defn, payload, FIELD_SPEED, False)
    if raw is not None:
        speed = raw / 1000.0
    else:
        raw = _get_field(defn, payload, FIELD_ENH_SPEED, False)
        if raw is not None:
            speed = raw / 1000.0

    dist = None
    raw = _get_field(defn, payload, FIELD_DIST, False)
    if raw is not None:
        dist = raw / 100.0

    alt = None
    raw = _get_field(defn, payload, FIELD_ALT, True)
    if raw is not None:
        alt = raw / 5.0 - 500.0
    else:
        raw = _get_field(defn, payload, FIELD_ENH_ALT, False)
        if raw is not None:
            alt = raw / 5.0 - 500.0

    hr = None
    raw = _get_field(defn, payload, FIELD_HR, False)
    if raw is not None:
        hr = raw

    cadence = None
    raw = _get_field(defn, payload, FIELD_CADENCE, False)
    if raw is not None:
        cadence = raw

    grade = None
    raw = _get_field(defn, payload, FIELD_GRADE, True)
    if raw is not None:
        grade = raw / 100.0

    return {
        "t": timestamp,
        "lat": lat,
        "lon": lon,
        "speed": speed,
        "dist": dist,
        "alt_raw": alt,
        "hr": hr,
        "cadence": cadence,
        "grade_device": grade,
    }


def parse_fit(path):
    """Parse a .fit file into records + metadata.

    Returns ``(records, meta)``. ``records`` is a list of dicts with keys
    t/lat/lon/speed/dist/alt_raw/hr/cadence/grade_device (``t`` is a Unix
    timestamp in seconds). ``meta`` carries session/file-level info.
    """
    data = pathlib.Path(path).read_bytes()
    if len(data) < 12:
        raise ValueError("File too small to be a FIT file")
    header_size = data[0]
    if header_size < 12:
        raise ValueError("Bad FIT header")
    data_size = struct.unpack_from("<I", data, 4)[0]
    section = data[header_size:header_size + data_size]

    local_defs = {}
    records = []
    meta = {}
    base_ts = None

    pos = 0
    total = len(section)
    while pos < total:
        header = section[pos]

        if header & 0x80:
            # Compressed-timestamp data message.
            local_type = (header >> 5) & 0x03
            offset_bits = header & 0x1F
            defn = local_defs.get(local_type)
            if defn is None:
                raise ValueError("Compressed message references undefined local definition")
            payload_pos = pos + 1
            if offset_bits == 0x1F:
                if payload_pos >= total:
                    break
                offset_bits = section[payload_pos]
                payload_pos += 1
            ts = (base_ts or 0) + offset_bits
            # Compressed-timestamp messages omit the timestamp field from the
            # payload, so its bytes must not be counted in the message size or
            # the stream desynchronises on the next message.
            ts_field = defn["field_map"].get(FIELD_TIMESTAMP)
            sz = defn["payload_size"] - (ts_field["size"] if ts_field else 0)
            payload = section[payload_pos:payload_pos + sz]
            if defn["global_num"] == RECORD_GLOBAL:
                # ``ts`` is in FIT-epoch seconds (``base_ts`` stays FIT-epoch
                # for later compressed offsets); convert to Unix for records.
                rec = _decode_record(defn, payload, ts + FIT_EPOCH_UNIX)
                if rec is not None:
                    records.append(rec)
            base_ts = ts
            pos = payload_pos + sz
            continue

        local_type = header & 0x0F
        is_def = (header & 0x40) != 0

        if is_def:
            off, defn = _parse_definition(section, pos, header, total)
            local_defs[local_type] = defn
            pos = off
            continue

        defn = local_defs.get(local_type)
        if defn is None:
            raise ValueError("Data message references undefined local definition")
        sz = defn["payload_size"]
        payload = section[pos + 1:pos + 1 + sz]
        pos += 1 + sz

        gn = defn["global_num"]
        ts_field = _get_field(defn, payload, FIELD_TIMESTAMP, False)
        if ts_field is not None:
            ts = ts_field + FIT_EPOCH_UNIX
            base_ts = ts_field  # FIT-epoch seconds, used as compressed base
        else:
            ts = None

        if gn == RECORD_GLOBAL and ts is not None:
            rec = _decode_record(defn, payload, ts)
            if rec is not None:
                records.append(rec)
        elif gn == SESSION_GLOBAL:
            _read_session(meta, defn, payload)
        elif gn == FILE_ID_GLOBAL:
            _read_file_id(meta, defn, payload)
        elif gn == SPORT_GLOBAL:
            _read_sport(meta, defn, payload)

    records.sort(key=lambda r: r["t"])
    _finalize_meta(meta, records)
    return records, meta


def _read_session(meta, defn, payload):
    start = _get_field(defn, payload, SESSION_START_TIME, False)
    if start is not None:
        meta["start_time_unix"] = start + FIT_EPOCH_UNIX
    for key, num, scale in (
        ("total_distance", SESSION_TOTAL_DIST, 100.0),
        ("total_elapsed", SESSION_TOTAL_ELAPSED, 1000.0),
        ("total_timer", SESSION_TOTAL_TIMER, 1000.0),
        ("avg_speed", SESSION_AVG_SPEED, 1000.0),
        ("max_speed", SESSION_MAX_SPEED, 1000.0),
        ("total_ascent", SESSION_TOTAL_ASCENT, 1.0),
        ("total_descent", SESSION_TOTAL_DESCENT, 1.0),
    ):
        v = _get_field(defn, payload, num, False)
        if v is not None:
            meta[key] = v / scale
    for key, num in (("avg_hr", SESSION_AVG_HR), ("max_hr", SESSION_MAX_HR)):
        v = _get_field(defn, payload, num, False)
        if v is not None:
            meta[key] = v
    v = _get_field(defn, payload, SESSION_SPORT, False)
    if v is not None:
        meta["sport"] = v


def _read_file_id(meta, defn, payload):
    for key, num in (("type", 0), ("manufacturer", 1), ("product", 2), ("serial", 3)):
        v = _get_field(defn, payload, num, False)
        if v is not None:
            meta[key] = v
    t = _get_field(defn, payload, 4, False)
    if t is not None:
        meta["time_created_unix"] = t + FIT_EPOCH_UNIX


def _read_sport(meta, defn, payload):
    v = _get_field(defn, payload, 0, False)
    if v is not None:
        meta["sport"] = v


def _finalize_meta(meta, records):
    if records:
        if "start_time_unix" not in meta:
            meta["start_time_unix"] = records[0]["t"]
        meta["end_time_unix"] = records[-1]["t"]
        # The device's timer (total_timer) is the *moving* time — auto-pauses
        # are excluded — so it is the ride's real duration. Record timestamps
        # span wall-clock time (pauses included), so only fall back to that
        # span when the device reports no timer (missing or zero).
        elapsed = max(0.0, records[-1]["t"] - records[0]["t"])
        moving = meta.get("total_timer")
        meta["duration_seconds"] = float(moving) if (moving and moving > 0) else elapsed
        meta["point_count"] = len(records)
        if "total_distance" not in meta:
            meta["total_distance"] = records[-1].get("dist") or 0.0
