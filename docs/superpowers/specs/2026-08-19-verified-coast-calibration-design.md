# Verified-Coast Calibration — Implementation Plan

Date: 2026-08-19. Status: draft. Goal: replace the heart-rate *guess* for
"was I coasting?" with user-verified tags plus a caution-first learned
classifier, then pool every trusted coasting descent across rides into a
shared Crr/CdA fit with a per-ride wind. As a by-product, pedalled descents — currently thrown away —
are recovered into the power estimate.

## 0. Problem summary

- `find_coast_segments` infers coasting from `grade < -1%`, speed > 2 m/s and
  HR < 78% max. A rider who always pedals downhill never produces a coast
  segment, and a soft-pedalled descent can masquerade as a coast.
- `calibrate_loop` is single-ride: it needs one ride whose descents cover
  enough headings. It fits one wind vector for that ride.
- `compute_power` sets every point below −1% grade to `mode="coast"` and
  reports 0 W, so genuinely pedalled descents are discarded from power and
  from every `mode == "pedal"` aggregate.

## 1. Goals and non-goals

**Goals**

1. Let the owner tag each descent run on the ride replay as **coast**,
   **pedal**, or **brake**, persisted per ride.
2. A learned classifier that auto-labels descents but **errs toward asking**:
   it only auto-labels when confident, and always asks when unsure or when
   the descent looks unlike anything seen before.
3. Pool trusted coast segments across rides into one shared Crr/CdA fit with
   a per-ride wind.
4. Recover pedalled-descent power instead of zeroing it.
5. Keep the honesty contract: estimates keep bands; acceptance gates keep
   bad fits out; no fake precision.

**Non-goals**

- No live/on-bike capture. The app stays post-ride analysis.
- No assumption of a cadence or power sensor.
- The climb check stays diagnostic-only (never applied).
- No change to route grouping, watts@HR, or the existing single-ride loop
  fit unless it is deliberate and tested.

## 2. Pipeline

```
records
  └─ find_descent_segments(records)        # geometry only: grade < -1%, speed > 2 m/s
       └─ classify(segment)                # {coast|pedal|ask, P(coast)}
       └─ user tags (coast|pedal|brake)    # authoritative when present
            └─ effective coasts             # manual coast OR (auto coast AND confident)
                 ├─ calibrate_loop         # existing single-ride path (fallback)
                 └─ calibrate_pooled       # new: shared Crr/CdA + per-ride wind
                      └─ bike.calibration  # applied, tightens bands
  └─ compute_power                         # "pedal" tag ⇒ mode="pedal", not 0 W
```

Key refactor: split today's `find_coast_segments` into two concerns —

- `find_descent_segments(records)` — **candidate detection, geometry only.**
  This is what the tagging UI and scoring operate on. It must *not* filter on
  HR, otherwise pedalled descents (high HR) never appear as candidates at all.
- `classify_coast(records, segments, overrides)` — decides which candidates
  are effective coasts, honouring manual tags first, then the caution-first
  classifier (§4). `find_coast_segments` becomes a thin wrapper over these
  two for the existing auto path.

## 3. Data model

New table (added in `storage._migrate`, idempotent like the existing `sex`
column migration):

```sql
CREATE TABLE IF NOT EXISTS coast_segment (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ride_id INTEGER NOT NULL,
    t_start REAL NOT NULL,          -- seconds since ride start (gps_point.t space)
    t_end REAL NOT NULL,
    label TEXT NOT NULL,            -- 'coast' | 'pedal' | 'brake'
    source TEXT NOT NULL,           -- 'manual' | 'auto'
    score REAL,                     -- coast likelihood 0..1 (auto rows)
    created_at REAL
);
CREATE INDEX IF NOT EXISTS idx_coast_ride ON coast_segment (ride_id);
```

- Manual tags are **segment ranges**, not per-point flags: one row per
  descent run. The replay UI can still trim endpoints by scrubbing.
- Auto rows store the score so the UI can render "confident" vs "ask".
- Labels map to a per-point override injected into records as
  `coast_label` during calibration/recalculation:
  `coast` → include as zero-power; `pedal`/`brake` → exclude from the fit.

## 4. Classification — a caution-first learned classifier

The classifier answers one question per descent: **is this a genuine
freewheel?** It returns `P(coast | features)` and a disposition, and it is
explicitly biased toward asking.

**Features** (all already available in `gps_point`/`hr_point`):

- mean HR ÷ max_hr (lower ⇒ coast),
- HR trend bpm/min over the run (falling ⇒ coast),
- speed-acceleration variance after `_smoothed_speed` (pedalling adds stroke
  jitter; coasting is smooth),
- fraction of points with HR < 0.78·max_hr (the existing signal).

**Model.** Logistic regression over those features, trained on the owner's
manual tags (coast = 1; pedal and brake = 0), fitted with scipy
(`scipy.optimize.minimize` on the negative log-likelihood — numpy/scipy
only, no new dependency) and a small L2 penalty for stability. Cold start
(no tags yet): a hand-tuned prior — the same features weighted to the
current HR<0.78 heuristic — is the initial model.

