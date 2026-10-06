"""Tests for run.py — the evening forecast entrypoint."""

from __future__ import annotations

import os
from datetime import date, timedelta
from unittest.mock import patch

from prediction_log import PredictionLog
from scorer import Band, HourForecast, ScoreResult, WindowConfig


def _record_tomorrow(log: PredictionLog) -> None:
    result = ScoreResult(raw_score=75.0, display_score=75, band=Band.GOOD,
                         will_dry=True, override=False, best_window=(9, 14),
                         gust_flag=False, skipped=False)
    hours = [HourForecast(hour=i, temp_c=18.0, rh_pct=60.0, vpd_kpa=0.7, wind_mph=8.0,
                          solar_wm2=300.0, precip_mm=0.0, precip_prob_pct=5.0)
             for i in range(24)]
    log.record(date.today() + timedelta(days=1), result,
               WindowConfig(hang_hour=9, bring_in_hour=18, dusk_hour=21), hours)


def test_second_run_same_evening_sends_nothing(tmp_path):
    """
    Two schedulers can start the forecast (outside scheduler + GitHub backup).
    If tomorrow's forecast is already in the Prediction Log, the second run
    must not fetch, send or write anything.
    """
    import run
    log = PredictionLog(str(tmp_path / "log.csv"))
    _record_tomorrow(log)
    before = open(log.path).read()

    with patch.dict(os.environ, {"TELEGRAM_TOKEN": "tok", "TELEGRAM_CHAT_ID": "111"}), \
         patch("run.fetch_forecast") as fetch, \
         patch("run.send") as send:
        run.main(log)

    fetch.assert_not_called()
    send.assert_not_called()
    assert open(log.path).read() == before
