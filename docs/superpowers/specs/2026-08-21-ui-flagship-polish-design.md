# VeloTrack UI Flagship Polish — Audit & Design Plan

Date: 2026-08-21
Scope: `web/` (index.html, style.css, app.js) with zero-to-minimal backend support.
Mandate: "polish the web UI into an ultimate flagship product … comprehensive audit, written plan, then use that plan." Documentation may be outdated; creativity granted (PRODUCT.md: "absolutely everything design wise can go").

---

## 1. Where the product stands

VeloTrack is a single-rider, local-first cycling analysis studio. The current shell is a
light "Route Atlas" system (daylight canvas #f4f7f4, green anchor #1b6f4d, IBM Plex +
Archivo) shipped in 2c3f2b4 and simplified in cf8a412 / fb8555b. The bones are good:
purpose-built SVG graph engine, linked replay cursors, honest uncertainty bands,
keyboard/ARIA discipline, a deterministic Playwright regression suite.

The audit found that cf8a412's simplification pass **regressed features and dropped
design-system assets**, and that several visual details are below flagship bar.

## 2. Audit findings

### 2a. Defects (must fix)

| # | Finding | Evidence |
|---|---------|----------|
| D1 | Search field icon overlaps its placeholder on Rides — `.search-field input { padding-left: 42px }` (style.css:183) is overridden by the later shorthand `padding: 9px 12px` in the input group rule (style.css:186). | Screenshot: placeholder starts under the magnifier. |
| D2 | Selector typo `button:active` (missing dot) at style.css:96 applies the press transform to every `<button>` instead of `.button`. | Source inspection. |
| D3 | Calorie feature lost in the cf8a412 rewrite. Backend computes `metrics.calories` (kcal + lo/hi + method note + HR/power cross-checks; metrics.py:477-632) and exposes `calories_kcal` on ride summaries (storage.py:421), but no page renders it. Commit 358da49 originally shipped this UI. | git show 358da49 vs live app. |
| D4 | Watts@HR trend draws a vertical "hook" with curl when two rides share a day: duplicate x values + Catmull-Rom overshoot (app.js graphCurvedPath). | Overview screenshot Aug 14–15. |
| D5 | Ride map route renders as broken dark dashes: permanent per-descent highlight polylines (drawDescentHighlights) overpaint the green route in mixed colors. | Ride detail screenshot. |
| D6 | Recent power curves legend shows duplicate labels ("Aug 14, 2026" twice). | Overview screenshot. |
| D7 | Gradient distribution x labels are arbitrary sparse indices (-20% … 18% with dead space); category tick step ignores bucket semantics. | Ride detail screenshot. |
| D8 | `--subtle: #65736b` drifted from DESIGN.md's AA-checked `#5c6b63`; 11px utility text sits at ~4.4:1 on canvas. | style.css:12 vs DESIGN.md. |
| D9 | DESIGN.md tokens absent from code: canvas-deep, blue-deep/soft, canvas-glow, map-badge-border, slider-thumb-shadow, skeleton-shimmer, tooltip-*, plate-glass, shadow-tint — CSS and JS can't share them. | Diff DESIGN.md front-matter vs :root. |

### 2b. Gaps versus the documented design language ("Route Atlas")

- G1 The **route ribbon** — the declared signature surface (DESIGN.md) — does not exist.
  Routes/Rides pages are text-only cards; route identity is invisible until you open replay.
- G2 No sidebar footer; the "local & private" product stance is nowhere in the chrome
  (DESIGN.md: "Do not hide local/private status in a settings page").
- G3 Page enters have no motion; DESIGN.md specifies "a short, low-distance rise".
- G4 Records page is six bare cards with a void beneath — no award motif, no context.
- G5 Import page under-explains the pipeline (the lidar→physics story is the differentiator)
  and the dropzone is small; queue items don't communicate stages.
- G6 Ride detail hero ignores weather, calories, VAM, min/max elevation, route link — all
  already present in `/api/rides/{id}`.
- G7 Power-curve chart tooltips/legends lack ride-number disambiguation for same-day rides.

### 2c. Constraints — regression suite contracts (tests/browser_regression.py)

