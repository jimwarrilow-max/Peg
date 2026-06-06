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


def _hours_with_uv(uv: float = 5.0) -> list[HourForecast]:
    return [
        HourForecast(
            hour=i, temp_c=18.0, rh_pct=60.0,
            vpd_kpa=0.7, wind_mph=8.0, solar_wm2=300.0,
            precip_mm=0.0, precip_prob_pct=5.0, uv_index=uv,
        )
        for i in range(24)
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

    @pytest.mark.parametrize("band, raw, will_dry, keyword", [
        (Band.CRACK,    85.0, True,  "belter"),
        (Band.GOOD,     65.0, True,  "solid"),
        (Band.MARGINAL, 45.0, False, "fence"),
        (Band.TUMBLE,   20.0, False, "don't bother"),
    ])
    def test_band_message_contains_score_and_keyword(self, band, raw, will_dry, keyword):
        """Each band produces the right voice copy and includes the score."""
        msg = format_message(_result(raw_score=raw, band=band, will_dry=will_dry), 9, 18, 21, _hours())
        assert str(int(round(raw / 5) * 5)) in msg
        assert keyword in msg.lower()

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

    def test_override_tumble_shows_tumble_with_rain_info(self):
        """Override + TUMBLE → tumble message that still mentions the rain timing and shows score."""
        hours = _hours()
        hours[17] = HourForecast(
            hour=17, temp_c=15.0, rh_pct=90.0,
            vpd_kpa=0.1, wind_mph=5.0, solar_wm2=50.0,
            precip_mm=0.0, precip_prob_pct=80.0,
        )
        msg = format_message(_result(override=True, raw_score=2.0, band=Band.TUMBLE, will_dry=False), 9, 18, 21, hours)
        assert "cautious" not in msg.lower()
        assert "don't bother" in msg.lower()
        assert "0" in msg           # score shown
        assert "rain" in msg.lower()  # rain mentioned

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

    def test_rain_line_shown_when_rain_in_window(self):
        """Rain timing line appears when a window hour is rain-gated."""
        hours = _hours()
        hours[15] = HourForecast(
            hour=15, temp_c=15.0, rh_pct=80.0,
            vpd_kpa=0.3, wind_mph=8.0, solar_wm2=100.0,
            precip_mm=0.0, precip_prob_pct=70.0,
        )
        msg = format_message(_result(raw_score=65.0, band=Band.GOOD), 9, 18, 21, hours)
        assert "🌧️" in msg
        assert "70%" in msg

    def test_rain_line_absent_when_no_rain(self):
        """No rain line on a clear day."""
        msg = format_message(_result(raw_score=90.0, band=Band.CRACK), 9, 18, 21, _hours())
        assert "🌧️" not in msg

    def test_rain_from_start_of_window(self):
        """When rain starts at hang hour, shows 'Rain from 9am' without dry prefix."""
        hours = _hours()
        hours[9] = HourForecast(
            hour=9, temp_c=15.0, rh_pct=80.0,
            vpd_kpa=0.3, wind_mph=8.0, solar_wm2=100.0,
            precip_mm=0.0, precip_prob_pct=80.0,
        )
        msg = format_message(_result(raw_score=20.0, band=Band.TUMBLE, will_dry=False), 9, 18, 21, hours)
        assert "Rain from 9am" in msg
        assert "Dry till" not in msg

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
        msg = format_message(_result(raw_score=90.0, band=Band.CRACK), 9, 18, 21, _hours(temp_c=10.0))
        assert "Nippy" in msg

    def test_no_gloat_on_warm_crack_day(self):
        msg = format_message(_result(raw_score=90.0, band=Band.CRACK), 9, 18, 21, _hours(temp_c=22.0))
        assert "Nippy" not in msg

    def test_html_bold_present(self):
        """Messages use HTML bold tags for Telegram."""
        msg = format_message(_result(raw_score=85.0, band=Band.CRACK), 9, 18, 21, _hours())
        assert "<b>" in msg and "</b>" in msg

    def test_uv_line_shown_when_uv_present(self):
        """UV index line appears when window hours have UV data."""
        msg = format_message(_result(raw_score=75.0, band=Band.GOOD), 9, 18, 21, _hours_with_uv(uv=5.0))
        assert "☀️" in msg
        assert "UV" in msg
        assert "Moderate" in msg

    def test_uv_line_absent_when_no_uv_data(self):
        """No UV line when uv_index is None for all hours."""
        msg = format_message(_result(raw_score=75.0, band=Band.GOOD), 9, 18, 21, _hours())
        assert "☀️" not in msg

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

    def test_row_has_expected_fields(self, tmp_path):
        """Date, band and raw_score are all written correctly in the same row."""
        rows, _ = self._write_and_read(
            date(2026, 5, 30), _result(band=Band.GOOD, raw_score=72.5), _cfg(), _hours(), tmp_path
        )
        assert rows[0]["date"] == "2026-05-30"
        assert rows[0]["band"] == Band.GOOD.value
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
            date(2026, 5, 30), _result(best_window=(9, 13)), _cfg(), _hours(), tmp_path
        )
        assert rows[0]["best_window"] == "09:00-13:00"

    def test_no_best_window_is_empty_string(self, tmp_path):
        rows, _ = self._write_and_read(
            date(2026, 5, 30), _result(best_window=None), _cfg(), _hours(), tmp_path
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
