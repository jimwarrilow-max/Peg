"""
Tests for the fetch-and-transform layer (fetch.py).

The transform() function is pure (no I/O), so all tests below run without
hitting the network.  _fetch_raw() error-handling is tested via unittest.mock.
"""

from __future__ import annotations

import json
from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest

from fetch import FetchError, _at, transform
from scorer import compute_vpd


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

def _make_response(n: int = 24, date: str = "2026-05-30") -> dict:
    """Build a minimal but complete Open-Meteo-shaped response for n hours."""
    times = [f"{date}T{h:02d}:00" for h in range(n)]
    return {
        "hourly": {
            "time":                        times,
            "temperature_2m":              [15.0 + h * 0.1  for h in range(n)],
            "relative_humidity_2m":        [60.0            for _ in range(n)],
            "wind_speed_10m":              [8.0             for _ in range(n)],
            "wind_gusts_10m":              [12.0            for _ in range(n)],
            "shortwave_radiation":         [300.0           for _ in range(n)],
            "precipitation":               [0.0             for _ in range(n)],
            "precipitation_probability":   [10.0            for _ in range(n)],
            "et0_fao_evapotranspiration":  [0.15            for _ in range(n)],
        },
        "daily": {
            "time":    [date],
            "sunrise": [f"{date}T05:10"],
            "sunset":  [f"{date}T21:18"],
        },
    }


# ---------------------------------------------------------------------------
# INT-01 — Parallel arrays mapped to per-hour objects, index-aligned
# ---------------------------------------------------------------------------

class TestTransformHappyPath:

    def test_returns_24_hours(self):
        data = _make_response()
        hours, _ = transform(data)
        assert len(hours) == 24

    def test_hour_field_matches_index(self):
        data = _make_response()
        hours, _ = transform(data)
        for i, h in enumerate(hours):
            assert h.hour == i

    def test_temperature_aligned(self):
        data = _make_response()
        hours, _ = transform(data)
        for i, h in enumerate(hours):
            assert h.temp_c == pytest.approx(15.0 + i * 0.1)

    def test_wind_aligned(self):
        data = _make_response()
        hours, _ = transform(data)
        assert all(h.wind_mph == pytest.approx(8.0) for h in hours)

    def test_precip_aligned(self):
        data = _make_response()
        hours, _ = transform(data)
        assert all(h.precip_mm == pytest.approx(0.0) for h in hours)

    def test_precip_prob_aligned(self):
        data = _make_response()
        hours, _ = transform(data)
        assert all(h.precip_prob_pct == pytest.approx(10.0) for h in hours)

    def test_gust_aligned(self):
        data = _make_response()
        hours, _ = transform(data)
        assert all(h.wind_gust_mph == pytest.approx(12.0) for h in hours)

    def test_solar_aligned(self):
        data = _make_response()
        hours, _ = transform(data)
        assert all(h.solar_wm2 == pytest.approx(300.0) for h in hours)

    def test_dusk_hour_extracted(self):
        data = _make_response()   # sunset at T21:18
        _, dusk_hour = transform(data)
        assert dusk_hour == 21

    def test_dusk_hour_floor(self):
        """Sunset at :45 should give floor hour, not ceiling."""
        data = _make_response()
        data["daily"]["sunset"][0] = "2026-05-30T20:45"
        _, dusk_hour = transform(data)
        assert dusk_hour == 20

    def test_dusk_hour_midnight_edge(self):
        data = _make_response()
        data["daily"]["sunset"][0] = "2026-05-30T00:01"
        _, dusk_hour = transform(data)
        assert dusk_hour == 0


# ---------------------------------------------------------------------------
# VPD computed from temp+RH (not taken from the API — PRD §7)
# ---------------------------------------------------------------------------

