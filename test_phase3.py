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

from messages import _fmt_hour, _uv_label, format_message
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
    first_rain_hour: int | None = None,
    window_rain_hour: int | None = None,
    window_rain_prob: float | None = None,
    mean_temp_c: float | None = 18.0,
    mean_wind_mph: float | None = 8.0,
    mean_rh_pct: float | None = 60.0,
    peak_uv: float | None = None,
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
        first_rain_hour=first_rain_hour,
        window_rain_hour=window_rain_hour,
        window_rain_prob=window_rain_prob,
        mean_temp_c=mean_temp_c,
        mean_wind_mph=mean_wind_mph,
        mean_rh_pct=mean_rh_pct,
        peak_uv=peak_uv,
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
        msg = format_message(_result(skipped=True), 9, 18, 21)
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
        msg = format_message(_result(raw_score=raw, band=band, will_dry=will_dry), 9, 18, 21)
        assert str(int(round(raw / 5) * 5)) in msg
        assert keyword in msg.lower()

    def test_override_message(self):
        msg = format_message(
            _result(override=True, band=Band.MARGINAL, first_rain_hour=17),
            9, 18, 21,
        )
        assert "cautious" in msg.lower()
        assert "⚠" in msg

    def test_override_tumble_shows_tumble_with_rain_info(self):
        """Override + TUMBLE → tumble message that still mentions the rain timing and shows score."""
        msg = format_message(
            _result(override=True, raw_score=2.0, band=Band.TUMBLE, will_dry=False, first_rain_hour=17),
            9, 18, 21,
        )
        assert "cautious" not in msg.lower()
        assert "don't bother" in msg.lower()
        assert "0" in msg
        assert "rain" in msg.lower()

    def test_override_shows_score(self):
        msg = format_message(
            _result(override=True, raw_score=65.0, band=Band.MARGINAL, first_rain_hour=17),
            9, 18, 21,
        )
        assert "65" in msg

    def test_override_timing_uses_window_rain_hour(self):
        """Override headline uses window_rain_hour (full window) when available."""
        msg = format_message(
            _result(override=True, band=Band.MARGINAL, window_rain_hour=15, first_rain_hour=17),
            9, 18, 21,
        )
        assert "3pm" in msg

    def test_override_timing_falls_back_to_first_rain_hour(self):
        """Override headline falls back to first_rain_hour when window_rain_hour is absent."""
        msg = format_message(
            _result(override=True, band=Band.MARGINAL, window_rain_hour=None, first_rain_hour=17),
            9, 18, 21,
        )
        assert "5pm" in msg

    def test_conditions_line_present(self):
        """All non-skipped messages include temperature, wind, and humidity."""
        msg = format_message(_result(raw_score=75.0, band=Band.GOOD), 9, 18, 21)
        assert "°C" in msg
        assert "mph" in msg
        assert "humidity" in msg

    def test_rain_line_shown_when_rain_in_window(self):
        """Rain timing line appears when window_rain_hour is set on result."""
        msg = format_message(
            _result(raw_score=65.0, band=Band.GOOD, window_rain_hour=15, window_rain_prob=70.0),
            9, 18, 21,
        )
        assert "🌧️" in msg
        assert "70%" in msg

    def test_rain_line_shows_dry_till_prefix_when_rain_after_hang_hour(self):
        """'Dry till X · Rain from Y' format when rain arrives after hang hour."""
        msg = format_message(
            _result(raw_score=65.0, band=Band.GOOD, window_rain_hour=15, window_rain_prob=70.0),
            9, 18, 21,
        )
        assert "Dry till 2pm" in msg
        assert "Rain from 3pm" in msg

    def test_rain_line_omits_probability_when_prob_none(self):
        """Rain gated by mm threshold only (prob=None) → no probability shown."""
        msg = format_message(
            _result(raw_score=65.0, band=Band.GOOD, window_rain_hour=15, window_rain_prob=None),
            9, 18, 21,
        )
        assert "🌧️" in msg
        assert "(%" not in msg   # no parenthetical probability on the rain line

    def test_rain_line_absent_when_no_rain(self):
        """No rain line on a clear day."""
        msg = format_message(_result(raw_score=90.0, band=Band.CRACK), 9, 18, 21)
        assert "🌧️" not in msg

    def test_rain_from_start_of_window(self):
        """When rain starts at hang hour, shows 'Rain from 9am' without dry prefix."""
        msg = format_message(
            _result(raw_score=20.0, band=Band.TUMBLE, will_dry=False, window_rain_hour=9, window_rain_prob=80.0),
            9, 18, 21,
        )
        assert "Rain from 9am" in msg
        assert "Dry till" not in msg

    def test_override_never_shows_good_band(self):
        """INV-07 reflected in the message: override → no 'Good drying day' wording."""
        msg = format_message(
            _result(override=True, raw_score=82.0, band=Band.MARGINAL, first_rain_hour=17),
            9, 18, 21,
        )
        assert "Good drying day" not in msg
        assert "Crack open the pegs" not in msg

    def test_hang_advice_out_by_when_window_starts_at_hang_hour(self):
        """best_window starts at hang_hour → 'Out by 9am'."""
        msg = format_message(_result(raw_score=85.0, band=Band.CRACK, best_window=(9, 14)), 9, 18, 21)
        assert "Out by 9am" in msg

    def test_hang_advice_hold_off_when_window_starts_late(self):
        """best_window starts after hang_hour → 'Hold off till 1pm'."""
        msg = format_message(_result(raw_score=65.0, band=Band.GOOD, best_window=(13, 17)), 9, 18, 21)
        assert "Hold off till 1pm" in msg
        assert "in by 5pm" in msg

    def test_marginal_window_tip_present_when_best_window_exists(self):
        msg = format_message(_result(raw_score=45.0, band=Band.MARGINAL, best_window=(11, 15)), 9, 18, 21)
        assert "11am" in msg
        assert "3pm" in msg

    def test_marginal_no_window_tip_when_best_window_none(self):
        msg = format_message(_result(raw_score=40.0, band=Band.MARGINAL, best_window=None), 9, 18, 21)
        assert "Best window" not in msg

    def test_cold_gloat_appended_on_cold_crack_day(self):
        """Cold + Crack → gloat line appended."""
        msg = format_message(_result(raw_score=90.0, band=Band.CRACK, mean_temp_c=10.0), 9, 18, 21)
        assert "Nippy" in msg

    def test_no_gloat_on_warm_crack_day(self):
        msg = format_message(_result(raw_score=90.0, band=Band.CRACK, mean_temp_c=22.0), 9, 18, 21)
        assert "Nippy" not in msg

    def test_html_bold_present(self):
        """Messages use HTML bold tags for Telegram."""
        msg = format_message(_result(raw_score=85.0, band=Band.CRACK), 9, 18, 21)
        assert "<b>" in msg and "</b>" in msg

    def test_uv_line_shown_when_uv_present(self):
        """UV index line appears when peak_uv is set on result."""
        msg = format_message(_result(raw_score=75.0, band=Band.GOOD, peak_uv=5.0), 9, 18, 21)
        assert "☀️" in msg
        assert "UV" in msg
        assert "Moderate" in msg

    def test_uv_line_absent_when_no_uv_data(self):
        """No UV line when peak_uv is None."""
        msg = format_message(_result(raw_score=75.0, band=Band.GOOD, peak_uv=None), 9, 18, 21)
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

    @pytest.mark.parametrize("uv, expected_label", [
        (0.0,  "Low"),
        (2.9,  "Low"),
        (3.0,  "Moderate"),
        (5.9,  "Moderate"),
        (6.0,  "High"),
        (7.9,  "High"),
        (8.0,  "Very High"),
        (10.9, "Very High"),
        (11.0, "Extreme"),
        (15.0, "Extreme"),
    ])
    def test_uv_label_thresholds(self, uv, expected_label):
        """Each WHO UV band boundary maps to the correct label."""
        assert _uv_label(uv) == expected_label


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

    def test_window_stats_all_fields_written(self, tmp_path):
        """All _window_stats fields (mean and peak) are present and non-empty in the log row."""
        rows, _ = self._write_and_read(
            date(2026, 5, 30), _result(), _cfg(), _hours(temp_c=15.0), tmp_path
        )
        row = rows[0]
        # _hours: temp_c=15.0, rh_pct=60.0, vpd_kpa=0.7, wind_mph=8.0, solar_wm2=300.0
        assert float(row["mean_temp_c"])    == pytest.approx(15.0)
        assert float(row["mean_rh_pct"])    == pytest.approx(60.0)
        assert float(row["mean_vpd_kpa"])   == pytest.approx(0.7)
        assert float(row["mean_wind_mph"])  == pytest.approx(8.0)
        assert float(row["mean_solar_wm2"]) == pytest.approx(300.0)
        assert row["max_uv_index"] == ""    # _hours sets no uv_index → None → empty

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
