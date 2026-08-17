# Cycling Progress Tracker — Spec

A local Windows app that imports Wahoo `.fit` files, rebuilds elevation from
UK lidar, estimates power with honest uncertainty bands, and tracks fitness
over time. Private by construction: everything lives on your machine.

---

## 1. Scope

**In scope**
- Bulk-import history of `.fit` files (this is the first thing that must work).
- Automatic altitude repair on every import (lidar, not the old global DEMs).
- Estimated power for every point, with a per-point uncertainty band.
- HR-based load and zones; watts@fixed-HR as the headline fitness trend.
- Personal records, climb stats, and charts.
- One user profile: age, weight, height, bike type, HR zones.

**Out of scope**
- Real measured power (no power meter exists; nothing to read).
- Live/on-bike capture — this is post-ride analysis only.
- Cloud sync, social features, multi-user.
- Trying to extract usable power from windy flats or long coasts. We
  *estimate and flag*, we don't pretend.

---

## 2. Sensors and inputs

Wahoo records: **GPS** (lat/lon, speed, distance) and **heart rate**. No power
meter, no cadence. Everything else is derived:

- Elevation → **lidar DTM** (post-hoc, not the barometer).
- Grade → derivative of smoothed elevation.
- Power → physics model from speed + grade + mass + drag + weather.
- Wind/temp/pressure → weather API at ride time + location.

---

## 3. Pipeline (per imported file)

```
.fit ──▶ parse (records: lat/lon/speed/dist/HR)
     ──▶ map-match track onto road network
     ──▶ sample lidar DTM ──▶ smooth 15–25 m ──▶ grade
     ──▶ fetch weather (time + centroid)
     ──▶ estimate power + uncertainty band per point
     ──▶ compute metrics, zones, load, records
     ──▶ store everything (raw + derived) locally
```

`qwenv.py` becomes the altitude stage of this pipeline, upgraded from
OpenTopoData/Terrarium (~25–30 m global DEMs) to Environment Agency National
LIDAR (1 m, free OGL, DTM). That upgrade is what makes "better than Strava"
true instead of aspirational.

---

## 4. Elevation — the one piece with real engineering risk

Rules that make 1 m lidar *better* rather than just finer:

1. **DTM, not DSM** — bare ground, so we don't sample tree canopies, roofs,
   or bridges.
2. **Map-match first** — GPS wanders 3–10 m; raw sampling hits ditches and
   verges. Snap the track to the road network before sampling.
3. **Smooth over ~15–25 m** — micro-relief (kerbs, bridge decks) isn't what
   the wheel rides; unsmoothed 1 m data is *noisier* than the old 25 m DEM.

This stage is the first prototype milestone, because if map-match + smoothing
don't produce clean grade, every downstream number inherits the error.

---

## 5. Power model and uncertainty

Power is estimated from physics:

```
P = (rolling_resistance + aero_drag + gravity + acceleration) × speed
```

where the gravity term is `mass × g × grade × speed` — which is exactly why
elevation quality matters.

**The honest ceiling:** at high speed, aero drag dominates and you cannot
separate wind from CdA using only speed and grade. On a flat windy ride the
estimate can be off by >100%. No amount of research removes this — it's an
information limit, not an implementation detail.

