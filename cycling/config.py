"""Configuration, paths and physical defaults for the engine."""

import os
import pathlib

# ---------------------------------------------------------------------------
# Paths — everything lives under the user's home directory so the app is
# private by construction and needs no admin rights.
# ---------------------------------------------------------------------------

APP_NAME = "Cycling Progress Tracker"

DATA_ROOT = pathlib.Path(
    os.environ.get("CYCLING_DATA_ROOT", pathlib.Path.home() / ".cycling_tracker")
)
DB_PATH = DATA_ROOT / "cycling.db"
CACHE_ROOT = DATA_ROOT / "cache"
LIDAR_CACHE = CACHE_ROOT / "lidar"
DEM_CACHE = CACHE_ROOT / "dem"
OPENTOPO_CACHE = CACHE_ROOT / "opentopo.json"
WEATHER_CACHE = CACHE_ROOT / "weather.json"
ROAD_CACHE = CACHE_ROOT / "roads"
IMPORT_DIR = DATA_ROOT / "imports"

for _p in (DATA_ROOT, CACHE_ROOT, LIDAR_CACHE, DEM_CACHE, ROAD_CACHE, IMPORT_DIR):
    _p.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Elevation stage
# ---------------------------------------------------------------------------

# Try EA National LIDAR before the 25 m DEM fallback. Set CYCLING_PREFER_LIDAR=0
# to skip the (sometimes slow) WCS request and go straight to EU-DEM.
PREFER_LIDAR = os.environ.get("CYCLING_PREFER_LIDAR", "1") != "0"

# How far the track may drift from the road network before we stop snapping.
MAPMATCH_MAX_DISTANCE_M = 30.0
# Smooth elevation over roughly this distance (metres) before differentiating
# into grade. 1 m lidar is noisier than a 25 m DEM unless smoothed.
SMOOTH_DISTANCE_M = 25.0
# Median-despike window (metres) applied to the raw sampled elevation before
# smoothing. Kills isolated GPS/lidar sampling spikes.
ELEV_MEDIAN_WINDOW_M = 30.0
# Grade is the least-squares slope over this baseline (metres). Differentiating
# at ~4 m spacing is pure noise; ~50-60 m matches what the wheel rides.
GRADE_BASELINE_M = 60.0
# Minimum rise per ~25 m bin counted towards elevation gain. Applied to the
# distance-resampled profile so the threshold is independent of point spacing.
# 0.2 m per 25 m bin ≈ 0.8% grade: low enough that a real 1-2% climb counts,
# high enough to reject the few-cm noise floor of a 25 m-smoothed profile.
ELEVATION_GAIN_THRESHOLD = 0.2
# Minimum track length (metres) before we even attempt elevation work.
MIN_TRACK_M = 200.0
# Grade sanity clamp (a road steeper than this is almost always a data error).
MAX_GRADE = 0.35  # 35%
# Any point on a grade below this is "downhill" and is excluded from power
# entirely: on a descent speed + grade cannot separate pedalling, coasting
# and braking (gravity offsets aero drag and wind is unknown), so the model
# reports ~0 W rather than a fake number. Descending points are also kept
# out of every power aggregate (average/NP/best-N-minute/VO2max).
DOWNHILL_GRADE = -0.01  # -1%

# Grade above which a point counts as "climbing" for VAM and climb-segment
# detection. Deliberately coarser than the elevation-gain threshold: VAM is a
# metric of real climbs, not of every metre gained.
CLIMB_GRADE = 0.03

# UK bounds — EA National LIDAR coverage is England only (roughly).
LIDAR_LAT_MIN, LIDAR_LAT_MAX = 49.85, 55.88
LIDAR_LON_MIN, LIDAR_LON_MAX = -7.10, 2.09

# EA National LIDAR composite DTM (1 m, OGL). Coverage id discovered via
# GetCapabilities. The elevation grid is EPSG:27700 (OSGB36 / British
# National Grid) and ~99% of England at 1 m resolution.
LIDAR_WCS_URL = (
    "https://environment.data.gov.uk/geoservices/datasets/"
    "13787b9a-26a4-4775-8523-806d13af58fc/wcs"
)
LIDAR_COVERAGE_ID = (
    "13787b9a-26a4-4775-8523-806d13af58fc__Lidar_Composite_Elevation_DTM_1m"
)
# Cap the lidar raster we download per track to keep sampling cheap.
LIDAR_MAX_PIXELS = 1600
# Padding (metres) added around the track bounding box before sampling.
LIDAR_BBOX_PAD_M = 60.0