**Caution-asymmetric decision rule.** A silent mislabel costs far more than
asking:

| actual → predicted | coast | pedal | ask |
|---|---|---|---|
| coast | 0 | lose calibration data + invent power on a freewheel | tiny ε |
| pedal | corrupt the fit + drop real effort | 0 | tiny ε |
| brake | corrupt the fit | invent power | tiny ε |

Auto-label only when genuinely confident, otherwise **ask**:

- `P(coast) ≥ 0.9` ⇒ auto **coast** (may feed the fit),
- `P(coast) ≤ 0.1` ⇒ auto **pedal** (power recovery only, never the fit),
- otherwise ⇒ **ask**.

Three safeguards stop the classifier from learning itself into blindness on
a one-sided history (e.g. every tag is "pedal"):

1. **Skeptical prior floor** — the "coast" class keeps a small prior weight
   even when no coast has ever been labelled, so `P(coast)` cannot collapse
   to zero from a one-sided sample.
2. **Novelty check (out-of-distribution)** — if a descent's feature vector is
   far from *every* labelled example (Mahalanobis distance / max-margin
   outlier), it is **always asked**, regardless of the model's confidence.
   This is the case that matters: the first genuine freewheel after a long
   run of pedalled descents.
3. **Ask floor** — the model may never shrink the "ask" band below a small
   minimum on descents that are at all coast-plausible, so behaviour changes
   still surface.

**Retraining.** The model refits after every manual tag (cheap: tens of
segments). A corrected tag refits too. Decision thresholds and the prior
floor are tuned against the eval harness (§10 F1/F6), not by hand.

## 5. Tagging UI (ride replay)

Surface: the existing replay in `setupReplay` (`web/app.js`).

1. Draw candidate descents as Leaflet polylines under the route, coloured by
   label (green=coast, orange=pedal, red=brake, grey=unlabelled).
2. Add a tag bar next to the scrubber: **Coast / Pedalled / Braked / Clear**.
   It acts on the descent under the current playhead (`state.replayIndex`).
3. A compact list under the map: "3 descents — 1 coast, 1 pedal, 1 to
   review", each row clickable to seek.
4. Tag changes call the endpoint, then re-run calibration + power for that
   ride (mirror the existing `/api/calibrate/{ride_id}` path).

Unlabelled + low-score descents are the "ask" set and are visually distinct.

## 6. Pooled fit (the big payoff)

New `power.calibrate_pooled(segments_by_ride, rider, bike, weather_by_ride)`:

