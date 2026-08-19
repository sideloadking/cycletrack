"""Local SQLite storage.

Implements the PLAN §9 data model. All tables live in a single SQLite file
under the user's home directory — single machine, zero setup, no server.
"""

import json
import sqlite3
import threading

from . import config

_lock = threading.Lock()
_conn = None


def _connect():
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(str(config.DB_PATH), check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("PRAGMA synchronous=NORMAL")
    return _conn


def init_db():
    with _lock:
        conn = _connect()
        conn.executescript(_SCHEMA)
        _migrate(conn)
        conn.commit()


def _migrate(conn):
    """Additive schema migrations for databases created before a feature."""
    ride_cols = {r[1] for r in conn.execute("PRAGMA table_info(ride)")}
    if "route_id" not in ride_cols:
        conn.execute("ALTER TABLE ride ADD COLUMN route_id INTEGER")
    rider_cols = {r[1] for r in conn.execute("PRAGMA table_info(rider)")}
    if "sex" not in rider_cols:
        conn.execute("ALTER TABLE rider ADD COLUMN sex TEXT")


_SCHEMA = """
CREATE TABLE IF NOT EXISTS rider (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    age INTEGER, weight_kg REAL, height_cm REAL, bike_type TEXT,
    sex TEXT,
    resting_hr INTEGER, max_hr INTEGER, hr_zones TEXT,
    updated_at REAL
);

CREATE TABLE IF NOT EXISTS bike (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT, mass_kg REAL, crr REAL, cdA REAL,
    drivetrain_efficiency REAL, calibrated INTEGER DEFAULT 0,
    calibration TEXT
);

CREATE TABLE IF NOT EXISTS ride (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bike_id INTEGER,
    filename TEXT,
    started_at REAL,
    ended_at REAL,
    tz TEXT,
    elevation_source TEXT,
    weather TEXT,
    metrics TEXT,
    distance_m REAL, duration_s REAL, gain_m REAL,
    avg_hr REAL, trimp REAL, avg_watts REAL,
    watts_at_hr TEXT, power_curve TEXT,
    imported_at REAL,
    file_hash TEXT UNIQUE,
    has_hr INTEGER DEFAULT 0,
    bike_calibrated INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS gps_point (
    ride_id INTEGER NOT NULL, t REAL, lat REAL, lon REAL,
    elev_raw REAL, elev REAL, grade REAL, speed REAL, dist REAL, idx INTEGER
);
CREATE INDEX IF NOT EXISTS idx_gps_ride ON gps_point (ride_id);

CREATE TABLE IF NOT EXISTS hr_point (
    ride_id INTEGER NOT NULL, t REAL, hr INTEGER
);
CREATE INDEX IF NOT EXISTS idx_hr_ride ON hr_point (ride_id);

CREATE TABLE IF NOT EXISTS power_point (
    ride_id INTEGER NOT NULL, t REAL,
    watts_lo REAL, watts_est REAL, watts_hi REAL,
    confidence TEXT, mode TEXT
);
CREATE INDEX IF NOT EXISTS idx_power_ride ON power_point (ride_id);

CREATE TABLE IF NOT EXISTS calibration (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ride_id INTEGER NOT NULL, type TEXT, params TEXT, r2 REAL, created_at REAL
);

CREATE TABLE IF NOT EXISTS coast_segment (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ride_id INTEGER NOT NULL,
    t_start REAL NOT NULL,
    t_end REAL NOT NULL,
    label TEXT NOT NULL,
    source TEXT NOT NULL,
    score REAL,
    created_at REAL
);
CREATE INDEX IF NOT EXISTS idx_coast_ride ON coast_segment (ride_id);

CREATE TABLE IF NOT EXISTS record (
    ride_id INTEGER, metric TEXT, value REAL, label TEXT
);

CREATE TABLE IF NOT EXISTS weather (
    ride_id INTEGER PRIMARY KEY, temp REAL, wind_speed REAL,
    wind_dir REAL, pressure REAL, source TEXT
);

CREATE TABLE IF NOT EXISTS route (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ref_ride_id INTEGER,
    name TEXT,
    latlon TEXT,
    bbox TEXT,
    distance_m REAL,
    gain_m REAL,
    created_at REAL
);
"""

# ---------------------------------------------------------------------------
# Profile (rider + bike)
# ---------------------------------------------------------------------------


def get_profile():
    with _lock:
        conn = _connect()
        rider = conn.execute("SELECT * FROM rider WHERE id = 1").fetchone()
        bike = conn.execute("SELECT * FROM bike ORDER BY id LIMIT 1").fetchone()
        return _row_to_dict(rider), _row_to_dict(bike)


def save_profile(rider_data, bike_data):
    with _lock:
        conn = _connect()
        rider = {
            "age": int(rider_data.get("age", 40)),
            "weight_kg": float(rider_data.get("weight_kg", 75.0)),
            "height_cm": float(rider_data.get("height_cm", 178.0)),
            "bike_type": rider_data.get("bike_type", "road"),
            "sex": rider_data.get("sex"),
            "resting_hr": int(rider_data.get("resting_hr", 55) or 55),
            "max_hr": rider_data.get("max_hr"),
            "hr_zones": json.dumps(rider_data.get("hr_zones")),
        }
        conn.execute(
            """INSERT INTO rider (id, age, weight_kg, height_cm, bike_type,
               sex, resting_hr, max_hr, hr_zones, updated_at)
               VALUES (1, :age, :weight_kg, :height_cm, :bike_type, :sex,
               :resting_hr, :max_hr, :hr_zones, :updated_at)
               ON CONFLICT(id) DO UPDATE SET
               age=:age, weight_kg=:weight_kg, height_cm=:height_cm,
               bike_type=:bike_type, sex=:sex, resting_hr=:resting_hr,
               max_hr=:max_hr, hr_zones=:hr_zones, updated_at=:updated_at""",
            {**rider, "updated_at": _now()},
        )
        bike_id = bike_data.get("id") or 1
        conn.execute(
            """INSERT INTO bike (id, name, mass_kg, crr, cdA,
               drivetrain_efficiency, calibrated)
               VALUES (:id, :name, :mass_kg, :crr, :cdA, :eff, :cal)
               ON CONFLICT(id) DO UPDATE SET
               name=:name, mass_kg=:mass_kg, crr=:crr, cdA=:cdA,
               drivetrain_efficiency=:eff, calibrated=:cal""",
            {
                "id": bike_id,
                "name": bike_data.get("name", "Road bike"),
                "mass_kg": float(bike_data.get("mass_kg", 9.0)),
                "crr": float(bike_data.get("crr", 0.005)),
                "cdA": float(bike_data.get("cdA", 0.35)),
                "eff": float(bike_data.get("drivetrain_efficiency", 0.97)),
                "cal": 1 if bike_data.get("calibrated") else 0,
            },
        )
        conn.commit()
    return get_profile()


def _row_to_dict(row):
    if row is None:
        return None
    d = dict(row)
    for key in ("hr_zones", "calibration"):
        if key in d and d[key] and isinstance(d[key], str):
            try:
                d[key] = json.loads(d[key])
            except Exception:
                pass
    return d


# ---------------------------------------------------------------------------
# Rides
# ---------------------------------------------------------------------------


def ride_hash_exists(file_hash):
    with _lock:
        conn = _connect()
        return conn.execute(
            "SELECT 1 FROM ride WHERE file_hash = ?", (file_hash,)
        ).fetchone() is not None


def insert_ride(ride_data):
    """Insert a fully-processed ride plus its point tables. Returns ride id."""
    with _lock:
        conn = _connect()
        cur = conn.execute(
            """INSERT INTO ride (bike_id, filename, started_at, ended_at, tz,
               elevation_source, weather, metrics, distance_m, duration_s,
               gain_m, avg_hr, trimp, avg_watts, watts_at_hr, power_curve,
               imported_at, file_hash, has_hr, bike_calibrated)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                ride_data.get("bike_id") or 1,
                ride_data["filename"],
                ride_data["started_at"],
                ride_data["ended_at"],
                ride_data.get("tz", "UTC"),
                ride_data["elevation_source"],
                json.dumps(ride_data["weather"]),
                json.dumps(ride_data["metrics"]),
                ride_data["metrics"]["distance_m"],
                ride_data["metrics"]["duration_s"],
                ride_data["metrics"]["elevation_gain_m"],
                ride_data["metrics"]["avg_hr"],
                ride_data["metrics"]["trimp"],
                ride_data["metrics"]["avg_watts"],
                json.dumps(ride_data["metrics"]["watts_at_hr"]),
                json.dumps(ride_data["metrics"]["power_curve"]),
                _now(),
                ride_data["file_hash"],
                1 if ride_data["metrics"]["has_hr"] else 0,
                1 if ride_data.get("bike_calibrated") else 0,
            ),
        )
        ride_id = cur.lastrowid

        conn.executemany(
            """INSERT INTO gps_point (ride_id, t, lat, lon, elev_raw, elev, grade, speed, dist, idx)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            [
                (ride_id, r["t"], r["lat"], r["lon"], r.get("elev_raw"),
                 r.get("elev"), r.get("grade"), r.get("speed"), r.get("dist"), i)
                for i, r in enumerate(ride_data["records"])
            ],
        )
        conn.executemany(
            "INSERT INTO hr_point (ride_id, t, hr) VALUES (?,?,?)",
            [
                (ride_id, r["t"], r["hr"])
                for r in ride_data["records"] if r.get("hr") is not None
            ],
        )
        conn.executemany(
            """INSERT INTO power_point
               (ride_id, t, watts_lo, watts_est, watts_hi, confidence, mode)
               VALUES (?,?,?,?,?,?,?)""",
            [
                (ride_id, r["t"], r.get("watts_lo"), r.get("watts_est"),
                 r.get("watts_hi"), r.get("confidence"), r.get("mode"))
                for r in ride_data["records"]
            ],
        )
        conn.execute(
            """INSERT INTO weather (ride_id, temp, wind_speed, wind_dir, pressure, source)
               VALUES (?,?,?,?,?,?)""",
            (
                ride_id,
                ride_data["weather"].get("temp_c"),
                ride_data["weather"].get("wind_speed_mps"),
                ride_data["weather"].get("wind_dir_deg"),
                ride_data["weather"].get("pressure_hpa"),
                ride_data["weather"].get("source"),
            ),
        )
        conn.commit()
    _refresh_records()
    return ride_id


def list_rides():
    with _lock:
        conn = _connect()
        rows = conn.execute(
            """SELECT id, filename, started_at, ended_at, distance_m, duration_s,
               gain_m, avg_hr, trimp, avg_watts, watts_at_hr, metrics,
               elevation_source, has_hr, imported_at, route_id
               FROM ride ORDER BY started_at DESC"""
        ).fetchall()
        return [_summarize(r) for r in rows]


def get_ride(ride_id):
    with _lock:
        conn = _connect()
        row = conn.execute("SELECT * FROM ride WHERE id = ?", (ride_id,)).fetchone()
        if row is None:
            return None
        ride = dict(row)
        ride["metrics"] = json.loads(ride["metrics"] or "{}")
        ride["weather"] = json.loads(ride["weather"] or "{}")
        ride["watts_at_hr"] = json.loads(ride["watts_at_hr"] or "{}")
        ride["power_curve"] = json.loads(ride["power_curve"] or "{}")
        return ride


def get_ride_series(ride_id, downsample=None):
    with _lock:
        conn = _connect()
        gps = conn.execute(
            "SELECT t, lat, lon, elev_raw, elev, grade, speed, dist, idx FROM gps_point WHERE ride_id = ? ORDER BY idx",
            (ride_id,),
        ).fetchall()
        hr = conn.execute(
            "SELECT t, hr FROM hr_point WHERE ride_id = ? ORDER BY t", (ride_id,)
        ).fetchall()
        power = conn.execute(
            """SELECT t, watts_lo, watts_est, watts_hi, confidence, mode
               FROM power_point WHERE ride_id = ? ORDER BY t""",
            (ride_id,),
        ).fetchall()

        gps = [dict(r) for r in gps]
        hr = [dict(r) for r in hr]
        power = [dict(r) for r in power]
        if downsample and len(gps) > downsample:
            step = len(gps) // downsample
            gps = gps[::step]
            power = power[::step]
        return {"gps": gps, "hr": hr, "power": power}


def get_ride_records(ride_id):
    """Rebuild a stored ride's full record list (for recalculation)."""
    with _lock:
        conn = _connect()
        gps = conn.execute(
            "SELECT t, lat, lon, elev_raw, elev, grade, speed, dist "
            "FROM gps_point WHERE ride_id = ? ORDER BY idx", (ride_id,)
        ).fetchall()
        hrs = {
            int(round(r["t"])): r["hr"]
            for r in conn.execute("SELECT t, hr FROM hr_point WHERE ride_id = ?", (ride_id,))
        }
    records = []
    for r in gps:
        rec = dict(r)
        rec["hr"] = hrs.get(int(round(rec["t"])))
        records.append(rec)
    return records


def update_ride_metrics(ride_id, metrics, records):
    """Rewrite a ride's metrics + power points after profile recalculation."""
    with _lock:
        conn = _connect()
        conn.execute(
            """UPDATE ride SET metrics = ?, distance_m = ?, duration_s = ?,
               gain_m = ?, avg_hr = ?, trimp = ?, avg_watts = ?,
               watts_at_hr = ?, power_curve = ?, has_hr = ? WHERE id = ?""",
            (
                json.dumps(metrics),
                metrics["distance_m"], metrics["duration_s"],
                metrics["elevation_gain_m"], metrics["avg_hr"], metrics["trimp"],
                metrics["avg_watts"], json.dumps(metrics["watts_at_hr"]),
                json.dumps(metrics["power_curve"]),
                1 if metrics["has_hr"] else 0, ride_id,
            ),
        )
        conn.execute("DELETE FROM power_point WHERE ride_id = ?", (ride_id,))
        conn.executemany(
            """INSERT INTO power_point
               (ride_id, t, watts_lo, watts_est, watts_hi, confidence, mode)
               VALUES (?,?,?,?,?,?,?)""",
            [
                (ride_id, r["t"], r.get("watts_lo"), r.get("watts_est"),
                 r.get("watts_hi"), r.get("confidence"), r.get("mode"))
                for r in records
            ],
        )
        conn.commit()
    _refresh_records()


def delete_ride(ride_id):
    with _lock:
        conn = _connect()
        for table in ("gps_point", "hr_point", "power_point", "calibration", "coast_segment", "weather", "record"):
            conn.execute(f"DELETE FROM {table} WHERE ride_id = ?", (ride_id,))
        conn.execute("DELETE FROM ride WHERE id = ?", (ride_id,))
        conn.commit()
    _refresh_records()
    recompute_routes()


def _summarize(row):
    d = dict(row)
    d["watts_at_hr"] = json.loads(d["watts_at_hr"] or "{}")
    # Calories live inside the metrics JSON blob; surface the headline so the
    # list rows can show them without shipping the whole blob.
    try:
        m = json.loads(d.pop("metrics", None) or "{}")
        d["calories_kcal"] = (m.get("calories") or {}).get("kcal")
    except Exception:
        d["calories_kcal"] = None
    return d


# ---------------------------------------------------------------------------
# Calibration
# ---------------------------------------------------------------------------

def _is_authoritative(cal_type):
    """Whether a calibration type measures the bike independently (loop,
    pooled) rather than being a diagnostic echo (climb)."""
    return cal_type in ("loop", "pooled")


def get_ride_calibration(ride_id):
    """Most recent *authoritative* calibration params for a ride (dict) or None.

    A ride can record a loop fit, a pooled fit and a climb diagnostic. The
    loop/pooled fits are the authoritative ones — only they measure the bike
    independently and carry ``wind_recovered`` — so a climb must never shadow
    them here (this is used by ``recalculate_rides`` to re-apply the
    recovered wind). Among authoritative fits the most recent wins.
    """
    with _lock:
        conn = _connect()
        row = conn.execute(
            "SELECT params FROM calibration WHERE ride_id = ? "
            "ORDER BY (type = 'loop' OR type = 'pooled') DESC, "
            "created_at DESC, id DESC LIMIT 1",
            (ride_id,),
        ).fetchone()
    if row is None or not row["params"]:
        return None
    try:
        return json.loads(row["params"])
    except Exception:
        return None


def save_calibration(ride_id, calib):
    with _lock:
        conn = _connect()
        conn.execute(
            """INSERT INTO calibration (ride_id, type, params, r2, created_at)
               VALUES (?,?,?,?,?)""",
            (ride_id, calib["type"], json.dumps(calib), calib.get("r2"), _now()),
        )
        if _is_authoritative(calib["type"]):
            # Only loop/pooled fits are an independent measurement of the
            # bike's parameters: they use coasting points whose rider power
            # is zero by construction. The climb procedure is diagnostic — its
            # watts come from the model's assumed Crr, so it cannot measure
            # the true Crr and must not overwrite the profile.
            conn.execute(
                "UPDATE bike SET crr = ?, cdA = ?, calibrated = 1, calibration = ? WHERE id = 1",
                (calib["crr"], calib["cdA"], json.dumps(calib)),
            )
            # The ride whose data produced the calibration is now riding a
            # calibrated bike: tag it so the watts@HR trend can mark its
            # points confident instead of context.
            conn.execute(
                "UPDATE ride SET bike_calibrated = 1 WHERE id = ?", (ride_id,)
            )
        conn.commit()


def list_calibrations():
    with _lock:
        conn = _connect()
        rows = conn.execute(
            """SELECT c.*, r.started_at, r.filename FROM calibration c
               LEFT JOIN ride r ON r.id = c.ride_id ORDER BY c.created_at DESC"""
        ).fetchall()
        out = []
        seen_pooled = set()
        for row in rows:
            d = dict(row)
            d["params"] = json.loads(d["params"] or "{}")
            # A pooled fit is stored as one row per contributing ride (so each
            # ride can re-apply its own recovered wind); collapse the batch to
            # a single history entry.
            if d["type"] == "pooled":
                key = (d["type"],
                       round(d["params"].get("crr") or 0.0, 6),
                       round(d["params"].get("cdA") or 0.0, 6),
                       d["params"].get("n_rides"))
                if key in seen_pooled:
                    continue
                seen_pooled.add(key)
            out.append(d)
        return out


def apply_calibration(ride_id, calib):
    """Re-run stored metrics with calibrated bike? Kept minimal: just save."""
    save_calibration(ride_id, calib)


def get_coast_segments(ride_id):
    """Stored coast/pedal/brake tags for a ride, ordered by start time."""
    with _lock:
        conn = _connect()
        rows = conn.execute(
            "SELECT t_start, t_end, label, source, score "
            "FROM coast_segment WHERE ride_id = ? ORDER BY t_start",
            (ride_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def list_manual_coast_segments():
    """All manual coast/pedal/brake tags across rides (classifier training)."""
    with _lock:
        conn = _connect()
        rows = conn.execute(
            "SELECT ride_id, t_start, t_end, label FROM coast_segment "
            "WHERE source = 'manual' ORDER BY ride_id, t_start",
        ).fetchall()
    return [dict(r) for r in rows]


def save_coast_segments(ride_id, segments):
    """Replace a ride's coast tags with ``segments`` (list of dicts).

    Each segment dict carries ``t_start``, ``t_end``, ``label`` and optional
    ``source`` (default 'auto') and ``score``. Callers that only change one
    tag merge the full list first.
    """
    with _lock:
        conn = _connect()
        conn.execute("DELETE FROM coast_segment WHERE ride_id = ?", (ride_id,))
        conn.executemany(
            """INSERT INTO coast_segment
               (ride_id, t_start, t_end, label, source, score, created_at)
               VALUES (?,?,?,?,?,?,?)""",
            [
                (ride_id, s["t_start"], s["t_end"], s["label"],
                 s.get("source", "auto"), s.get("score"), _now())
                for s in segments
            ],
        )
        conn.commit()


def merge_coast_tag(ride_id, t_start, t_end, label):
    """Atomically add or clear one manual coast tag.

    The read-modify-write (fetch manual tags, drop any overlapping the
    clicked descent, append the new tag or clear) happens under the storage
    lock, so rapid clicks cannot interleave and lose updates: each click
    applies to the latest state instead of a stale snapshot.
    """
    with _lock:
        conn = _connect()
        manual = [dict(r) for r in conn.execute(
            "SELECT t_start, t_end, label, source, score FROM coast_segment "
            "WHERE ride_id = ? AND source = 'manual'", (ride_id,))]
        manual = [s for s in manual
                  if not (abs(s["t_start"] - t_start) < 2.0
                          and abs(s["t_end"] - t_end) < 2.0)]
        if label in ("coast", "pedal", "brake"):
            manual.append({"t_start": t_start, "t_end": t_end, "label": label,
                           "source": "manual", "score": None})
        conn.execute("DELETE FROM coast_segment WHERE ride_id = ?", (ride_id,))
        conn.executemany(
            """INSERT INTO coast_segment
               (ride_id, t_start, t_end, label, source, score, created_at)
               VALUES (?,?,?,?,?,?,?)""",
            [
                (ride_id, s["t_start"], s["t_end"], s["label"],
                 s.get("source", "manual"), s.get("score"), _now())
                for s in manual
            ],
        )
        conn.commit()
    return manual


# ---------------------------------------------------------------------------
# Routes (repeated-route grouping)
# ---------------------------------------------------------------------------


def recompute_routes():
    """Re-group every ride into routes from scratch.

    Cheap enough to run on every import/delete: fingerprints are ~120 points
    and a bounding-box prefilter skips obviously-different routes. Old route
    rows are replaced; ``ride.route_id`` is rewritten.
    """
    from . import routes as routes_mod
    with _lock:
        conn = _connect()
        ride_rows = conn.execute(
            "SELECT id FROM ride ORDER BY started_at").fetchall()
        ride_fps = []
        for row in ride_rows:
            pts = conn.execute(
                "SELECT lat, lon FROM gps_point WHERE ride_id = ? ORDER BY idx",
                (row["id"],),
            ).fetchall()
            if len(pts) < 2:
                continue
            fp = routes_mod.fingerprint(
                [p["lat"] for p in pts], [p["lon"] for p in pts]
            )
            ride_fps.append((row["id"], fp))

        groups = routes_mod.group_rides(ride_fps)
        conn.execute("DELETE FROM route")
        conn.execute("UPDATE ride SET route_id = NULL")
        for g in groups:
            ref = g["ref_ride_id"]
            ref_gain = conn.execute(
                "SELECT gain_m FROM ride WHERE id = ?", (ref,)
            ).fetchone()
            cur = conn.execute(
                """INSERT INTO route (ref_ride_id, name, latlon, bbox,
                   distance_m, gain_m, created_at)
                   VALUES (?,?,?,?,?,?,?)""",
                (
                    ref,
                    _route_name(conn, ref),
                    json.dumps(g["ref_fp"][0]),
                    json.dumps(g["bbox"]),
                    g["length_m"],
                    ref_gain["gain_m"] if ref_gain else None,
                    _now(),
                ),
            )
            route_id = cur.lastrowid
            for rid in g["ride_ids"]:
                conn.execute(
                    "UPDATE ride SET route_id = ? WHERE id = ?", (route_id, rid)
                )
        conn.commit()
    return len(groups)


def _route_name(conn, ride_id):
    row = conn.execute(
        "SELECT started_at FROM ride WHERE id = ?", (ride_id,)
    ).fetchone()
    if row is None:
        return "Route"
    import time
    return time.strftime("Route from %d %b %Y", time.localtime(row["started_at"]))


def list_routes():
    with _lock:
        conn = _connect()
        rows = conn.execute(
            """SELECT r.id, r.name, r.ref_ride_id, r.distance_m, r.created_at,
                      ref.gain_m,
                      COUNT(x.id) AS n_rides,
                      MIN(x.started_at) AS first_at,
                      MAX(x.started_at) AS last_at,
                      SUM(x.distance_m) AS total_distance_m
               FROM route r
               JOIN ride ref ON ref.id = r.ref_ride_id
               JOIN ride x ON x.route_id = r.id
               GROUP BY r.id ORDER BY first_at"""
        ).fetchall()
        return [dict(r) for r in rows]


def get_route(route_id):
    with _lock:
        conn = _connect()
        row = conn.execute("SELECT * FROM route WHERE id = ?", (route_id,)).fetchone()
        if row is None:
            return None
        d = dict(row)
        d["latlon"] = json.loads(d["latlon"] or "[]")
        d["bbox"] = json.loads(d["bbox"] or "[]")
        rides = conn.execute(
            """SELECT id, filename, started_at, ended_at, distance_m, duration_s,
               gain_m, avg_hr, trimp, avg_watts, watts_at_hr, metrics, has_hr
               FROM ride WHERE route_id = ? ORDER BY started_at""",
            (route_id,),
        ).fetchall()
        d["rides"] = [_summarize(r) for r in rides]
        for i, ride in enumerate(d["rides"]):
            ride["route_n"] = i + 1
        d["n_rides"] = len(d["rides"])
        # Climbing per ride, from the reference ride when not stored on the row.
        if d.get("gain_m") is None and d["rides"]:
            d["gain_m"] = d["rides"][0].get("gain_m")
        return d


def route_for_ride(ride_id):
    """Route membership summary for a single ride, or None when ungrouped."""
    with _lock:
        conn = _connect()
        row = conn.execute(
            """SELECT r.id, r.name FROM route r
               JOIN ride x ON x.route_id = r.id WHERE x.id = ?""",
            (ride_id,),
        ).fetchone()
        if row is None:
            return None
        d = dict(row)
        rides = conn.execute(
            "SELECT id FROM ride WHERE route_id = ? ORDER BY started_at", (d["id"],)
        ).fetchall()
        ids = [r["id"] for r in rides]
        d["size"] = len(ids)
        d["position"] = ids.index(ride_id) + 1
        return d


# ---------------------------------------------------------------------------
# Personal records
# ---------------------------------------------------------------------------

_RECORD_METRICS = [
    ("distance_m", "longest_ride", "Longest ride"),
    ("elevation_gain_m", "biggest_climb", "Most climbing"),
    ("avg_speed_mps", "fastest_avg", "Fastest average"),
    ("max_speed_mps", "max_speed", "Top speed"),
    ("avg_watts", "best_avg_power", "Best average power"),
    ("vo2max", "vo2max", "VO2max estimate"),
]


def _refresh_records():
    with _lock:
        conn = _connect()
        conn.execute("DELETE FROM record")
        rows = conn.execute(
            "SELECT id, metrics, distance_m, gain_m, avg_watts FROM ride"
        ).fetchall()
        best = {}
        for row in rows:
            m = json.loads(row["metrics"] or "{}")
            values = {
                "distance_m": m.get("distance_m", row["distance_m"]),
                "elevation_gain_m": m.get("elevation_gain_m", row["gain_m"]),
                "avg_speed_mps": m.get("avg_speed_mps"),
                "max_speed_mps": m.get("max_speed_mps"),
                "avg_watts": m.get("avg_watts", row["avg_watts"]),
                "vo2max": m.get("vo2max"),
            }
            for key, val in values.items():
                if val is None or val <= 0:
                    continue
                cur = best.get(key)
                if cur is None or val > cur["value"]:
                    best[key] = {"ride_id": row["id"], "value": val}

        for key, (label_key, label) in [
            ("distance_m", ("distance_m", "Longest ride")),
            ("elevation_gain_m", ("elevation_gain_m", "Most climbing")),
            ("avg_speed_mps", ("avg_speed_mps", "Fastest average")),
            ("max_speed_mps", ("max_speed_mps", "Top speed")),
            ("avg_watts", ("avg_watts", "Best average power")),
            ("vo2max", ("vo2max", "VO2max estimate")),
        ]:
            if key in best:
                conn.execute(
                    "INSERT INTO record (ride_id, metric, value, label) VALUES (?,?,?,?)",
                    (best[key]["ride_id"], key, best[key]["value"], label),
                )
        conn.commit()


def get_records():
    with _lock:
        conn = _connect()
        rows = conn.execute(
            """SELECT rec.*, r.started_at, r.filename FROM record rec
               LEFT JOIN ride r ON r.id = rec.ride_id"""
        ).fetchall()
        out = []
        for row in rows:
            d = dict(row)
            d["value_display"] = _format_record(d["metric"], d["value"])
            out.append(d)
        return out


def _format_record(metric, value):
    if metric == "distance_m":
        return f"{value / 1000:.2f} km"
    if metric in ("avg_speed_mps", "max_speed_mps"):
        return f"{value * 3.6:.1f} km/h"
    if metric in ("gain_m", "elevation_gain_m"):
        return f"{value:.0f} m"
    if metric == "avg_watts":
        return f"{value:.0f} W"
    if metric == "vo2max":
        return f"{value:.1f} ml/kg/min"
    return f"{value:.1f}"


def _now():
    import time
    return time.time()
