"""
The Prediction Log — Peg's record of every Day, kept in log.csv.

This module is the only code that opens log.csv. Callers work with Day
records and never see CSV rows. It also owns the rules about Days:
whether a Day is answerable, whether Peg was right, and Accuracy.

    log = PredictionLog()                 # log.csv; pass a path in tests
    log.record(date, result, cfg, hours)  # morning: add today's Day (once)
    log.day(date)                         # one Day, or None
    log.days(start, end)                  # Days in a date range
    log.record_outcome(date, Outcome.DRY) # evening answer
    log.recent_accuracy()                 # Accuracy over the last 10 answers

File format (stable — add columns at the end only):
  date, hang_hour, bring_in_hour, dusk_hour,
  raw_score, display_score, band, will_dry, override, gust_flag, skipped,
  best_window,
  mean_temp_c, mean_rh_pct, mean_vpd_kpa, mean_wind_mph, mean_solar_wm2,
  max_uv_index,
  outcome
"""

from __future__ import annotations

import csv
import os
from dataclasses import dataclass
from datetime import date as Date
from enum import Enum
from typing import Iterable, NamedTuple, Optional

from scorer import Band, HourForecast, ScoreResult, WindowConfig

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


class Outcome(str, Enum):
    """The user's evening answer. The value is what log.csv stores."""
    DRY  = "dry"
    DAMP = "damp"
    SKIP = "skip"   # "Didn't hang"


class Accuracy(NamedTuple):
    correct: int
    total: int


@dataclass(frozen=True)
class Day:
    """One row of the Prediction Log: Peg's verdict and the user's Outcome."""
    date: Date
    band: Optional[Band]
    outcome: Optional[Outcome]

    @property
    def answerable(self) -> bool:
        """
        True if Peg asks for an Outcome on this Day. Tumble-dryer Days get
        no evening question — "did it dry?" makes no sense after "don't bother".
        """
        return self.band != Band.TUMBLE

    @property
    def answered(self) -> bool:
        return self.outcome is not None

    @property
    def correct(self) -> Optional[bool]:
        """
        Was Peg right? None unless the Outcome is dry or damp.
        Right when Peg predicted drying (Good/Crack) and it dried, or
        predicted no drying (Tumble/Marginal) and it stayed damp.
        """
        if self.outcome not in (Outcome.DRY, Outcome.DAMP):
            return None
        predicted_dry = self.band in (Band.GOOD, Band.CRACK)
        return predicted_dry == (self.outcome == Outcome.DRY)


def accuracy(days: Iterable[Day]) -> Accuracy:
    """Correct Days out of Days with a dry or damp Outcome."""
    judged = [d.correct for d in days if d.correct is not None]
    return Accuracy(correct=sum(judged), total=len(judged))


class PredictionLog:

    def __init__(self, path: str = LOG_PATH) -> None:
        self.path = path

    # --- Writes ---------------------------------------------------------

    def record(
        self,
        today: Date,
        result: ScoreResult,
        config: WindowConfig,
        hours: list[HourForecast],
    ) -> None:
        """
        Add the Day for `today`, creating the file with headers if needed.
        Does nothing if that Day is already recorded.
        """
        if self.day(today) is not None:
            return

        stats = _window_stats(hours, config)
        best = (
            f"{result.best_window[0]:02d}:00-{result.best_window[1]:02d}:00"
            if result.best_window else ""
        )
        row = {
            "date":          today.isoformat(),
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
            **stats,
            "outcome":       "",
        }

        file_exists = os.path.isfile(self.path)
        with open(self.path, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=_COLUMNS)
            if not file_exists:
                writer.writeheader()
            writer.writerow(row)

    def record_outcome(self, day: Date, outcome: Outcome) -> bool:
        """
        Store the Outcome for `day`, replacing any earlier one.
        Returns False if there is no such Day.
        """
        if not os.path.isfile(self.path):
            return False

        key = day.isoformat()
        found = False
        with open(self.path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            fieldnames = list(reader.fieldnames or _COLUMNS)
            rows = []
            for row in reader:
                if row.get("date") == key:
                    row["outcome"] = outcome.value
                    found = True
                rows.append(row)

        if found:
            with open(self.path, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(rows)
        return found

    # --- Reads ----------------------------------------------------------

    def day(self, day: Date) -> Optional[Day]:
        key = day.isoformat()
        return next((d for d in self._days() if d.date.isoformat() == key), None)

    def days(self, start: Date, end: Date) -> list[Day]:
        """Days from `start` to `end` inclusive, in file order."""
        return [d for d in self._days() if start <= d.date <= end]

    def recent_accuracy(self, n: int = 10) -> Optional[Accuracy]:
        """
        Accuracy over the last n Days with a dry or damp Outcome
        (n counts answers, not calendar days). None if fewer than 3.
        """
        judged = [d for d in self._days() if d.correct is not None][-n:]
        if len(judged) < 3:
            return None
        return accuracy(judged)

    # --- Internal -------------------------------------------------------

    def _days(self) -> list[Day]:
        if not os.path.isfile(self.path):
            return []
        with open(self.path, newline="", encoding="utf-8") as f:
            return [d for row in csv.DictReader(f) if (d := _to_day(row))]


def _to_day(row: dict) -> Optional[Day]:
    """A CSV row as a Day; None if the date cannot be read."""
    try:
        when = Date.fromisoformat(row.get("date") or "")
    except ValueError:
        return None
    return Day(
        date=when,
        band=_enum(Band, row.get("band")),
        outcome=_enum(Outcome, row.get("outcome")),
    )


def _enum(kind, value):
    try:
        return kind(value)
    except ValueError:
        return None


def _mean(values: list) -> Optional[float]:
    clean = [v for v in values if v is not None]
    return round(sum(clean) / len(clean), 3) if clean else None


def _window_stats(hours: list[HourForecast], config: WindowConfig) -> dict:
    window = [h for h in hours if config.hang_hour <= h.hour <= config.end_hour]
    uv_vals = [h.uv_index for h in window if h.uv_index is not None]
    return {
        "mean_temp_c":    _mean([h.temp_c    for h in window]),
        "mean_rh_pct":    _mean([h.rh_pct    for h in window]),
        "mean_vpd_kpa":   _mean([h.vpd_kpa   for h in window]),
        "mean_wind_mph":  _mean([h.wind_mph  for h in window]),
        "mean_solar_wm2": _mean([h.solar_wm2 for h in window]),
        "max_uv_index":   round(max(uv_vals), 1) if uv_vals else None,
    }