class TestVpdComputed:

    def test_vpd_computed_not_from_api_field(self):
        """VPD must be derived from temp+RH regardless of API content."""
        data = _make_response()
        hours, _ = transform(data)
        for h in hours:
            expected = compute_vpd(h.temp_c, h.rh_pct)
            assert h.vpd_kpa == pytest.approx(expected, rel=1e-6)

    def test_vpd_positive_for_normal_rh(self):
        data = _make_response()
        hours, _ = transform(data)
        assert all(h.vpd_kpa > 0 for h in hours)

    def test_vpd_near_zero_at_saturation(self):
        """RH=100 → VPD≈0."""
        data = _make_response()
        for i in range(24):
            data["hourly"]["relative_humidity_2m"][i] = 100.0
        hours, _ = transform(data)
        assert all(h.vpd_kpa == pytest.approx(0.0, abs=0.01) for h in hours)

    def test_vpd_none_when_temp_missing(self):
        data = _make_response()
        data["hourly"]["temperature_2m"][5] = None
        hours, _ = transform(data)
        assert hours[5].vpd_kpa is None

    def test_vpd_none_when_rh_missing(self):
        data = _make_response()
        data["hourly"]["relative_humidity_2m"][3] = None
        hours, _ = transform(data)
        assert hours[3].vpd_kpa is None

    def test_vpd_cold_dry_beats_warm_humid(self):
        """10°C/50% RH out-dries 22°C/85% RH — the PRD §7 hand-test."""
        data = _make_response()
        data["hourly"]["temperature_2m"][0]       = 10.0
        data["hourly"]["relative_humidity_2m"][0] = 50.0
        data["hourly"]["temperature_2m"][1]       = 22.0
        data["hourly"]["relative_humidity_2m"][1] = 85.0
        hours, _ = transform(data)
        assert hours[0].vpd_kpa > hours[1].vpd_kpa


# ---------------------------------------------------------------------------
# INT-06 — Null / missing fields produce None, not 0 or an error
# ---------------------------------------------------------------------------

class TestNullFieldHandling:

    def test_null_wind_gives_none(self):
        data = _make_response()
        data["hourly"]["wind_speed_10m"][7] = None
        hours, _ = transform(data)
        assert hours[7].wind_mph is None

    def test_null_solar_gives_none(self):
        data = _make_response()
        data["hourly"]["shortwave_radiation"][2] = None
        hours, _ = transform(data)
        assert hours[2].solar_wm2 is None

    def test_null_precip_gives_none(self):
        data = _make_response()
        data["hourly"]["precipitation"][10] = None
        hours, _ = transform(data)
        assert hours[10].precip_mm is None

    def test_null_precip_prob_gives_none(self):
        data = _make_response()
        data["hourly"]["precipitation_probability"][10] = None
        hours, _ = transform(data)
        assert hours[10].precip_prob_pct is None

    def test_missing_field_entirely_gives_none(self):
        """If a whole field key is absent from the response, every hour gets None."""
        data = _make_response()
        del data["hourly"]["wind_speed_10m"]
        hours, _ = transform(data)
        assert all(h.wind_mph is None for h in hours)

    def test_null_does_not_become_zero(self):
        """Null values must never silently become 0 — PRD §5 conservative bias."""
        data = _make_response()
        data["hourly"]["wind_speed_10m"][6] = None
        hours, _ = transform(data)
        assert hours[6].wind_mph is None
        assert hours[6].wind_mph != 0.0


# ---------------------------------------------------------------------------
# INT-02 — Wind is in mph (regression guard against km/h)
# ---------------------------------------------------------------------------

