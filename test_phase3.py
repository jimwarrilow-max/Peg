"""
Tests for Phase 3 components: messages.py and log.py.
notify.py transport is tested via mocking (see test_fetch.py pattern).
"""

from __future__ import annotations

import csv
import os
import tempfile
from datetime import date

import pytest

from messages import _fmt_hour, _is_cold, format_message
from scorer import Band, HourForecast, ScoreResult, WindowConfig
from log import LOG_PATH, _row_exists, append_prediction


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _result(
    raw_score: float = 75.0,
    band: Band = Band.GOOD,
    will_dry: bool = True,
    override: bool = False,
    skipped: bool = False,
    best_window: tuple | None = (9, 14),
    gust_flag: bool = False,
) -> ScoreResult:
    from scorer import round_display
    return ScoreResult(
        raw_score=raw_score,
        display_score=round_display(raw_score),
        band=band,
        will_dry=will_dry,
        override=override,
        best_window=best_window,
        gust_flag=gust_flag,
        skipped=skipped,
        reason="Good drying conditions across the window.",
    )


def _hours(n: int = 24, temp_c: float = 18.0, rh_pct: float = 60.0) -> list[HourForecast]:
    return [
        HourForecast(
            hour=i, temp_c=temp_c, rh_pct=rh_pct,
            vpd_kpa=0.7, wind_mph=8.0, solar_wm2=300.0,
            precip_mm=0.0, precip_prob_pct=5.0,
        )
        for i in range(n)
    ]


def _cfg(hang: int = 9, bring_in: int = 18, dusk: int = 21) -> WindowConfig:
    return WindowConfig(hang_hour=hang, bring_in_hour=bring_in, dusk_hour=dusk)


# ---------------------------------------------------------------------------
# messages.py — format_message
# ---------------------------------------------------------------------------

class TestFormatMessage:

    def test_skipped_message(self):
        msg = format_message(_result(skipped=True), 9, 18, 21, _hours())
        assert "blank" in msg
        assert "no verdict" in msg

    def test_crack_open_contains_score(self):
        msg = format_message(_result(raw_score=85.0, band=Band.CRACK), 9, 18, 21, _hours())
        assert "85" in msg
        assert "belter" in msg.lower()

    def test_good_contains_score(self):
        msg = format_message(_result(raw_score=65.0, band=Band.GOOD), 9, 18, 21, _hours())
        assert "65" in msg
        assert "solid" in msg.lower()

    def test_marginal_contains_score(self):
        msg = format_message(_result(raw_score=45.0, band=Band.MARGINAL, will_dry=False), 9, 18, 21, _hours())
        assert "45" in msg
        assert "fence" in msg.lower()

    def test_tumble_contains_score(self):
        msg = format_message(_result(raw_score=20.0, band=Band.TUMBLE, will_dry=False), 9, 18, 21, _hours())
        assert "20" in msg
        assert "don't bother" in msg.lower()

    def test_override_message(self):
        # Make the final 2h of the window rain-gated
        hours = _hours()
        for i in (17, 18):
            hours[i] = HourForecast(
                hour=i, temp_c=18.0, rh_pct=70.0,
                vpd_kpa=0.5, wind_mph=5.0, solar_wm2=100.0,
                precip_mm=0.0, precip_prob_pct=80.0,
            )
        msg = format_message(_result(override=True, band=Band.MARGINAL), 9, 18, 21, hours)
        assert "cautious" in msg.lower()
        assert "⚠" in msg

    def test_override_tumble_shows_tumble_not_override(self):
        """Override + TUMBLE band → show tumble message, not the misleading override warning."""
        hours = _hours()
        hours[17] = HourForecast(
            hour=17, temp_c=15.0, rh_pct=90.0,
            vpd_kpa=0.1, wind_mph=5.0, solar_wm2=50.0,
            precip_mm=0.0, precip_prob_pct=80.0,
        )
        msg = format_message(_result(override=True, raw_score=2.0, band=Band.TUMBLE, will_dry=False), 9, 18, 21, hours)
        assert "cautious" not in msg.lower()
        assert "don't bother" in msg.lower()

    def test_override_shows_score(self):
        hours = _hours()
        hours[17] = HourForecast(
            hour=17, temp_c=18.0, rh_pct=70.0,
            vpd_kpa=0.5, wind_mph=5.0, solar_wm2=100.0,
            precip_mm=0.0, precip_prob_pct=80.0,
        )
        msg = format_message(_result(override=True, raw_score=65.0, band=Band.MARGINAL), 9, 18, 21, hours)
        assert "65" in msg

    def test_conditions_line_present(self):
        """All non-skipped messages include temperature, wind, and humidity."""
        msg = format_message(_result(raw_score=75.0, band=Band.GOOD), 9, 18, 21, _hours())
        assert "°C" in msg
        assert "mph" in msg
        assert "humidity" in msg

    def test_override_never_shows_good_band(self):
        """INV-07 reflected in the message: override → no 'Good drying day' wording."""
        hours = _hours()
        hours[17] = HourForecast(
            hour=17, temp_c=18.0, rh_pct=70.0,
            vpd_kpa=0.5, wind_mph=5.0, solar_wm2=100.0,
            precip_mm=0.5, precip_prob_pct=90.0,
        )
        msg = format_message(_result(override=True, raw_score=82.0, band=Band.MARGINAL), 9, 18, 21, hours)
        assert "Good drying day" not in msg
        assert "Crack open the pegs" not in msg

    def test_cold_gloat_appended_on_cold_crack_day(self):
        """Cold + Crack → gloat line appended."""
        cold_hours = _hours(temp_c=10.0)
        msg = format_message(_result(raw_score=90.0, band=Band.CRACK), 9, 18, 21, cold_hours)
        assert "Nippy" in msg

    def test_no_gloat_on_warm_crack_day(self):
        warm_hours = _hours(temp_c=22.0)
        msg = format_message(_result(raw_score=90.0, band=Band.CRACK), 9, 18, 21, warm_hours)
        assert "Nippy" not in msg

    def test_message_is_string(self):
        msg = format_message(_result(), 9, 18, 21, _hours())
        assert isinstance(msg, str)
        assert len(msg) > 0

    @pytest.mark.parametrize("h, expected", [
        (0,  "midnight"),
        (9,  "9am"),
        (12, "12pm"),
        (13, "1pm"),
        (18, "6pm"),
        (23, "11pm"),
    ])
    def test_fmt_hour(self, h, expected):
        assert _fmt_hour(h) == expected

    def test_html_bold_present(self):
        """Messages use HTML bold tags for Telegram."""
        msg = format_message(_result(raw_score=85.0, band=Band.CRACK), 9, 18, 21, _hours())
        assert "<b>" in msg and "</b>" in msg


