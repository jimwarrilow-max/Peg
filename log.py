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
    "max_uv_index",
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
        "max_uv_index":  stats["max_uv_index"],
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

def read_band(date_str: str, log_path: str = LOG_PATH) -> Optional[str]:
    """Return the stored band value for date_str, or None if not found."""
    if not os.path.isfile(log_path):
        return None
    with open(log_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("date") == date_str:
                return row.get("band")
    return None


def write_outcome(
    date_str: str,
    outcome: str,
    log_path: str = LOG_PATH,
) -> bool:
    """
    Write `outcome` into the row for `date_str`.
    Returns True if the row was found and updated, False otherwise.
    """
    if not os.path.isfile(log_path):
        return False

    rows: list[dict] = []
    fieldnames: list[str] = []
    found = False

    with open(log_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames or _COLUMNS)
        for row in reader:
            if row.get("date") == date_str:
                row["outcome"] = outcome
                found = True
            rows.append(row)

    if found:
        with open(log_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

    return found


def recent_accuracy(n: int = 10, log_path: str = LOG_PATH) -> Optional[tuple[int, int]]:
    """
    Return (correct, total) for the last n days with a non-skip outcome recorded.
    'correct' means outcome==dry when band is not TUMBLE/MARGINAL, or outcome==damp otherwise.
    Actually: correct = the band prediction matched reality (dry=good prediction, damp=bad).
    More precisely: correct when outcome=='dry' (Peg's positive forecast proved right)
    or outcome=='damp' is counted as total but not correct.
    Returns None if fewer than 3 results exist.
    """
    if not os.path.isfile(log_path):
        return None
    rows = []
    with open(log_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            outcome = row.get("outcome", "")
            if outcome in ("dry", "damp"):
                rows.append(row)
    rows = rows[-n:]
    if len(rows) < 3:
        return None
    # Peg was right when it said GOOD/CRACK and it dried, or said TUMBLE/MARGINAL and it didn't
    from scorer import Band
    correct = 0
    for row in rows:
        outcome = row["outcome"]
        band = row.get("band", "")
        predicted_dry = band in (Band.GOOD.value, Band.CRACK.value)
        if (predicted_dry and outcome == "dry") or (not predicted_dry and outcome == "damp"):
            correct += 1
    return correct, len(rows)


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
    end_hour = config.end_hour
    window = [h for h in hours if config.hang_hour <= h.hour <= end_hour]
    uv_vals = [h.uv_index for h in window if h.uv_index is not None]
    return {
        "mean_temp_c":    _mean([h.temp_c    for h in window]),
        "mean_rh_pct":    _mean([h.rh_pct    for h in window]),
        "mean_vpd_kpa":   _mean([h.vpd_kpa   for h in window]),
        "mean_wind_mph":  _mean([h.wind_mph   for h in window]),
        "mean_solar_wm2": _mean([h.solar_wm2  for h in window]),
        "max_uv_index":   round(max(uv_vals), 1) if uv_vals else None,
    }
