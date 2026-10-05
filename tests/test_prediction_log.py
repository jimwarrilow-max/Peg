"""
Tests for the Prediction Log, through its interface only.

Each test uses a temporary log.csv. File-format checks read the CSV
directly on purpose: the format is part of the contract (old rows must
keep working, and GitHub commits the file daily).
"""

from __future__ import annotations

import csv
from datetime import date

import pytest

from prediction_log import Accuracy, Day, Outcome, PredictionLog, accuracy
from scorer import Band, HourForecast, ScoreResult, WindowConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _result(band: Band = Band.GOOD, raw_score: float = 75.0, **kw) -> ScoreResult:
    fields = dict(
        raw_score=raw_score, display_score=75, band=band,
        will_dry=True, override=False, best_window=(9, 14),
        gust_flag=False, skipped=False,
    )
    fields.update(kw)
    return ScoreResult(**fields)


def _cfg() -> WindowConfig:
    return WindowConfig(hang_hour=9, bring_in_hour=18, dusk_hour=21)


def _hours(temp_c: float = 18.0) -> list[HourForecast]:
    return [
        HourForecast(hour=i, temp_c=temp_c, rh_pct=60.0, vpd_kpa=0.7,
                     wind_mph=8.0, solar_wm2=300.0, precip_mm=0.0,
                     precip_prob_pct=5.0)
        for i in range(24)
    ]


@pytest.fixture
def log(tmp_path) -> PredictionLog:
    return PredictionLog(str(tmp_path / "log.csv"))


def _rows(log: PredictionLog) -> list[dict]:
    with open(log.path, newline="") as f:
        return list(csv.DictReader(f))


def _log_with(log: PredictionLog, entries: list[tuple[str, Band, str]]) -> PredictionLog:
    """entries: (iso date, band, outcome value or '')"""
    for iso, band, outcome in entries:
        log.record(date.fromisoformat(iso), _result(band=band), _cfg(), _hours())
        if outcome:
            log.record_outcome(date.fromisoformat(iso), Outcome(outcome))
    return log


D = date(2026, 5, 30)


# ---------------------------------------------------------------------------
# record — writing a Day (file format unchanged)
# ---------------------------------------------------------------------------

class TestRecord:

    def test_creates_file_with_header(self, log):
        log.record(D, _result(), _cfg(), _hours())
        assert len(_rows(log)) == 1

    def test_row_has_expected_fields(self, log):
        log.record(D, _result(band=Band.GOOD, raw_score=72.5), _cfg(), _hours())
        row = _rows(log)[0]
        assert row["date"] == "2026-05-30"
        assert row["band"] == Band.GOOD.value
        assert float(row["raw_score"]) == pytest.approx(72.5)

    def test_column_order_unchanged(self, log):
        log.record(D, _result(), _cfg(), _hours())
        with open(log.path, newline="") as f:
            header = next(csv.reader(f))
        assert header == [
            "date", "hang_hour", "bring_in_hour", "dusk_hour",
            "raw_score", "display_score", "band", "will_dry", "override",
            "gust_flag", "skipped", "best_window",
            "mean_temp_c", "mean_rh_pct", "mean_vpd_kpa", "mean_wind_mph", "mean_solar_wm2",
            "max_uv_index", "outcome",
        ]

    def test_outcome_column_empty_on_write(self, log):
        log.record(D, _result(), _cfg(), _hours())
        assert _rows(log)[0]["outcome"] == ""

    def test_idempotent_same_day(self, log):
        log.record(D, _result(), _cfg(), _hours())
        log.record(D, _result(), _cfg(), _hours())
        assert len(_rows(log)) == 1

    def test_different_days_append_separate_rows(self, log):
        log.record(date(2026, 5, 30), _result(), _cfg(), _hours())
        log.record(date(2026, 5, 31), _result(), _cfg(), _hours())
        assert [r["date"] for r in _rows(log)] == ["2026-05-30", "2026-05-31"]

    def test_window_stats_written(self, log):
        log.record(D, _result(), _cfg(), _hours(temp_c=15.0))
        row = _rows(log)[0]
        assert float(row["mean_temp_c"])    == pytest.approx(15.0)
        assert float(row["mean_rh_pct"])    == pytest.approx(60.0)
        assert float(row["mean_vpd_kpa"])   == pytest.approx(0.7)
        assert float(row["mean_wind_mph"])  == pytest.approx(8.0)
        assert float(row["mean_solar_wm2"]) == pytest.approx(300.0)
        assert row["max_uv_index"] == ""    # no uv_index → empty

    def test_best_window_formatted(self, log):
        log.record(D, _result(best_window=(9, 13)), _cfg(), _hours())
        assert _rows(log)[0]["best_window"] == "09:00-13:00"

    def test_no_best_window_is_empty_string(self, log):
        log.record(D, _result(best_window=None), _cfg(), _hours())
        assert _rows(log)[0]["best_window"] == ""

    def test_skipped_day_still_recorded(self, log):
        """Even a skipped day gets a row, so gaps are visible."""
        log.record(D, _result(skipped=True, raw_score=0.0, band=Band.TUMBLE, will_dry=False),
                   _cfg(), _hours())
        assert _rows(log)[0]["skipped"] == "True"


