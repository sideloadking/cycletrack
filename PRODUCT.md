# Product

<!-- uizze:product-schema 1 -->

## Platform

web

## Users

One person: the owner, a cyclist who rides with a Wahoo head unit (no power meter, no cadence) and analyses rides after the fact on a local Windows machine. The tool is built for this single user, but carries a flagship-quality bar — it should feel as crafted as a top-tier commercial product even though it is private.

## Product Purpose

A private, local Windows app that imports Wahoo `.fit` files, rebuilds elevation from UK lidar, estimates power with honest uncertainty bands, and tracks fitness over time. Success is answering two questions: "Am I fitter?" (watts produced at a fixed heart rate over time) and "Am I actually getting faster on this loop?" (side-by-side comparison of repeated rides on the same roads). Nothing is uploaded — the database, caches, and UI all live on the machine.

## Positioning

The mechanism a neighbouring product (e.g. Strava) could not truthfully copy: elevation rebuilt from 1 m UK lidar — map-matched to the road, bare-ground DTM rather than DSM, smoothed over ~20 m so kerbs and bridge decks don't become climbs — instead of the device barometer or a 25–30 m global DEM, and power estimated with honest per-point uncertainty bands. No FTP and no TSS, because there is no power meter and those numbers would be faked. Privacy by construction is a product stance, not a footnote.

## Operating Context

- Runs on Windows: `python main.py` serves the UI at `http://127.0.0.1:8347` and opens the browser; an optional Tauri shell exists in `tauri/`.
- Post-ride analysis only — no live/on-bike capture.
- Every imported `.fit` runs the full pipeline: parse → map-match → lidar elevation → weather → power + uncertainty → metrics → route grouping.
- One rider profile (age, weight, height, bike type, HR zones) and one bike, optionally calibrated by a loop-CdA fit (climb results are recorded for visibility only).
- Charts use a lightweight, purpose-built SVG renderer with shared axes, uncertainty bands, hover cards, keyboard seeking, and linked replay cursors; the ride map is Leaflet with OSM/CARTO tiles; if tiles are unreachable the route line still draws.

## Capabilities and Constraints

Confirmed capabilities: bulk `.fit` import; elevation from EA National LIDAR (England) with EU-DEM 25 m and Terrarium fallbacks; per-point power estimate with an uncertainty band and confidence tag (tight on climbs, wide on flats/descents, coasting flagged as "no pedalling required"); HR zones and Banister TRIMP load; CTL/ATL/TSB fitness/freshness; best-N-minute power curves with envelope; VO2max estimate; VAM; gradient distribution; personal records; cardiac-drift measure; interactive ride replay (timeline scrubber drives a map marker and linked chart cursors); automatic route grouping with side-by-side ride comparison (including reversed loops); profile and bike settings; a loop-CdA calibration that tightens the bands over time, with climb results recorded as diagnostics.

Confirmed constraints: no power meter (no measured power, no cadence); no cloud sync, social features, or multi-user; lidar coverage is England only (other regions degrade to the 25 m DEM); timestamps are the device's local wall-clock; flat windy rides are genuinely uncertain and are flagged wide rather than pretended precise. The feature set is essentially complete — nothing major is planned or left undecided.

## Brand Commitments

The name "Cycling Progress Tracker" is in use, but the owner has stated no visual constraint is binding — the entire design may be replaced ("absolutely everything design wise can go"). The honest, no-pretend voice (uncertainty bands, no fake FTP/TSS) is product truth, not a visual commitment.

## Evidence on Hand

Real implementation, no placeholder content: `README.md` and `PLAN.md` (full spec), `DESIGN.md` (Route Atlas visual system), `web/` (browser UI: `index.html`, `style.css`, `app.js`), `cycling/` (Python engine: parser, geo, lidar, map-match, elevation, weather, power, metrics, storage, pipeline, server), `tauri/` (optional native shell), and `tests/`. The browser UI uses a shared Route Atlas shell, a consolidated overview read path, a lightweight SVG graph system with linked replay cursors, Leaflet replay, and responsive page templates.

## Product Principles

1. Honesty over precision — every estimate carries a band and a confidence tag; never fake a number the sensors cannot produce (no FTP, no TSS).
2. Privacy by construction — everything lives on this machine; nothing is uploaded.
3. Elevation is the differentiator — map-matched, smoothed 1 m lidar DTM beats barometer and global DEM; it is the core mechanism, not a garnish.
4. Confidence-weighted insight — derived metrics are weighted by confidence, never averaged blindly.
5. Flagship craft for one user — the interface should earn the same quality bar as a commercial product, not a personal-tool minimum.