# Fallback DEM providers (used when lidar is unavailable / out of coverage).
OPENTOPO_DATASET = "eudem25m"  # EU-DEM 25 m

# ---------------------------------------------------------------------------
# Physical defaults (per bike / rider, overridden by profile & calibration)
# ---------------------------------------------------------------------------

DEFAULT_RIDER = {
    "age": 40,
    "weight_kg": 75.0,
    "height_cm": 178.0,
    "bike_type": "road",
    "sex": None,  # male/female feed the Keytel calorie + Banister TRIMP equations
    "resting_hr": 55,
    "max_hr": None,  # computed from age if unset
    "hr_zones": None,  # computed from max_hr if unset
}

DEFAULT_BIKE = {
    "name": "Road bike",
    "mass_kg": 9.0,
    "crr": 0.0050,          # rolling resistance coefficient
    "cdA": 0.35,            # m^2, effective drag area (rider + bike)
    "drivetrain_efficiency": 0.97,
    "calibrated": False,
}

# Uncertainty model — the "honest ceiling" from the plan. Wind on flat ground
# cannot be separated from CdA using speed + grade alone, so uncalibrated
# rides get wide bands and calibrated rides get tighter ones.
UNCERTAINTY = {
    "wind_sigma_mps": 2.0,       # 1-sigma unknown wind, m/s
    "cdA_rel_sigma": 0.20,       # fraction of CdA (uncalibrated)
    "cdA_rel_sigma_cal": 0.06,   # fraction of CdA (loop-calibrated)
    "crr_rel_sigma": 0.25,       # fraction of Crr (uncalibrated)
    "crr_rel_sigma_cal": 0.08,   # fraction of Crr (loop-calibrated)
    "grade_sigma": 0.005,        # 0.5% grade uncertainty (lidar is finer, 25 m DEM needs this)
    "mass_sigma_kg": 1.5,        # rider + kit + bike mass uncertainty
    "rho_rel_sigma": 0.015,      # air-density rel. error (weather + elevation)
}

AIR_DENSITY_DEFAULT = 1.225  # kg/m^3 (15 C, 1013 hPa)

# Headline trend: watts at these fixed heart rates.
WATTS_AT_HR = [130, 140, 150, 160]

# Best-effort power curve durations (minutes).
POWER_CURVE_MINUTES = [1, 2, 5, 10, 20, 30, 60]

# Banister TRIMP + impulse-response fitness constants.
TRIMP_HR_EXP = 1.92
TRIMP_HR_COEF = 0.64
# Banister's women's constants differ from the men's; TRIMP must use the
# rider's sex rather than always applying the male weighting.
TRIMP_HR_EXP_FEMALE = 1.67
TRIMP_HR_COEF_FEMALE = 0.86
CTL_TAU_DAYS = 42.0
ATL_TAU_DAYS = 7.0

# Cardiac drift: steady-effort window detection. HR drift at constant
# estimated power is only interpretable when the window is long and the
# workload genuinely steady, so the thresholds are deliberately conservative.
DRIFT_MIN_MINUTES = 8.0       # minimum window length
DRIFT_SKIP_START_S = 240.0    # skip the first 4 min (warm-up HR rise)
DRIFT_MIN_WATTS = 60.0        # below this it isn't a workload
DRIFT_HR_MIN = 100.0          # HR must be elevated (aerobic band)
DRIFT_SMOOTH_S = 60.0         # power smoothed over this window before CV check
DRIFT_MERGE_GAP_S = 90.0      # bridge stops at junctions shorter than this
DRIFT_CV_POWER = 0.25         # max CV of *smoothed* estimated watts
DRIFT_CV_SPEED = 0.35         # max coefficient of variation of speed

# Weather: Open-Meteo archive (no API key), used for air density + wind.
OPEN_METEO_ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"

# Timeout (seconds) for external HTTP calls. Missing data degrades gracefully.
HTTP_TIMEOUT = 25.0

# FIT epoch: 1989-12-31T00:00:00 UTC as a Unix timestamp.
FIT_EPOCH_UNIX = 631065600
