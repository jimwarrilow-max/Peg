"""
Tests for messages.py (the Prediction Log has its own tests).
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
    near_rain_hour: int | None = None,
    near_rain_prob: float | None = None,
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
        near_rain_hour=near_rain_hour,
        near_rain_prob=near_rain_prob,
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
        assert "Good drying" not in msg
        assert "Drying conditions hold" in msg

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

    def test_override_marginal_shows_best_window_tip(self):
        """Override MARGINAL with best_window set → shows window tip."""
        msg = format_message(
            _result(override=True, band=Band.MARGINAL, first_rain_hour=17, best_window=(11, 15)),
            9, 18, 21,
        )
        assert "11am" in msg
        assert "3pm" in msg

    def test_override_marginal_no_window_tip_when_best_window_none(self):
        """Override MARGINAL with no best_window → no window tip."""
        msg = format_message(
            _result(override=True, band=Band.MARGINAL, first_rain_hour=17, best_window=None),
            9, 18, 21,
        )
        assert "Best window" not in msg

    def test_near_rain_line_shown_when_set(self):
        """Near-rain warning appears when near_rain_hour/prob are set."""
        msg = format_message(
            _result(raw_score=75.0, band=Band.GOOD, near_rain_hour=14, near_rain_prob=35.0),
            9, 18, 21,
        )
        assert "⚠️ Note:" in msg
        assert "35%" in msg
        assert "2pm" in msg

    def test_near_rain_line_absent_when_none(self):
        """No near-rain warning when near_rain_hour is None."""
        msg = format_message(_result(raw_score=75.0, band=Band.GOOD), 9, 18, 21)
        assert "⚠️ Note:" not in msg

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