Must preserve exactly: `h1` == "Overview" and single `h1` on ride page; `#period-title`
text "Last 30 days"; `#watts-hr-chart svg`, `#fitness-chart .graph-line` ×3;
`.primary-chart`; `.import-layout` centered `.import-card`, `#dropzone`, `#queue-card`,
`.job--done` containing filename; class `.import-guide` must NOT exist; `#replay-play`
aria-pressed toggling; `.detail-hero` contains "42.5 km" and "151 W – 281 W";
`#ride-map.leaflet-container`; `#ch-elev/#ch-hr/#ch-power .graph-line`;
`details.descents` closed by default; `#descents-count` "N to review · M total";
descent rows "To review"/"Freewheeled" + 4 equal-width tag buttons;
`[data-retry-overview]` retry flow with "… unavailable" copy.

---

## 3. Design direction — "Route Atlas II: Instrument finish"

Keep the daylight Route Atlas identity (it is distinctive and right for a data-trust
product) and execute it to flagship depth. Four moves:

1. **The ribbon becomes real.** Every route card, the route-detail hero, and the ride
   hero carry an SVG trace of the actual GPS shape (green line + soft under-stroke +
   start/finish nodes), fetched from each route's `ref_ride_id` series endpoint and
   cached in-session. This turns lists into an atlas without adding map weight.
2. **Instrument honesty.** Uncertainty stays loud but stops shouting: softer hatch
   (thinner strokes, lighter fill), band-aware y-domains so the estimate line never
   drowns, straight segments through duplicate-x clusters, deduped curve legends.
3. **Restored substance.** Calories return (hero Energy metric with band + method note,
   Rides column), weather and route context join the ride hero, the sidebar gets a
   local/private foot.
4. **Quiet motion.** One 320ms page-enter rise, toast slide-in, existing graph entrance;
   everything gated by prefers-reduced-motion (already global).

Visual system deltas: add the missing tokens (D9), adopt `#5c6b63` subtle (D8),
hairline-divided metric plates stay, records gain an award treatment (tinted mark,
value emphasis, context line), import gains a four-step pipeline strip, zones gain
zone-color chips, focus ring stays blue.

## 4. Work breakdown

**Phase A — Foundations & defects (style.css, index.html)**
Fix D1, D2, D8; add D9 tokens + derived utilities; page-enter/toast keyframes;
sidebar-foot styles; ribbon styles; record/import/zone/weather additions; bump asset
versions. Add needed icons to the sprite (check, clock, flame, wind, pin, chevron-down,
shield).

**Phase B — Graph engine corrections (app.js)**
Duplicate-x guard → straight segments + slight marker offset (D4); band-aware robust
domains for watts@HR & power trends; softer band pattern constants; power-curve legend
dedupe with ride ordinals (D6); gradient-distribution boundary labels (D7).

**Phase C — Pages**
- Ride detail: Energy metric (+band+method note), weather chip row, route link chip,
  cleaner map (descents highlight only while their section is open, softer casing) (D5, G6).
- Rides: filename subtitle + calories column; search fix visible (D1, D3).
- Routes: ribbon cards via ref-ride series cache (G1).
- Route detail: ribbon in hero.
- Records: award-card redesign (G4).
- Import: taller dropzone + pipeline explainer + privacy line (G5).
- Overview: refined records rows, summary strip polish, watts@HR domain fix.
- Profile: zone chips, save-row placement fix, calibration note polish.
- Shell: sidebar foot "Local · Private · v" (G2); page-enter motion (G3); toast icon/slide.

**Phase D — Verification**
1. `python tests/browser_regression.py` → 5/5 PASS.
2. Playwright screenshot pass (desktop 1440×900 + mobile 390×844 emulation) of all
   routes incl. ride detail/replay; stored under dogfood-output/screenshots/polish/.
3. Visual re-review of every capture against this plan; iterate until nothing on the
   defect/gap list remains observable.
4. Keyboard pass: skip link, nav, chart seeking, descent tags still operable.

**Phase E — Documentation**
Update DESIGN.md token block to match shipped code (single source of truth), README
screenshot note if needed, commit in logical phases.

## 5. Acceptance criteria

- All D1–D9 fixed and verifiable in captures or code.
- G1–G7 implemented.
- Regression suite 5/5; no new console errors on any page.
- Mobile 390px: no horizontal overflow; nav, summary, tables, replay usable.
- Docs updated so code is the source of truth.
