"""
Pure scoring function for Peg / Good Drying Day.

Input:  a list of HourForecast dataclasses (VPD already computed by the
        fetch-transform layer) + a WindowConfig.
Output: ScoreResult with raw_score, band, display_score, will_dry,
        override, best_window, and reason.

No I/O, no network calls, no side-effects.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


# ---------------------------------------------------------------------------
# Constants — physics fixed; others are calibration knobs (§7)
# ---------------------------------------------------------------------------

# VPD formula coefficients (physics — do not calibrate)
_ES_A = 0.6108
_ES_B = 17.27
_ES_C = 237.3

# Calibration knobs
VPD_FULL = 1.0          # kPa — VPD at which sub-score reaches 1.0
WIND_FLOOR = 0.25       # still-air floor
WIND_FULL_MPH = 12.0    # mph at which wind sub-score reaches 1.0
SOLAR_FULL = 450.0      # W/m² at which solar sub-score reaches 1.0

WEIGHT_VPD = 0.50
WEIGHT_WIND = 0.30
WEIGHT_SOLAR = 0.20

RAIN_PROB_GATE = 50     # > this → gated  (i.e. >50%, not >=50%)
RAIN_MM_GATE = 0.2      # > this → gated

LATE_RAIN_HOURS = 2     # final N hours of window trigger "risky bring-in"
UNSCORABLE_LIMIT = 0.25 # fraction — >25% unscorable → skip the day

DRY_TARGET = 4.0        # "four perfect hours" for towels


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

class Band(str, Enum):
    TUMBLE   = "Tumble-dryer weather"
    MARGINAL = "Marginal"
    GOOD     = "Good drying day"
    CRACK    = "Crack open the pegs"


@dataclass
class HourForecast:
    """One hour of forecast data.  VPD is computed by the caller (fetch layer)."""
    hour: int                          # 0–23
    temp_c: Optional[float]            # for logging / VPD sanity only
    rh_pct: Optional[float]            # for logging / VPD sanity only
    vpd_kpa: Optional[float]           # computed upstream; None → unscorable
    wind_mph: Optional[float]          # mph — caller must convert
    solar_wm2: Optional[float]         # W/m²
    precip_mm: Optional[float]         # mm/h
    precip_prob_pct: Optional[float]   # 0–100
    wind_gust_mph: Optional[float] = None   # for gust flag only; not scored
    uv_index: Optional[float] = None        # display only; not used in scoring


@dataclass
class WindowConfig:
    hang_hour: int          # earliest hour washing goes out (0–23)
    bring_in_hour: int      # latest hour washing must be in (0–23)
    dusk_hour: int          # hour of sunset (inclusive cap)


@dataclass
class ScoreResult:
    raw_score: float
    display_score: int      # rounded to nearest 5, clamped 0–100
    band: Band
    will_dry: bool
    override: bool          # True → "Risky bring-in" applies
    best_window: Optional[tuple[int, int]]   # (start_hour, end_hour) inclusive
    gust_flag: bool         # gusts >32 mph observed in window — independent of score
    skipped: bool           # True → data too poor to score
    reason: str             # one-line explanation for the notification


# ---------------------------------------------------------------------------
# VPD helper (also used by the fetch-transform layer to populate HourForecast)
# ---------------------------------------------------------------------------

def compute_vpd(temp_c: float, rh_pct: float) -> float:
    """Tetens saturation vapour pressure → VPD in kPa."""
    es = _ES_A * math.exp(_ES_B * temp_c / (temp_c + _ES_C))
    return es * (1.0 - rh_pct / 100.0)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _is_rain_gated(hour: HourForecast) -> bool:
    prob = hour.precip_prob_pct
    mm   = hour.precip_mm
    if prob is not None and prob > RAIN_PROB_GATE:
        return True
    if mm is not None and mm > RAIN_MM_GATE:
        return True
    return False


def _is_scorable(hour: HourForecast) -> bool:
    """An hour is unscorable if any required scoring field is missing."""
    return all(v is not None for v in (
        hour.vpd_kpa,
        hour.wind_mph,
        hour.solar_wm2,
        hour.precip_mm,
        hour.precip_prob_pct,
    ))


def _hourly_potential(hour: HourForecast) -> float:
    """Returns 0–1; caller must have verified the hour is scorable."""
    if _is_rain_gated(hour):
        return 0.0
    vpd_score   = _clamp(hour.vpd_kpa / VPD_FULL, 0.0, 1.0)
    wind_score  = _clamp(WIND_FLOOR + hour.wind_mph / (WIND_FULL_MPH / (1.0 - WIND_FLOOR)), 0.0, 1.0)
    solar_score = _clamp(hour.solar_wm2 / SOLAR_FULL, 0.0, 1.0)
    return WEIGHT_VPD * vpd_score + WEIGHT_WIND * wind_score + WEIGHT_SOLAR * solar_score


def _round5(value: float) -> int:
    """Round to nearest 5."""
    return int(round(value / 5.0) * 5)


def _band_from_raw(raw: float) -> Band:
    return band_from_raw(raw)


def band_from_raw(raw: float) -> Band:
    """Band boundaries evaluated on the raw (unrounded) score."""
    if raw < 35:
        return Band.TUMBLE
    if raw < 55:
        return Band.MARGINAL
    if raw < 80:
        return Band.GOOD
    return Band.CRACK


def round_display(raw: float) -> int:
    """Round raw score to nearest 5 for display, clamped to [0, 100]."""
    return int(max(0, min(100, round(raw / 5.0) * 5)))


def _find_best_window(
    hours: list[HourForecast],
    window_hours: list[int],
    potentials: dict[int, float],
) -> Optional[tuple[int, int]]:
    """
    Earliest contiguous run of scorable hours whose cumulative potential
    reaches DRY_TARGET.  Returns (start_hour, end_hour) inclusive, or None.
    """
    for start_idx in range(len(window_hours)):
        cumulative = 0.0
        for end_idx in range(start_idx, len(window_hours)):
            h = window_hours[end_idx]
            cumulative += potentials.get(h, 0.0)
            if cumulative >= DRY_TARGET:
                return (window_hours[start_idx], window_hours[end_idx])
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def score(hours: list[HourForecast], config: WindowConfig) -> ScoreResult:
    """
    Score the day.

    `hours` must cover all hours in [hang_hour, min(bring_in_hour, dusk_hour)].
    VPD must already be computed by the caller.
    """
    # --- Determine the effective window ----------------------------------
    end_hour = min(config.bring_in_hour, config.dusk_hour)
    window_hours = [h for h in range(config.hang_hour, end_hour + 1)]

    if not window_hours:
        return ScoreResult(
            raw_score=0, display_score=0, band=Band.TUMBLE,
            will_dry=False, override=False, best_window=None,
            gust_flag=False, skipped=True,
            reason="No daylight window — check hang/bring-in config.",
        )

    # Index hours by hour number for easy lookup
    by_hour: dict[int, HourForecast] = {h.hour: h for h in hours}
    window_forecasts = [by_hour[h] for h in window_hours if h in by_hour]

    # --- Check unscorable hours ------------------------------------------
    unscorable = [h for h in window_forecasts if not _is_scorable(h)]
    scorable   = [h for h in window_forecasts if _is_scorable(h)]

    total = len(window_hours)
    if total > 0 and len(unscorable) / total > UNSCORABLE_LIMIT:
        return ScoreResult(
            raw_score=0, display_score=0, band=Band.TUMBLE,
            will_dry=False, override=False, best_window=None,
            gust_flag=False, skipped=True,
            reason="Too many missing data points — skipping today rather than guessing.",
        )

    # --- Gust flag (independent of score) --------------------------------
    gust_flag = any(
        h.wind_gust_mph is not None and h.wind_gust_mph > 32
        for h in window_forecasts
    )

    # --- Per-hour potentials ---------------------------------------------
    potentials: dict[int, float] = {
        h.hour: _hourly_potential(h) for h in scorable
    }

    cumulative = sum(potentials.values())

    # --- Raw score -------------------------------------------------------
    raw_score = _clamp(50.0 * cumulative / DRY_TARGET, 0.0, 100.0)

    # --- will_dry ---------------------------------------------------------
    will_dry = cumulative >= DRY_TARGET

    # --- Band (on raw score) ---------------------------------------------
    band = _band_from_raw(raw_score)

    # --- Late-rain "risky bring-in" override -----------------------------
    # Final LATE_RAIN_HOURS of the window
    late_hours = window_hours[-LATE_RAIN_HOURS:] if len(window_hours) >= LATE_RAIN_HOURS else window_hours
    late_forecasts = [by_hour[h] for h in late_hours if h in by_hour]
    override = any(_is_rain_gated(h) for h in late_forecasts)

    if override:
        # Cap band at Marginal
        if band in (Band.GOOD, Band.CRACK):
            band = Band.MARGINAL

    # --- Display score (rounded to nearest 5) ----------------------------
    display_score = _clamp(_round5(raw_score), 0, 100)

    # --- Best window ------------------------------------------------------
    scorable_window_hours = [h for h in window_hours if h in potentials]
    best_window = _find_best_window(hours, scorable_window_hours, potentials)

    # --- Reason (one-liner) ----------------------------------------------
    if override:
        reason = f"Rain expected in the final {LATE_RAIN_HOURS}h — risky bring-in."
    elif band == Band.CRACK:
        reason = "Dry, breezy, and plenty of energy — ideal conditions."
    elif band == Band.GOOD:
        reason = "Good drying conditions across the window."
    elif band == Band.MARGINAL:
        reason = "Borderline — heavy items may not fully dry."
    else:
        reason = "Air too damp or rain in the window."

    return ScoreResult(
        raw_score=raw_score,
        display_score=display_score,
        band=band,
        will_dry=will_dry,
        override=override,
        best_window=best_window,
        gust_flag=gust_flag,
        skipped=False,
        reason=reason,
    )