# ---------------------------------------------------------------------------
# log.py — append_prediction
# ---------------------------------------------------------------------------

class TestLog:

    def _write_and_read(self, today, result, cfg, hours, tmp_path):
        log_path = str(tmp_path / "test_log.csv")
        append_prediction(today, result, cfg, hours, log_path=log_path)
        with open(log_path, newline="") as f:
            rows = list(csv.DictReader(f))
        return rows, log_path

    def test_creates_file_with_header(self, tmp_path):
        rows, log_path = self._write_and_read(
            date(2026, 5, 30), _result(), _cfg(), _hours(), tmp_path
        )
        assert os.path.isfile(log_path)
        assert len(rows) == 1

    def test_row_contains_date(self, tmp_path):
        rows, _ = self._write_and_read(
            date(2026, 5, 30), _result(), _cfg(), _hours(), tmp_path
        )
        assert rows[0]["date"] == "2026-05-30"

    def test_row_contains_band(self, tmp_path):
        rows, _ = self._write_and_read(
            date(2026, 5, 30), _result(band=Band.GOOD), _cfg(), _hours(), tmp_path
        )
        assert rows[0]["band"] == Band.GOOD.value

    def test_row_contains_raw_score(self, tmp_path):
        rows, _ = self._write_and_read(
            date(2026, 5, 30), _result(raw_score=72.5), _cfg(), _hours(), tmp_path
        )
        assert float(rows[0]["raw_score"]) == pytest.approx(72.5)

    def test_outcome_column_empty_on_write(self, tmp_path):
        rows, _ = self._write_and_read(
            date(2026, 5, 30), _result(), _cfg(), _hours(), tmp_path
        )
        assert rows[0]["outcome"] == ""

    def test_idempotent_same_day(self, tmp_path):
        """Calling append twice for the same date produces exactly one row (INT-09)."""
        log_path = str(tmp_path / "test_log.csv")
        append_prediction(date(2026, 5, 30), _result(), _cfg(), _hours(), log_path=log_path)
        append_prediction(date(2026, 5, 30), _result(), _cfg(), _hours(), log_path=log_path)
        with open(log_path, newline="") as f:
            rows = list(csv.DictReader(f))
        assert len(rows) == 1

    def test_different_days_append_separate_rows(self, tmp_path):
        log_path = str(tmp_path / "test_log.csv")
        append_prediction(date(2026, 5, 30), _result(), _cfg(), _hours(), log_path=log_path)
        append_prediction(date(2026, 5, 31), _result(), _cfg(), _hours(), log_path=log_path)
        with open(log_path, newline="") as f:
            rows = list(csv.DictReader(f))
        assert len(rows) == 2
        assert rows[0]["date"] == "2026-05-30"
        assert rows[1]["date"] == "2026-05-31"

    def test_window_stats_written(self, tmp_path):
        """Mean weather stats over the window are logged for calibration."""
        rows, _ = self._write_and_read(
            date(2026, 5, 30), _result(), _cfg(), _hours(temp_c=15.0), tmp_path
        )
        assert float(rows[0]["mean_temp_c"]) == pytest.approx(15.0)

    def test_best_window_formatted(self, tmp_path):
        rows, _ = self._write_and_read(
            date(2026, 5, 30),
            _result(best_window=(9, 13)),
            _cfg(), _hours(), tmp_path
        )
        assert rows[0]["best_window"] == "09:00-13:00"

    def test_no_best_window_is_empty_string(self, tmp_path):
        rows, _ = self._write_and_read(
            date(2026, 5, 30),
            _result(best_window=None),
            _cfg(), _hours(), tmp_path
        )
        assert rows[0]["best_window"] == ""

    def test_skipped_row_written(self, tmp_path):
        """Even a skipped day gets a log row (so gaps are visible)."""
        rows, _ = self._write_and_read(
            date(2026, 5, 30),
            _result(skipped=True, raw_score=0.0, band=Band.TUMBLE, will_dry=False),
            _cfg(), _hours(), tmp_path
        )
        assert rows[0]["skipped"] == "True"
