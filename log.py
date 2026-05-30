"""
Prediction log for Peg — one CSV row per day, committed to the repo.

append_prediction(date, result, config, hours, log_path)

Idempotent: if a row for `date` already exists, the call is a no-op.
The `outcome` column starts empty and is filled later (Phase 3.5).

Schema (stable — add columns at the end only):
  date, hang_hour, bring_in_hour, dusk_hour,
  raw_score, display_score, band, will_dry, override, gust_flag, skipped,
  best_window,
  mean_temp_c, mean_rh_pct, mean_vpd_kpa, mean_wind_mph, mean_solar_wm2,
  outcome
"""

from __future__ import annotations

import csv
import os
from datetime import date as Date
from typing import Optional

from scorer import HourForecast, ScoreResult, WindowConfig

LOG_PATH = "log.csv"

_COLUMNS = [
    "date",
    "hang_hour", "bring_in_hour", "dusk_hour",
    "raw_score", "display_score", "band", "will_dry", "override",
    "gust_flag", "skipped", "best_window",
    "mean_temp_c", "mean_rh_pct", "mean_vpd_kpa", "mean_wind_mph", "mean_solar_wm2",
    "outcome",
]


def append_prediction(
    today: Date,
    result: ScoreResult,
    config: WindowConfig,
    hours: list[HourForecast],
    log_path: str = LOG_PATH,
) -> None:
    """
    Append one prediction row to the CSV, creating the file with headers if needed.
    Does nothing if a row for `today` is already present (idempotency guard).
    """
    date_str = today.isoformat()

    if _row_exists(log_path, date_str):
        return

    stats = _window_stats(hours, config)
    best = (
        f"{result.best_window[0]:02d}:00-{result.best_window[1]:02d}:00"
        if result.best_window else ""
    )

    row = {
        "date":          date_str,
        "hang_hour":     config.hang_hour,
        "bring_in_hour": config.bring_in_hour,
        "dusk_hour":     config.dusk_hour,
        "raw_score":     round(result.raw_score, 2),
        "display_score": result.display_score,
        "band":          result.band.value,
        "will_dry":      result.will_dry,
        "override":      result.override,
        "gust_flag":     result.gust_flag,
        "skipped":       result.skipped,
        "best_window":   best,
        "mean_temp_c":   stats["mean_temp_c"],
        "mean_rh_pct":   stats["mean_rh_pct"],
        "mean_vpd_kpa":  stats["mean_vpd_kpa"],
        "mean_wind_mph": stats["mean_wind_mph"],
        "mean_solar_wm2": stats["mean_solar_wm2"],
        "outcome":       "",
    }

    file_exists = os.path.isfile(log_path)
    with open(log_path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=_COLUMNS)
        if not file_exists:
            writer.writeheader()
        writer.writerow(row)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _row_exists(log_path: str, date_str: str) -> bool:
    if not os.path.isfile(log_path):
        return False
    with open(log_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return any(row.get("date") == date_str for row in reader)


def _mean(values: list[float]) -> Optional[float]:
    clean = [v for v in values if v is not None]
    return round(sum(clean) / len(clean), 3) if clean else None


def _window_stats(hours: list[HourForecast], config: WindowConfig) -> dict:
    end_hour = min(config.bring_in_hour, config.dusk_hour)
    window = [h for h in hours if config.hang_hour <= h.hour <= end_hour]
    return {
        "mean_temp_c":    _mean([h.temp_c    for h in window]),
        "mean_rh_pct":    _mean([h.rh_pct    for h in window]),
        "mean_vpd_kpa":   _mean([h.vpd_kpa   for h in window]),
        "mean_wind_mph":  _mean([h.wind_mph   for h in window]),
        "mean_solar_wm2": _mean([h.solar_wm2  for h in window]),
    }
