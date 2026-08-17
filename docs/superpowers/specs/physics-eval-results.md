# Physics eval scoreboard

Gate: `python tests/physics_eval.py` + `python tests/verify_backend.py`.
Rule: an iteration must not regress any check and must improve ≥1 target.

| Iter | Change | M1 crr_err | M1 cdA_err | M2 bias (W) | M3 med_rel | M4 coverage | M5 rate | M6 W | verify |
|------|--------|-----------|-----------|-------------|-----------|-------------|---------|------|--------|
| base | (none) | 0.195 | 0.350 | −4.7 (flat −22) | 0.025 | 0.998 | 0.913 ✗ | 37 ✓ | pass |
| 2 | crosswind drag + HR-aware coast | 0.195 | 0.350 | **+0.4** | 0.025 | 0.997 | **0.979** | 37 ✓ | pass |
| 3 | elevation-aware air density | 0.195 | 0.350 | −0.9 | 0.025 | 0.997 | 0.975 | 37 ✓ | pass |
| 4 | wind-recovering calibration | **4.5e-5** | **0.0016** (wind 4.9/90.3° vs 5/90°) | +0.4 | 0.025 | 0.997 | 0.975 | 37 ✓ | pass |
| 5 | Savitzky-Golay smoothing | 7.2e-4 | 0.018 | +0.4 | 0.024 | 0.997 | **0.992** | 37 ✓ | pass |
| 6 | wind reapply in pipeline (M8: MAE 34.1→9.2) | 7.2e-4 | 0.018 | +0.4 | 0.024 | 0.997 | 0.992 | 37 ✓ | pass |
| 7 | robust calibration + climb-crr fix + covariance | 8.7e-4 | 0.021 | +0.4 | 0.024 | 0.997 | 0.992 | 37 ✓ | pass |
| 8 | honest bands (per-ride wind sigma, calib cov) | 8.7e-4 | 0.021 | +0.4 | 0.024 | **0.992**, width **0.38** | 0.992 | 37 ✓ | pass |
| 9 | time-windowed power curves + robust watts@HR | 8.7e-4 | 0.021 | +0.4 | 0.024 | 0.992 | 0.992 | 37 ✓ | pass |
| 10 | hourly wind interpolation (M13: 27.5→12.5 W) | 8.7e-4 | 0.021 | +0.4 | 0.024 | 0.992 | 0.992 | 37 ✓ | pass |
| review | circular-bearings guard, grade sigma reverted to 0.005 (DEM-safe), wind reapply in server + recalc paths | 8.7e-4 | 0.021 | +0.4 | 0.024 | 0.992 | 0.992 | 37 ✓ | pass |

Targets: M1 crr_err ≤ 0.0008, cdA_err ≤ 0.03; M2 |bias| ≤ 3 W; M3 ≤ 0.06;
M4 ≥ 0.90; M5 rate ≥ 0.95, false_coast = 0; M6 plausible.