- `segments_by_ride` = effective coast points grouped by ride.
- **Shared unknowns:** Crr, CdA (the bike's constants).
- **Per-ride unknowns:** wind speed, wind direction (nuisance parameters).
- **Two-stage solve** (keeps the dimension under control):
  1. outer search over (Crr, CdA);
  2. for each candidate, per-ride wind is a cheap 2-parameter minimize using
     that ride's coast points — seed it with the archived Open-Meteo wind,
     refine with Nelder-Mead. Inner least squares still uses `_irls_solve`.
- **Identifiability gate** mirrors `_fit_loop_wind`: pooled coast headings
  must span the circle with no gap > 180°, and require ≥ 2 rides and ≥ 3
  segments total (revisit thresholds with the eval harness).
- **Acceptance gate** mirrors `_acceptable_loop` (r², Crr/CdA physical
  ranges, finite covariance), plus "enough points per ride".
- **Fallback:** when the pool is insufficient, keep the existing
  single-ride `calibrate_loop`.

Apply path: `save_calibration` currently only applies `type == "loop"` to the
bike. Generalise to an `_is_authoritative(type)` helper (`loop` and
`pooled`), and make `get_ride_calibration` prefer any authoritative type, not
literally `type = 'loop'`.

## 7. Pedalled-descent power recovery

- `compute_power` replaces the blanket `coast = grade < DOWNHILL_GRADE` with:
  coast unless a stored `pedal` label covers this point.
- Pedalled-descent points get normal `mode="pedal"` and `p_leg =
  max(0, p_wheel/eff)` (gravity term is negative, so the net is the pedalling
  effort — already handled).
- Downstream is automatic: `metrics.py` already selects `mode == "pedal"` for
  averages/NP/best-N/VO2max/watts@HR.
- Wind is the remaining uncertainty. Uncalibrated rides keep the wide wind
  band; once a pooled fit recovers a ride's effective wind, those descents
  tighten like the rest of the ride.
- **Honesty note (UI copy):** a pedalled descent still cannot internally
  separate pedalling from braking; the label is the owner's assertion, so it
  is shown as a user-tagged signal, never styled as a measured sensor value.

## 8. Engine plumbing

- `find_coast_segments(records, overrides)` — accept per-point `coast_label`
  overrides; effective coasts = manual coast OR (untagged AND auto-score
  accepted). Never include manual `pedal`/`brake`.
- Import path (`pipeline`): after power/metrics, run
  `find_descent_segments` + scoring, persist auto `coast_segment` rows, then
  run the pooled fit (or single-ride fallback) exactly where
  `try_auto_calibrate` runs today.
- `recalculate_rides`: reload `coast_segment` rows and re-inject overrides so
  a profile change cannot silently drop tags.
- `/api/calibrate/{ride_id}`: honour stored tags when re-running.

## 9. API surface

- `GET /api/rides/{id}/descents` → candidate runs with `{t_start, t_end,
  label, source, score, n_points}`.
- `POST /api/rides/{id}/coast_segments` → upsert one label `{t_start,
  t_end, label}` (source=manual), then recalculate that ride.
- `POST /api/calibrate/pooled` → run the pooled fit across all rides and
  apply if accepted (also triggered automatically after a tag save).
- Extend `GET /api/calibrations` to show pooled entries and their `n_rides`.

## 10. Testing / eval

Extend `tests/physics_eval.py` (synthetic rides with known Crr/CdA/wind):

- **F1 scoring:** auto-score labels synthetic coasts (low, falling HR,
  smooth speed) vs pedalled (high HR, jittery speed) correctly.
- **F2 pooled recovery:** `calibrate_pooled` recovers injected Crr/CdA within
  tolerance across ≥ 2 synthetic rides with *different* winds; per-ride wind
  recovered within tolerance.
- **F3 pedal override:** a manual `pedal` label on a synthetic descent yields
  nonzero watts and `mode="pedal"`; `mode="coast"` still yields ~0 W.
- **F4 identifiability:** pooled fit with single-direction coverage is
  rejected (returns None / fallback).
- **F5 real ride:** the repo `.fit` file produces candidate descents; manual
  tags round-trip through storage and recalculation.
- **F6 caution:** under a one-sided label history (all "pedal"), the
  classifier must keep asking on coast-plausible and novel descents rather
  than collapsing to "always pedal"; a later genuine coast must be asked or
  caught, never silently mislabelled.

Keep `tests/verify_backend.py` green as the regression gate. Record results
in a scoreboard alongside `physics-eval-results.md`.

## 11. Phasing (dependency-ordered)

**Phase 0 — schema + detection split + classifier skeleton (S).**
`coast_segment` table; `find_descent_segments` / `classify_coast`; feature
extraction; hand-tuned prior as cold start with the caution decision rule;
`GET /api/rides/{id}/descents`. No UI, no fit changes. Gate: F1, F5, F6,
existing suite.

**Phase 1 — tagging UI + feed single-ride fit (M).**
Replay highlight + tag bar + list; `POST /api/rides/{id}/coast_segments`;
overrides in `find_coast_segments`; recalc on tag save. Ships the owner's
core ask. Gate: F3, F5, manual smoke on a real ride.

**Phase 2 — pooled cross-ride fit (L).**
`calibrate_pooled`, authoritative-type handling, apply path, Profile button,
`/api/calibrate/pooled`. Gate: F2, F4.

**Phase 3 — pedalled-descent power recovery (S–M).**
`compute_power` honours `pedal` labels; verify `metrics.py` aggregates and
calorie/HR cross-checks still behave. Gate: F3 (extended), full eval.

**Phase 4 — train and deploy the learned classifier (M).**
Swap the hand-tuned prior for the logistic model trained on manual tags, with
the asymmetric decision rule and the three safeguards (§4). Refit after every
tag; "ask" is the default wherever confidence is low or the segment is
novel. Gate: F1 and F6 — it must beat the prior without learning itself into
blindness.

## 12. Risks / mitigations

- **Memory-based labels are imperfect** → labels are soft (a tagged coast
  can still be downweighted by IRLS); allow re-tagging; acceptance gates
  remain the last line of defence.
- **Pooled wind dimension grows with ride count** → two-stage solve with the
  weather archive as a per-ride prior; cap the pool to the N most informative
  rides if needed.
- **Recovering pedalled-descent power changes stored metrics** → recalc is
  deterministic and already exists; show the change on the ride after a tag,
  never silently.
- **Soft-pedalled descents still slip through** → they land in the "ask"
  band by design; the model auto-accepts "coast" only above 0.9, and a
  manual `pedal` tag corrects the rest.
- **Classifier learns itself into "always pedal"** → the skeptical prior
  floor, novelty check and ask floor (§4) keep it asking on coast-plausible
  and novel descents; F6 tests this explicitly.
- **Complexity creep** → each phase has an eval gate; a phase that fails
  reverts rather than shipping.

## 13. Open questions

1. Tag granularity: whole-descent ranges (chosen) vs per-point flags vs
   scrub-bracketed freeform windows. Start with whole runs + endpoint trim.
2. Should pedalled-descent recovery be global or per-ride opt-in? Recommend
   global once labels exist, with a visible "user-tagged" badge.
3. Pooled fit trigger: automatically after every tag save (cheap for small
   history) vs a manual "Run pooled calibration" button? Recommend both —
   auto for ≤ N rides, manual button always available.
4. Classifier thresholds (0.9 / 0.1) and the prior-floor strength are
   initial values; pin them against F1/F6 in phase 4, and treat the ask-floor
   size as a tunable rather than a constant.