# ---------------------------------------------------------------------------
# record_outcome
# ---------------------------------------------------------------------------

class TestRecordOutcome:

    @pytest.mark.parametrize("outcome", list(Outcome))
    def test_stores_outcome(self, log, outcome):
        log.record(D, _result(), _cfg(), _hours())
        assert log.record_outcome(D, outcome) is True
        assert log.day(D).outcome == outcome
        assert _rows(log)[0]["outcome"] == outcome.value

    def test_false_for_missing_day(self, log):
        log.record(D, _result(), _cfg(), _hours())
        assert log.record_outcome(date(2026, 5, 31), Outcome.DRY) is False

    def test_false_for_missing_file(self, log):
        assert log.record_outcome(D, Outcome.DRY) is False

    def test_only_target_day_updated(self, log):
        _log_with(log, [("2026-05-29", Band.GOOD, ""), ("2026-05-30", Band.GOOD, "dry"),
                        ("2026-05-31", Band.GOOD, "")])
        assert [r["outcome"] for r in _rows(log)] == ["", "dry", ""]

    def test_preserves_all_columns(self, log):
        log.record(D, _result(), _cfg(), _hours())
        before = _rows(log)[0]
        log.record_outcome(D, Outcome.DRY)
        after = _rows(log)[0]
        assert {k: v for k, v in after.items() if k != "outcome"} == \
               {k: v for k, v in before.items() if k != "outcome"}

    def test_can_be_overwritten(self, log):
        """A delayed reply or correction replaces the earlier Outcome."""
        log.record(D, _result(), _cfg(), _hours())
        log.record_outcome(D, Outcome.DRY)
        log.record_outcome(D, Outcome.DAMP)
        assert log.day(D).outcome == Outcome.DAMP


# ---------------------------------------------------------------------------
# day / days
# ---------------------------------------------------------------------------

