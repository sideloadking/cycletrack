# Cycling Progress Tracker

A private, local Windows app that imports Wahoo `.fit` files, rebuilds
elevation from UK lidar, estimates power with honest uncertainty bands, and
tracks fitness over time. Nothing is uploaded: the database, cache and UI all
live on your machine. See [PLAN.md](PLAN.md) for the full spec this implements.

## Run it

```sh
pip install -r requirements.txt
python main.py
```

On Windows you can also double-click **`run.bat`** (console) or **`run.pyw`**
(windowless). The engine serves the UI at <http://127.0.0.1:8347> and opens it
in your browser automatically. There is also an optional native Tauri shell in
[`tauri/`](tauri/README.md).

## What it does per imported file

```
.fit ──▶ parse (GPS / speed / distance / HR)
     ──▶ map-match track onto the road network (OpenStreetMap, cached)
     ──▶ sample DTM ──▶ smooth ~20 m ──▶ grade
     ──▶ fetch weather (Open-Meteo archive, cached)
     ──▶ estimate power + per-point uncertainty band
     ──▶ compute metrics, zones, load, records, cardiac drift
     ──▶ group the ride with any repeats of the same route
     ──▶ store everything locally in SQLite
```

### Elevation

Three tiers, best first:

1. **EA National LIDAR** composite DTM (1 m, England, OGL) via the public WCS,
   queried in OSGB36 using a built-in projection, downsampled per track and
   cached on disk. This is the "better than Strava" stage — bare-ground DTM,
   map-matched to the road first, then smoothed so kerbs and bridge decks don't
   become climbs.
2. **EU-DEM 25 m** (OpenTopoData) — the reliable fallback.
3. **Terrarium** tiles + the device's own altitude as a last resort.

If the lidar WCS is slow or out of coverage the import degrades to tier 2
automatically and records which source was used (shown on every ride and trend).

### Power — and why there are bands, not numbers

The bike has no power meter, so power is physics:

```
P = (rolling + aero + gravity + acceleration) × speed
```

Wind cannot be separated from CdA using speed + grade alone, so on flat ground
the estimate is genuinely uncertain. Every point therefore carries a band
(`watts_lo`/`watts_est`/`watts_hi`) and a confidence tag:

- **tight** on steep climbs (gravity dominates),
- **wide** on flats/descents (drag dominates, wind unknown),
- **~0 W while coasting downhill**, flagged "coast" rather than "zero effort".

Two calibration procedures tighten the bands over time: a **steep-climb** test
(pins rolling resistance) and a **loop CdA fit** (closes the energy budget on
coasting descents). Fits that land outside physically-sane ranges are rejected
so a brake-heavy descent never corrupts the bike profile.

### Headline trend

"Am I fitter?" is answered by **watts produced at a fixed heart rate** over
time — more watts at the same HR means fitter. Points are tagged *confident*
(calibrated bike) or *context*, and never averaged blindly.

Also included: HR zones + Banister TRIMP load, CTL/ATL/TSB fitness/freshness,
best-N-minute power curves (with envelope) and their trend over time, a VO2max
estimate, VAM, gradient distribution, personal records, and a **cardiac-drift**
measure (HR rise during steady estimated effort — only reported when a long,
steady window actually exists). No FTP and no TSS — there is no power meter,
so those numbers are not faked.

### Interactive ride replay

Every ride has a **map with a scrubber**. Drag the timeline (or click the map
or any chart, or hit play) and a marker moves along the route while a cursor
highlights the same instant on the elevation, grade, heart-rate, speed and
power charts, with a live readout of all values at that point. Tiles come from
OpenStreetMap/CARTO; if tiles are unreachable the route line still draws.

### Routes — repeated-ride comparison

Rides are grouped into **routes** automatically (GPS-tolerant fingerprinting,
including loops ridden in reverse). The Routes page compares every ride on
the same roads side-by-side — time, average power, watts@fixed-HR, TRIMP and
the weather that day — so "am I actually getting faster on this loop?" can be
answered without comparing different terrain.

## Architecture

```
cycling/           Python engine
  fit_parser.py    dependency-free FIT binary parser
  geo.py           WGS84→OSGB36, distance, grade
  lidar.py         EA LIDAR WCS + DEM fallbacks
  tiffread.py      minimal TIFF decoder (no GDAL/rasterio)
  mapmatch.py      snap-to-road (Overpass, cached)
  elevation.py     map-match → sample → smooth → grade
  weather.py       Open-Meteo archive, cached
  power.py         physics model + uncertainty + calibration
  metrics.py       zones, TRIMP, power curves, W@HR, VO2max
  storage.py       SQLite (data model from PLAN §9)
  pipeline.py      per-file import orchestration
  server.py        FastAPI on localhost + static UI
web/               browser UI (vanilla JS + vendored Plotly)
tauri/             optional native shell (Rust, tray, autostart)
main.py            entry point
```

## Environment knobs

| Variable | Effect |
|----------|--------|
| `CYCLING_DATA_ROOT` | where the DB + caches live (default `~/.cycling_tracker`) |
| `CYCLING_PREFER_LIDAR=0` | skip lidar and go straight to the 25 m DEM |

## Honest limitations

- **No measured power.** Everything power-derived is an estimate with a band.
- **Flat, windy rides.** The model flags these wide rather than pretending.
- **Timestamps** are treated as the device's local wall-clock time (Wahoo
  stores them this way); weather lookups use the ride's location timezone.
- **Lidar coverage** is England only; other regions fall back to EU-DEM.
- The **Tauri shell** is provided as source and requires the Rust toolchain to
  build; the Python engine + web UI are the fully tested core.