class TestUnits:

    def test_wind_speed_unit_is_mph_in_url(self):
        """The URL sent to Open-Meteo must include wind_speed_unit=mph."""
        from fetch import OPEN_METEO_URL, _HOURLY_FIELDS
        import urllib.parse

        captured_url = []

        class FakeResponse:
            status = 200
            def read(self): return json.dumps(_make_response()).encode()
            def __enter__(self): return self
            def __exit__(self, *a): pass

        with patch("urllib.request.urlopen", side_effect=lambda url, **kw: (captured_url.append(url), FakeResponse())[1]):
            from fetch import _fetch_raw
            _fetch_raw(52.0, -1.9, "Europe/London")

        assert len(captured_url) == 1
        parsed = urllib.parse.urlparse(captured_url[0])
        params = urllib.parse.parse_qs(parsed.query)
        assert params.get("wind_speed_unit") == ["mph"], (
            "wind_speed_unit=mph missing from Open-Meteo URL — "
            "all wind scores would be wrong (km/h is the default)"
        )

    def test_timezone_is_london_in_url(self):
        """The URL must request Europe/London timezone (INT-03)."""
        import urllib.parse as urlparse
        captured_url = []

        class FakeResponse:
            status = 200
            def read(self): return json.dumps(_make_response()).encode()
            def __enter__(self): return self
            def __exit__(self, *a): pass

        with patch("urllib.request.urlopen", side_effect=lambda url, **kw: (captured_url.append(url), FakeResponse())[1]):
            from fetch import _fetch_raw
            _fetch_raw(52.0, -1.9, "Europe/London")

        parsed = urlparse.urlparse(captured_url[0])
        params = urlparse.parse_qs(parsed.query)
        assert "Europe/London" in params.get("timezone", [])


# ---------------------------------------------------------------------------
# INT-04 / INT-05 — Error handling: non-200 and malformed JSON
# ---------------------------------------------------------------------------

class TestErrorHandling:

    def _fake_urlopen(self, status: int = 200, body: bytes = b"{}"):
        mock_resp = MagicMock()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_resp.status = status
        mock_resp.read.return_value = body
        return MagicMock(return_value=mock_resp)

    def test_non_200_raises_fetch_error(self):
        with patch("urllib.request.urlopen", self._fake_urlopen(status=503)):
            from fetch import _fetch_raw
            with pytest.raises(FetchError, match="HTTP 503"):
                _fetch_raw(52.0, -1.9, "Europe/London")

    def test_malformed_json_raises_fetch_error(self):
        with patch("urllib.request.urlopen", self._fake_urlopen(body=b"not json {")):
            from fetch import _fetch_raw
            with pytest.raises(FetchError, match="Malformed JSON"):
                _fetch_raw(52.0, -1.9, "Europe/London")

    def test_network_error_raises_fetch_error(self):
        import urllib.error
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("timeout")):
            from fetch import _fetch_raw
            with pytest.raises(FetchError, match="Network error"):
                _fetch_raw(52.0, -1.9, "Europe/London")

    def test_missing_sunset_raises_fetch_error(self):
        data = _make_response()
        del data["daily"]["sunset"]
        with pytest.raises(FetchError, match="sunset"):
            transform(data)

    def test_empty_hourly_raises_fetch_error(self):
        data = _make_response()
        data["hourly"] = {}
        with pytest.raises(FetchError, match="missing or empty"):
            transform(data)

    def test_short_arrays_raises_fetch_error(self):
        """Fewer than 24 hourly entries → FetchError (partial day, can't score)."""
        data = _make_response(n=12)
        with pytest.raises(FetchError, match="24"):
            transform(data)


# ---------------------------------------------------------------------------
# _at helper — boundary behaviour
# ---------------------------------------------------------------------------

class TestAtHelper:

    def test_returns_value(self):
        assert _at({"x": [1.0, 2.0, 3.0]}, "x", 1) == pytest.approx(2.0)

    def test_missing_key_returns_none(self):
        assert _at({}, "x", 0) is None

    def test_null_value_returns_none(self):
        assert _at({"x": [None, 2.0]}, "x", 0) is None

    def test_out_of_range_returns_none(self):
        assert _at({"x": [1.0]}, "x", 5) is None

    def test_value_coerced_to_float(self):
        result = _at({"x": [42]}, "x", 0)
        assert result == pytest.approx(42.0)
        assert isinstance(result, float)