class TestReadDays:

    def test_day_returns_band(self, log):
        log.record(D, _result(band=Band.GOOD), _cfg(), _hours())
        assert log.day(D) == Day(date=D, band=Band.GOOD, outcome=None)

    def test_day_none_for_missing_date(self, log):
        log.record(D, _result(), _cfg(), _hours())
        assert log.day(date(2026, 5, 31)) is None

    def test_day_none_for_missing_file(self, log):
        assert log.day(D) is None

    def test_days_in_range_inclusive(self, log):
        _log_with(log, [(f"2026-05-{n}", Band.GOOD, "") for n in (27, 28, 29, 30, 31)])
        got = log.days(date(2026, 5, 28), date(2026, 5, 30))
        assert [d.date.day for d in got] == [28, 29, 30]

    def test_days_empty_for_missing_file(self, log):
        assert log.days(date(2026, 5, 1), date(2026, 5, 31)) == []

    def test_unreadable_date_row_is_ignored(self, log):
        log.record(D, _result(), _cfg(), _hours())
        with open(log.path, "a", newline="") as f:
            f.write("not-a-date" + "," * 18 + "\r\n")
        assert len(log.days(date(2026, 1, 1), date(2026, 12, 31))) == 1

    def test_unknown_outcome_reads_as_unanswered(self, log):
        log.record(D, _result(), _cfg(), _hours())
        rows = _rows(log)
        rows[0]["outcome"] = "maybe"
        with open(log.path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
        assert log.day(D).answered is False


# ---------------------------------------------------------------------------
# Day rules
# ---------------------------------------------------------------------------

class TestDayRules:

    @pytest.mark.parametrize("band,answerable", [
        (Band.CRACK, True), (Band.GOOD, True), (Band.MARGINAL, True), (Band.TUMBLE, False),
    ])
    def test_answerable(self, band, answerable):
        assert Day(D, band, None).answerable is answerable

    @pytest.mark.parametrize("outcome,answered", [
        (None, False), (Outcome.DRY, True), (Outcome.DAMP, True), (Outcome.SKIP, True),
    ])
    def test_answered(self, outcome, answered):
        assert Day(D, Band.GOOD, outcome).answered is answered

    @pytest.mark.parametrize("band,outcome,correct", [
        (Band.CRACK,    Outcome.DRY,  True),
        (Band.GOOD,     Outcome.DRY,  True),
        (Band.GOOD,     Outcome.DAMP, False),
        (Band.MARGINAL, Outcome.DAMP, True),
        (Band.MARGINAL, Outcome.DRY,  False),
        (Band.TUMBLE,   Outcome.DAMP, True),
        (Band.GOOD,     Outcome.SKIP, None),
        (Band.GOOD,     None,         None),
    ])
    def test_correct(self, band, outcome, correct):
        assert Day(D, band, outcome).correct is correct

    def test_accuracy_ignores_skips_and_unanswered(self):
        days = [Day(D, Band.GOOD, Outcome.DRY), Day(D, Band.GOOD, Outcome.DAMP),
                Day(D, Band.GOOD, Outcome.SKIP), Day(D, Band.GOOD, None)]
        assert accuracy(days) == Accuracy(correct=1, total=2)


# ---------------------------------------------------------------------------
# recent_accuracy
# ---------------------------------------------------------------------------

class TestRecentAccuracy:

    def test_none_when_no_file(self, log):
        assert log.recent_accuracy() is None

    def test_none_when_fewer_than_3_results(self, log):
        _log_with(log, [("2026-05-30", Band.GOOD, "dry"), ("2026-05-31", Band.GOOD, "dry")])
        assert log.recent_accuracy() is None

    def test_correct_when_good_and_dried(self, log):
        _log_with(log, [("2026-05-28", Band.GOOD, "dry"), ("2026-05-29", Band.CRACK, "dry"),
                        ("2026-05-30", Band.GOOD, "dry")])
        assert log.recent_accuracy() == (3, 3)

    def test_correct_when_marginal_and_damp(self, log):
        _log_with(log, [("2026-05-28", Band.MARGINAL, "damp"), ("2026-05-29", Band.MARGINAL, "damp"),
                        ("2026-05-30", Band.GOOD, "dry")])
        assert log.recent_accuracy() == (3, 3)

    def test_skip_outcomes_not_counted(self, log):
        _log_with(log, [("2026-05-28", Band.GOOD, "dry"), ("2026-05-29", Band.GOOD, "skip"),
                        ("2026-05-30", Band.GOOD, "dry"), ("2026-05-31", Band.GOOD, "dry")])
        assert log.recent_accuracy() == (3, 3)

    def test_limits_to_last_n_results(self, log):
        _log_with(log, [(f"2026-05-{n}", Band.GOOD, "damp") for n in (25, 26, 27)] +
                       [(f"2026-05-{n}", Band.GOOD, "dry") for n in (28, 29, 30)])
        assert log.recent_accuracy(n=3) == (3, 3)
