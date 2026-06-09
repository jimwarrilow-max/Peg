"""
Fetch-and-transform layer for Peg.

fetch_forecast(lat, lon) → (hours, dusk_hour, sunrise_hour)

The two steps are deliberately separated so the transform can be tested
without hitting the network:

  _fetch_raw(lat, lon) → dict                       — I/O only; raises FetchError
  transform(data)      → (hours, dusk_hour, sunrise_hour)  — pure; testable with a fixture dict

VPD is computed here from temp+RH (PRD §7: the API's VPD field proved
unreliable in live testing — returned 0 despite real humidity).
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional

from scorer import HourForecast, compute_vpd

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"

_HOURLY_FIELDS = [
    "temperature_2m",
    "relative_humidity_2m",
    "wind_speed_10m",
    "wind_gusts_10m",
    "shortwave_radiation",
    "precipitation",
    "precipitation_probability",
    "et0_fao_evapotranspiration",
    "uv_index",
]


class FetchError(Exception):
    """Raised when the forecast cannot be obtained or parsed."""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def fetch_forecast(lat: float, lon: float, timezone: str = "Europe/London") -> tuple[list[HourForecast], int, int]:
    """Return (hours, dusk_hour, sunrise_hour) for tomorrow at the given location."""
    data = _fetch_raw(lat, lon, timezone)
    return transform(data, day_index=1)


def transform(data: dict, day_index: int = 0) -> tuple[list[HourForecast], int, int]:
    """
    Convert a raw Open-Meteo response dict into (hours, dusk_hour, sunrise_hour).

    day_index    — 0 = first day in the response, 1 = second day (tomorrow when
                   forecast_days=2).

    hours        — one HourForecast per hour of the target day (hour field 0–23);
                   vpd_kpa is computed from temp+RH, or None if either is missing.
    dusk_hour    — floor of the sunset hour (for WindowConfig.dusk_hour).
    sunrise_hour — floor of the sunrise hour (to gate pre-dawn hours).

    Raises FetchError if required structure is absent.
    """
    # --- Dusk and dawn hours -----------------------------------------------
    try:
        sunset_str: str  = data["daily"]["sunset"][day_index]
        dusk_hour = int(sunset_str[11:13])
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise FetchError(f"Could not parse sunset from response: {exc}") from exc

    try:
        sunrise_str: str = data["daily"]["sunrise"][day_index]
        sunrise_hour = int(sunrise_str[11:13])
    except (KeyError, IndexError, TypeError, ValueError):
        sunrise_hour = 0  # absent or malformed → no restriction

    # --- Hourly arrays ------------------------------------------------------
    hourly = data.get("hourly", {})

    n = _array_length(hourly)
    if n == 0:
        raise FetchError("Hourly arrays are missing or empty.")
    required = (day_index + 1) * 24
    if n < required:
        raise FetchError(f"Expected ≥{required} hourly entries, got {n}.")

    hours: list[HourForecast] = []
    start = day_index * 24
    for i in range(start, start + 24):
        temp_c = _at(hourly, "temperature_2m", i)
        rh_pct = _at(hourly, "relative_humidity_2m", i)

        vpd_kpa = (
            compute_vpd(temp_c, rh_pct)
            if temp_c is not None and rh_pct is not None
            else None
        )

        hours.append(HourForecast(
            hour=i - start,
            temp_c=temp_c,
            rh_pct=rh_pct,
            vpd_kpa=vpd_kpa,
            wind_mph=_at(hourly, "wind_speed_10m", i),
            solar_wm2=_at(hourly, "shortwave_radiation", i),
            precip_mm=_at(hourly, "precipitation", i),
            precip_prob_pct=_at(hourly, "precipitation_probability", i),
            wind_gust_mph=_at(hourly, "wind_gusts_10m", i),
            uv_index=_at(hourly, "uv_index", i),
            et0_mm=_at(hourly, "et0_fao_evapotranspiration", i),
        ))

    return hours, dusk_hour, sunrise_hour


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _fetch_raw(lat: float, lon: float, timezone: str) -> dict:
    params = {
        "latitude": lat,
        "longitude": lon,
        "hourly": ",".join(_HOURLY_FIELDS),
        "daily": "sunrise,sunset",
        "wind_speed_unit": "mph",
        "timezone": timezone,
        "forecast_days": "2",
    }
    url = OPEN_METEO_URL + "?" + urllib.parse.urlencode(params)

    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            if resp.status != 200:
                raise FetchError(f"Open-Meteo returned HTTP {resp.status}")
            raw = resp.read().decode("utf-8")
    except urllib.error.URLError as exc:
        raise FetchError(f"Network error contacting Open-Meteo: {exc}") from exc

    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise FetchError(f"Malformed JSON from Open-Meteo: {exc}") from exc


def _at(hourly: dict, field: str, idx: int) -> Optional[float]:
    """Return hourly[field][idx], or None if the array or element is absent/null."""
    arr = hourly.get(field)
    if arr is None or idx >= len(arr):
        return None
    value = arr[idx]
    return None if value is None else float(value)


def _array_length(hourly: dict) -> int:
    """Length of the first non-None hourly array, or 0."""
    for field in _HOURLY_FIELDS:
        arr = hourly.get(field)
        if arr is not None:
            return len(arr)
    return 0
