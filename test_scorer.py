"""
Invariant tests INV-01 → INV-09 for the Peg scorer.

Also includes the baseline SCORE fixture tests from §2 of the test-cases doc
so the scorer can be validated end-to-end before running the invariant suite.
"""

from __future__ import annotations

import pytest

from hypothesis import given, settings, assume
from hypothesis import strategies as st

from scorer import (
    Band,
    HourForecast,
    ScoreResult,
    WindowConfig,
    DRY_TARGET,
    LATE_RAIN_HOURS,
    band_from_raw,
    round_display,
    compute_vpd,
    score,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _hour(
    h: int = 10,
    vpd: float = 0.8,
    wind: float = 8.0,
    solar: float = 300.0,
    precip_mm: float = 0.0,
    precip_prob: float = 0.0,
    gust: float | None = None,
    temp_c: float = 15.0,
    rh_pct: float = 60.0,
) -> HourForecast:
    return HourForecast(
        hour=h,
        temp_c=temp_c,
        rh_pct=rh_pct,
        vpd_kpa=vpd,
        wind_mph=wind,
        solar_wm2=solar,
        precip_mm=precip_mm,
        precip_prob_pct=precip_prob,
        wind_gust_mph=gust,
    )


def _window(hang: int = 8, bring_in: int = 17, dusk: int = 20) -> WindowConfig:
    return WindowConfig(hang_hour=hang, bring_in_hour=bring_in, dusk_hour=dusk)


def _day_of(
    n_hours: int = 8,
    vpd: float = 0.8,
    wind: float = 8.0,
    solar: float = 300.0,
    precip_mm: float = 0.0,
    precip_prob: float = 0.0,
    start_hour: int = 8,
    **kw,
) -> list[HourForecast]:
    return [
        _hour(h=start_hour + i, vpd=vpd, wind=wind, solar=solar,
              precip_mm=precip_mm, precip_prob=precip_prob, **kw)
        for i in range(n_hours)
    ]


def _make_day_for_score(
    target_raw: float,
    n_hours: int = 8,
    hang: int = 8,
) -> tuple[list[HourForecast], WindowConfig]:
    """
    Build exactly n_hours of forecasts that produce target_raw from the scorer.

    Formula: score = 50 × (n × p) / DRY_TARGET  →  p = target_raw × DRY_TARGET / (50 × n)

    Two regimes (no channel exceeds its clamp ceiling):
      p < 0.5  → wind=0, solar=0:  potential = 0.5×vpd_s + 0.075  (wind-floor only)
      p ≥ 0.5  → wind=12, solar=450: potential = 0.5×vpd_s + 0.5

    p = 0 → all-rain-gated hours (only way to suppress the wind floor).
    """
    cfg = _window(hang=hang, bring_in=hang + n_hours - 1, dusk=23)
    if target_raw == 0.0:
        hours = [_hour(h=hang + i, precip_prob=100.0) for i in range(n_hours)]
        return hours, cfg
    p = target_raw * DRY_TARGET / (50.0 * n_hours)
    if p < 0.5:
        vpd_s = max(0.0, (p - 0.075) / 0.5)
        hours = [_hour(h=hang + i, vpd=vpd_s, wind=0.0, solar=0.0) for i in range(n_hours)]
    else:
        vpd_s = min(1.0, 2.0 * (p - 0.5))
        hours = [_hour(h=hang + i, vpd=vpd_s, wind=12.0, solar=450.0) for i in range(n_hours)]
    return hours, cfg


# ---------------------------------------------------------------------------
# Baseline fixture tests (SCORE-02 → SCORE-14)
# These pin concrete expected values and are re-baselined when weights change.
# ---------------------------------------------------------------------------

class TestBaselineFixtures:

    def test_SCORE02_still_air_wind_floor(self):
        """wind = 0 mph → wind sub-score = 0.10 (not 0)"""
        # All VPD and solar = 0; only wind floor contributes
        # hourly = 0.5×0 + 0.3×0.10 + 0.2×0 = 0.03 per hour
        # cumulative = 8 × 0.03 = 0.24  →  score = 50 × 0.24/4.0 = 3.0
        hours = _day_of(n_hours=8, vpd=0.0, wind=0.0, solar=0.0)
        result = score(hours, _window())
        assert result.raw_score == pytest.approx(3.0)
        assert not result.skipped

    def test_SCORE03_wind_saturation(self):
        """wind = 16 mph → wind sub-score clamps to 1.0 (not 1.25)"""
        r16 = score(_day_of(n_hours=8, vpd=0.0, wind=16.0, solar=0.0), _window())
        r12 = score(_day_of(n_hours=8, vpd=0.0, wind=12.0, solar=0.0), _window())
        assert r16.raw_score == pytest.approx(r12.raw_score)

    def test_SCORE04_vpd_clamp(self):
        """VPD = 1.5 kPa → vpd sub-score = 1.0 (clamped)"""
        r15 = score(_day_of(n_hours=8, vpd=1.5, wind=0.0, solar=0.0), _window())
        r10 = score(_day_of(n_hours=8, vpd=1.0, wind=0.0, solar=0.0), _window())
        assert r15.raw_score == pytest.approx(r10.raw_score)

    def test_SCORE05_weighted_mix(self):
        """vpd=0.6 / wind=8mph / solar=225 → hourly_potential = 0.61"""
        # vpd_s=0.6, wind_s=0.10+8/(12/0.9)=0.10+0.6=0.70, solar_s=225/450=0.5
        # hourly = 0.5×0.6 + 0.3×0.70 + 0.2×0.5 = 0.61; cumulative = 4.88
        # score = clamp(50 × 4.88/4.0, 0, 100) = 61.0
        hours = _day_of(n_hours=8, vpd=0.6, wind=8.0, solar=225.0)
        result = score(hours, _window(hang=8, bring_in=15, dusk=20))
        assert result.raw_score == pytest.approx(61.0)

    def test_SCORE06_perfect_day_hits_towel_bar(self):
        """4 perfect hours → cumulative 4.0 → score 50, will_dry True, band Marginal"""
        hours = _day_of(n_hours=4, vpd=1.0, wind=12.0, solar=450.0, start_hour=8)
        result = score(hours, _window(hang=8, bring_in=11, dusk=20))
        assert result.raw_score == pytest.approx(50.0)
        assert result.will_dry is True
        assert result.band == Band.MARGINAL  # 50 → Marginal (35 ≤ 50 < 55)

    def test_SCORE07_double_margin(self):
        """8 perfect hours → cumulative 8.0 → score clamps to 100"""
        hours = _day_of(n_hours=8, vpd=1.0, wind=12.0, solar=450.0)
        result = score(hours, _window(hang=8, bring_in=15, dusk=20))
        assert result.raw_score == pytest.approx(100.0)
        assert result.band == Band.CRACK

    def test_SCORE08_will_dry_boundary(self):
        """cumulative 3.99 → will_dry False; cumulative 4.00 → will_dry True"""
        # vpd=1.0, wind=12, solar=0: hourly potential = 0.80
        # 5h × 0.80 = 4.0 → True; 4h × 0.80 = 3.2 → False
        r5 = score(_day_of(n_hours=5, vpd=1.0, wind=12.0, solar=0.0), _window(hang=8, bring_in=12, dusk=20))
        r4 = score(_day_of(n_hours=4, vpd=1.0, wind=12.0, solar=0.0), _window(hang=8, bring_in=11, dusk=20))
        assert r5.will_dry is True
        assert r4.will_dry is False

    def test_SCORE10_rain_gate_boundaries(self):
        """precip_prob >40 gates; =40 does not. precip_mm >0.2 gates; =0.2 does not."""
        cfg = _window(hang=8, bring_in=8, dusk=20)
        h_40  = _hour(h=8, vpd=1.0, wind=12.0, solar=450.0, precip_prob=40.0, precip_mm=0.0)
        h_41  = _hour(h=8, vpd=1.0, wind=12.0, solar=450.0, precip_prob=41.0, precip_mm=0.0)
        h_02  = _hour(h=8, vpd=1.0, wind=12.0, solar=450.0, precip_prob=0.0,  precip_mm=0.2)
        h_021 = _hour(h=8, vpd=1.0, wind=12.0, solar=450.0, precip_prob=0.0,  precip_mm=0.21)
        assert score([h_40],  cfg).raw_score  > 0,             "40% prob should not be gated"
        assert score([h_41],  cfg).raw_score  == pytest.approx(0.0), "41% prob should be gated"
        assert score([h_02],  cfg).raw_score  > 0,             "0.2mm should not be gated"
        assert score([h_021], cfg).raw_score  == pytest.approx(0.0), "0.21mm should be gated"

    def test_SCORE11_all_day_rain(self):
        """Every hour gated → cumulative 0 → score 0 → Tumble-dryer"""
        hours = _day_of(n_hours=8, vpd=1.0, wind=12.0, solar=450.0, precip_prob=100.0)
        result = score(hours, _window())
        assert result.raw_score == pytest.approx(0.0)
        assert result.band == Band.TUMBLE

    def test_SCORE12_rain_dominates(self):
        """High VPD + wind but precip_prob 70% → that hour's potential = 0"""
        hours = [_hour(h=8, vpd=2.0, wind=20.0, solar=600.0, precip_prob=70.0)]
        result = score(hours, _window(hang=8, bring_in=8, dusk=20))
        assert result.raw_score == pytest.approx(0.0)

    def test_gust_flag_set_above_32mph(self):
        """A single gust over 32 mph sets gust_flag regardless of the overall score."""
        hours = _day_of(n_hours=8)
        hours[2] = _hour(h=10, gust=33.0)
        assert score(hours, _window()).gust_flag is True

    def test_gust_flag_clear_at_or_below_32mph(self):
        hours = _day_of(n_hours=8)
        hours[2] = _hour(h=10, gust=32.0)
        assert score(hours, _window()).gust_flag is False

    def test_too_many_missing_fields_skips(self):
        """More than 25% unscorable hours → skipped=True (UNSCORABLE_LIMIT)."""
        hours = _day_of(n_hours=8)
        # Make 3 of 8 hours (37.5% > 25%) unscorable by clearing their VPD
        for i in range(3):
            h = hours[i]
            hours[i] = HourForecast(
                hour=h.hour, temp_c=h.temp_c, rh_pct=h.rh_pct,
                vpd_kpa=None, wind_mph=h.wind_mph, solar_wm2=h.solar_wm2,
                precip_mm=h.precip_mm, precip_prob_pct=h.precip_prob_pct,
            )
        result = score(hours, _window())
        assert result.skipped is True

    def test_empty_window_skips(self):
        """hang_hour beyond dusk produces an empty window → skipped=True."""
        result = score([], WindowConfig(hang_hour=22, bring_in_hour=8, dusk_hour=6))
        assert result.skipped is True

    def test_missing_hours_count_as_unscorable(self):
        """Hours absent from the forecast list count toward the unscorable fraction."""
        # Window is 8–15 (8 hours); only hours 8–12 supplied → 3/8 = 37.5% > UNSCORABLE_LIMIT
        hours = _day_of(n_hours=5, start_hour=8)
        result = score(hours, _window(hang=8, bring_in=15, dusk=20))
        assert result.skipped is True

    def test_window_rain_hour_set_to_first_gated_hour(self):
        """score() sets window_rain_hour to the first rain-gated hour in the window."""
        hours = (
            [_hour(h=h, precip_prob=0.0)  for h in range(8, 12)] +
            [_hour(h=h, precip_prob=80.0) for h in range(12, 16)]
        )
        result = score(hours, _window(hang=8, bring_in=15, dusk=20))
        assert result.window_rain_hour == 12
        assert result.window_rain_prob == pytest.approx(80.0)

    def test_window_rain_hour_none_on_clear_day(self):
        """score() sets window_rain_hour to None when no hours are rain-gated."""
        result = score(_day_of(n_hours=8), _window())
        assert result.window_rain_hour is None
        assert result.window_rain_prob is None

    @pytest.mark.parametrize("raw, expected_display", [
        (72.3,  70),
        (73.0,  75),   # 73/5=14.6 → rounds to 15 → 75
        (72.5,  70),   # banker's round: 72.5/5=14.5 → rounds to 14 (even) → 70
        (37.5,  40),   # 37.5/5=7.5 → rounds to 8 (even) → 40
        (32.5,  30),   # 32.5/5=6.5 → rounds to 6 (even) → 30
        (0.0,    0),
        (50.0,  50),
        (100.0, 100),
    ])
    def test_SCORE13_rounding(self, raw, expected_display):
        """Display score is rounded to nearest 5 (tested via the public helper)."""
        assert round_display(raw) == expected_display

    @pytest.mark.parametrize("raw, expected_band", [
        # Half-open ranges: [0,35) Tumble · [35,55) Marginal · [55,80) Good · [80,100] Crack
        (0.0,    Band.TUMBLE),
        (34.0,   Band.TUMBLE),
        (34.999, Band.TUMBLE),
        (35.0,   Band.MARGINAL),
        (54.0,   Band.MARGINAL),
        (54.999, Band.MARGINAL),
        (55.0,   Band.GOOD),
        (79.0,   Band.GOOD),
        (79.999, Band.GOOD),
        (80.0,   Band.CRACK),
        (100.0,  Band.CRACK),
    ])
    def test_SCORE14_band_edges(self, raw, expected_band):
        """Band boundaries are evaluated on the raw score (tested via the public helper)."""
        assert band_from_raw(raw) == expected_band


# ---------------------------------------------------------------------------
# Invariant tests INV-01 → INV-09
# ---------------------------------------------------------------------------

_st_prob   = st.floats(min_value=0.0, max_value=100.0, allow_nan=False)
_st_vpd    = st.floats(min_value=0.0, max_value=3.0,   allow_nan=False)
_st_wind   = st.floats(min_value=0.0, max_value=50.0,  allow_nan=False)
_st_solar  = st.floats(min_value=0.0, max_value=900.0, allow_nan=False)
_st_precip = st.floats(min_value=0.0, max_value=20.0,  allow_nan=False)


@st.composite
def st_hour(draw, hour: int) -> HourForecast:
    return HourForecast(
        hour=hour,
        temp_c=draw(st.floats(min_value=-10.0, max_value=40.0, allow_nan=False)),
        rh_pct=draw(st.floats(min_value=0.0, max_value=100.0, allow_nan=False)),
        vpd_kpa=draw(_st_vpd),
        wind_mph=draw(_st_wind),
        solar_wm2=draw(_st_solar),
        precip_mm=draw(_st_precip),
        precip_prob_pct=draw(_st_prob),
        wind_gust_mph=draw(st.floats(min_value=0.0, max_value=80.0, allow_nan=False)),
    )


@st.composite
def st_day(draw, n_hours: int = 8, start: int = 8) -> list[HourForecast]:
    return [draw(st_hour(start + i)) for i in range(n_hours)]


def _standard_window() -> WindowConfig:
    return WindowConfig(hang_hour=8, bring_in_hour=15, dusk_hour=20)


class TestInvariants:

    @given(st_day())
    @settings(max_examples=500)
    def test_INV01_score_bounds(self, hours):
        """Score is always in [0, 100]."""
        result = score(hours, _standard_window())
        if not result.skipped:
            assert 0.0 <= result.raw_score <= 100.0

    @given(st_day())
    @settings(max_examples=500)
    def test_INV02_display_multiple_of_5(self, hours):
        """Displayed score is always a multiple of 5."""
        result = score(hours, _standard_window())
        if not result.skipped:
            assert result.display_score % 5 == 0

    @given(st_day(), st.integers(min_value=0, max_value=7))
    @settings(max_examples=500)
    def test_INV03_rain_never_increases_score(self, hours, idx):
        """Gating an hour with rain never makes the score go up."""
        assume(idx < len(hours))
        h = hours[idx]
        rainy_hour = HourForecast(
            hour=h.hour, temp_c=h.temp_c, rh_pct=h.rh_pct,
            vpd_kpa=h.vpd_kpa, wind_mph=h.wind_mph, solar_wm2=h.solar_wm2,
            precip_mm=h.precip_mm, precip_prob_pct=100.0,
            wind_gust_mph=h.wind_gust_mph,
        )
        r_before = score(hours,                                _standard_window())
        r_after  = score(hours[:idx] + [rainy_hour] + hours[idx+1:], _standard_window())
        if not r_before.skipped and not r_after.skipped:
            assert r_after.raw_score <= r_before.raw_score + 1e-9

    @given(st_day())
    @settings(max_examples=500)
    def test_INV04_more_wind_never_decreases_score(self, hours):
        """Replacing each hour's wind with max(original, 12mph) never decreases score."""
        hours_windier = [
            HourForecast(
                hour=h.hour, temp_c=h.temp_c, rh_pct=h.rh_pct,
                vpd_kpa=h.vpd_kpa, wind_mph=max(h.wind_mph or 0.0, 12.0),
                solar_wm2=h.solar_wm2, precip_mm=h.precip_mm,
                precip_prob_pct=h.precip_prob_pct, wind_gust_mph=h.wind_gust_mph,
            )
            for h in hours
        ]
        r_before = score(hours,         _standard_window())
        r_after  = score(hours_windier, _standard_window())
        if not r_before.skipped and not r_after.skipped:
            assert r_after.raw_score >= r_before.raw_score - 1e-9

    @given(st_day())
    @settings(max_examples=500)
    def test_INV05_lower_vpd_never_increases_score(self, hours):
        """Halving each hour's VPD never increases the score."""
        hours_drier = [
            HourForecast(
                hour=h.hour, temp_c=h.temp_c, rh_pct=h.rh_pct,
                vpd_kpa=(h.vpd_kpa or 0.0) / 2.0,
                wind_mph=h.wind_mph, solar_wm2=h.solar_wm2,
                precip_mm=h.precip_mm, precip_prob_pct=h.precip_prob_pct,
                wind_gust_mph=h.wind_gust_mph,
            )
            for h in hours
        ]
        r_before = score(hours,       _standard_window())
        r_after  = score(hours_drier, _standard_window())
        if not r_before.skipped and not r_after.skipped:
            assert r_after.raw_score <= r_before.raw_score + 1e-9

    @given(st_day())
    @settings(max_examples=500)
    def test_INV06_window_within_bounds(self, hours):
        """Best window start/end are always within the configured window bounds."""
        cfg = _standard_window()
        result = score(hours, cfg)
        if not result.skipped and result.best_window is not None:
            end_bound = min(cfg.bring_in_hour, cfg.dusk_hour)
            start, end = result.best_window
            assert start >= cfg.hang_hour
            assert end   <= end_bound
            assert start <= end

    @given(st_day())
    @settings(max_examples=500)
    def test_INV07_late_rain_caps_band(self, hours):
        """Rain in the final 2h of the window → band is never Good or Crack open pegs."""
        cfg = _standard_window()
        end_hour = min(cfg.bring_in_hour, cfg.dusk_hour)
        late_hours_set = set(range(end_hour - LATE_RAIN_HOURS + 1, end_hour + 1))
        forced = [
            HourForecast(
                hour=h.hour, temp_c=h.temp_c, rh_pct=h.rh_pct,
                vpd_kpa=h.vpd_kpa, wind_mph=h.wind_mph, solar_wm2=h.solar_wm2,
                precip_mm=100.0, precip_prob_pct=100.0,
                wind_gust_mph=h.wind_gust_mph,
            )
            if h.hour in late_hours_set else h
            for h in hours
        ]
        result = score(forced, cfg)
        if not result.skipped:
            assert result.band not in (Band.GOOD, Band.CRACK)

    @given(st_day())
    @settings(max_examples=500)
    def test_INV08_will_dry_iff_cumulative_meets_target(self, hours):
        """will_dry is True exactly when score >= 50 (the DRY_TARGET threshold)."""
        result = score(hours, _standard_window())
        if not result.skipped:
            if result.will_dry:
                assert result.raw_score >= 50.0 - 1e-9
            else:
                assert result.raw_score < 50.0 + 1e-9

    @pytest.mark.parametrize("n_hours", [4, 6, 8])
    def test_INV09_cold_dry_windy_beats_warm_humid_still(self, n_hours):
        """
        PRD hand-test: 10°C/50% RH (VPD≈0.61) must outperform 22°C/85% RH (VPD≈0.40).
        """
        vpd_cold = compute_vpd(10.0, 50.0)   # ≈ 0.610 kPa
        vpd_warm = compute_vpd(22.0, 85.0)   # ≈ 0.399 kPa
        cfg = WindowConfig(hang_hour=8, bring_in_hour=8 + n_hours - 1, dusk_hour=20)
        r_cold = score(_day_of(n_hours=n_hours, vpd=vpd_cold, wind=10.0, solar=225.0), cfg)
        r_warm = score(_day_of(n_hours=n_hours, vpd=vpd_warm, wind=2.0,  solar=225.0), cfg)
        assert not r_cold.skipped
        assert not r_warm.skipped
        assert r_cold.raw_score > r_warm.raw_score

    @pytest.mark.parametrize("temp_c, rh_pct, expected_vpd", [
        (10.0, 50.0,  0.610),  # PRD §7 cold-dry fixture
        (22.0, 85.0,  0.399),  # PRD §7 warm-humid fixture
    ])
    def test_vpd_hand_tests(self, temp_c, rh_pct, expected_vpd):
        """compute_vpd matches the PRD §7 hand-test values."""
        assert compute_vpd(temp_c, rh_pct) == pytest.approx(expected_vpd, abs=0.005)


# ---------------------------------------------------------------------------
# Best-window quality (#20) and near-rain warning (#23)
# ---------------------------------------------------------------------------

class TestBestWindowQuality:

    def test_prefers_higher_quality_window_over_earlier(self):
        """
        If afternoon hours are much better than morning, best_window should point
        to the afternoon even though morning reached DRY_TARGET first.
        Poor morning: vpd=0.01, wind=0.5, solar=10  → ~0.027 pot/hr (needs many hours)
        Good afternoon: vpd=1.0, wind=12, solar=450 → 1.0 pot/hr (4 hours = DRY_TARGET)
        """
        # Build a window of 12 hours: 8-10 poor, 11-14 excellent
        poor_hours = [_hour(h=h, vpd=0.01, wind=0.5, solar=10.0) for h in range(8, 11)]
        good_hours = [_hour(h=h, vpd=1.0,  wind=12.0, solar=450.0) for h in range(11, 16)]
        hours = poor_hours + good_hours
        cfg = _window(hang=8, bring_in=15, dusk=20)
        result = score(hours, cfg)
        assert result.best_window is not None
        # Window must include the good afternoon hours (start >= 11)
        assert result.best_window[0] >= 11

    def test_window_within_bounds_after_quality_selection(self):
        """Best window start/end stay within [hang_hour, end_hour]."""
        hours = _day_of(n_hours=8)
        result = score(hours, _window())
        if result.best_window is not None:
            cfg = _window()
            end_bound = min(cfg.bring_in_hour, cfg.dusk_hour)
            assert result.best_window[0] >= cfg.hang_hour
            assert result.best_window[1] <= end_bound


class TestNearRainWarning:

    def test_near_rain_set_when_prob_in_range(self):
        """Hour with prob 30% (above NEAR_RAIN_PROB_LOW=25, ≤ RAIN_PROB_GATE=40) → near_rain set."""
        hours = [_hour(h=h, precip_prob=0.0) for h in range(8, 12)]
        hours.append(_hour(h=12, precip_prob=30.0))
        hours += [_hour(h=h, precip_prob=0.0) for h in range(13, 16)]
        result = score(hours, _window(hang=8, bring_in=15, dusk=20))
        assert result.near_rain_hour == 12
        assert result.near_rain_prob == pytest.approx(30.0)

    def test_near_rain_none_on_clear_day(self):
        """No hours with notable probability → near_rain_hour is None."""
        hours = _day_of(n_hours=8)
        result = score(hours, _window())
        assert result.near_rain_hour is None
        assert result.near_rain_prob is None

    def test_near_rain_none_when_hour_is_gated(self):
        """Hour above RAIN_PROB_GATE is already gated — near_rain should not fire for it."""
        hours = [_hour(h=h, precip_prob=0.0) for h in range(8, 12)]
        hours.append(_hour(h=12, precip_prob=80.0))  # gated
        hours += [_hour(h=h, precip_prob=0.0) for h in range(13, 16)]
        result = score(hours, _window(hang=8, bring_in=15, dusk=20))
        # The 80% hour is gated — shown via window_rain_hour, not near_rain_hour
        assert result.near_rain_hour is None
        assert result.window_rain_hour == 12