So every point gets a **power band, not a point estimate**:
- Tight on steep climbs (gravity dominates, drag is small).
- Wide on flats and descents (drag dominates, wind unknown).
- ~0 W while coasting downhill (correct behavior, flagged as "no pedalling
  required" rather than "zero effort").

Every derived metric inherits the band. Charts show the envelope, and anything
computed from power carries a confidence tag.

---

## 6. Calibration — how we shrink the band

Two procedures, done on suitable rides:

1. **Steep-climb test** — on a sustained steep climb, gravity dominates and
   the estimate is near-exact. Used to pin down rolling resistance.
2. **Loop CdA fit** — ride the same loop in two directions and require the
   physics to close the energy budget; the residual gives an effective CdA
   (wind baked in as a per-ride offset).

Calibrated rides get tighter bands; uncalibrated rides get the default wide
assumptions, clearly labelled.

---

## 7. Metrics

Concrete, computable, and each with a confidence flag where it derives from
power.

**Concrete (no estimation):**
- Distance, duration, elevation gain (lidar).
- Time-in-HR-zone, average/max HR, HR-based load (TRIMP).
- Climb stats: VAM, metres climbed, gradient distribution.
- Records: longest ride, biggest climb, best distance/time.

**Estimated (from power, with bands):**
- Power: point bands, average, normalized power.
- W/kg at fixed HRs — **the headline trend** (see §8).
- Best N-minute power curves (with envelope).
- VO2max estimate.
- HR-based fitness/freshness (CTL/ATL/TSB on TRIMP).

No FTP, no real TSS — there's no power meter, so we don't fake those numbers.

---

## 8. Headline trend: watts@fixed-HR

"Am I fitter?" is answered by watts produced at a fixed heart rate over time:
more watts at the same HR = fitter. This is the main chart. It's graded:

- **Confident:** watts@fixed-HR from calibrated climbs — gravity-dominated,
  tight band.
- **Context only:** power@VO2max estimates and HR response on a repeated
  known route (weather-normalized).

Derived numbers are weighted by their confidence, not averaged blindly.

---

## 9. Data model

```
rider          id, age, weight, height, bike_type, hr_zones, ftp(unused)
bike           id, name, mass, crr, cda (calibrated or default)
ride           id, bike_id, started_at, tz, weather_id, elevation_source
gps_point      ride_id, t, lat, lon, elev_raw, elev_fixed, grade
hr_point       ride_id, t, hr
power_point    ride_id, t, watts_lo, watts_est, watts_hi, confidence, mode
calibration    ride_id, type (climb|loop), fitted params, r2
record         ride_id, metric, value
weather        ride_id, temp, wind_speed, wind_dir, pressure, source
```

Storage: local SQLite (fits the single-machine, zero-setup goal; no server).

---

## 10. Architecture

- **Tauri (Rust)** — native window, system tray, autostart, small launcher.
- **Python engine** — FastAPI serving compute on localhost: FIT parser/patcher,
  lidar sampling, map-match, `numpy`/`scipy` power + uncertainty, CdA fitting.
- **Browser UI** — talks to the engine over localhost; Plotly for charts.

The price of this split is two toolchains and a process boundary. It's worth
it because the engine is a data-science stack we'd otherwise have to re-port
into Rust, and the shell still feels native.

---

## 11. Build order

1. **Elevation prototype** — map-match + lidar DTM + smoothing on real `.fit`
   files. Gate: does grade come out clean? (Highest risk, do it first.)
2. **Import + store** — bulk `.fit` ingestion into SQLite, run the altitude
   stage, show ride list and per-ride summary.
3. **Concrete metrics** — distance, time, climb, HR zones, TRIMP, records.
4. **Power engine** — physics model + uncertainty bands, weather fetch.
5. **Calibration** — steep-climb and loop-CdA procedures.
6. **Trends** — watts@fixed-HR headline, power curves, fitness/freshness.
7. **Shell** — Tauri window, tray, autostart, polish.

---

## 12. Risks

| Risk | Mitigation |
|------|-----------|
| Map-match + smoothing don't yield clean grade | Prototype first; fall back to 25 m DEM with heavier smoothing |
| Lidar tiles too large to sample cheaply | Pre-build a local downsampled DTM pyramid, cache per-tile |
| Wind makes flat-ride power meaningless | Never pretend — wide band + confidence flag; steer metrics to climbs |
| Weather API availability/keys | Cache; treat missing weather as "default assumptions" not failure |
| Calibration rides never happen | Ship sensible defaults; calibration is optional tightening |
