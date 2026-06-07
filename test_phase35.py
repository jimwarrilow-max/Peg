"""
Tests for Phase 3.5: outcome capture (evening.py, outcome.py, log.write_outcome).
"""

from __future__ import annotations

import csv
import json
import os
import tempfile
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from log import write_outcome, append_prediction, read_band
from scorer import Band, HourForecast, ScoreResult, WindowConfig, round_display


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_log(tmp_path, dates: list[str]) -> str:
    log_path = str(tmp_path / "log.csv")
    for d in dates:
        result = ScoreResult(
            raw_score=75.0, display_score=75, band=Band.GOOD,
            will_dry=True, override=False, best_window=(9, 14),
            gust_flag=False, skipped=False,
        )
        cfg = WindowConfig(hang_hour=9, bring_in_hour=18, dusk_hour=21)
        hours = [
            HourForecast(hour=i, temp_c=18.0, rh_pct=60.0, vpd_kpa=0.7,
                         wind_mph=8.0, solar_wm2=300.0, precip_mm=0.0,
                         precip_prob_pct=5.0)
            for i in range(24)
        ]
        append_prediction(date.fromisoformat(d), result, cfg, hours, log_path=log_path)
    return log_path


def _read_log(log_path: str) -> list[dict]:
    with open(log_path, newline="") as f:
        return list(csv.DictReader(f))


# ---------------------------------------------------------------------------
# log.write_outcome
# ---------------------------------------------------------------------------

class TestWriteOutcome:

    @pytest.mark.parametrize("outcome", ["dry", "damp"])
    def test_writes_outcome(self, outcome, tmp_path):
        log_path = _make_log(tmp_path, ["2026-05-30"])
        result = write_outcome("2026-05-30", outcome, log_path=log_path)
        assert result is True
        assert _read_log(log_path)[0]["outcome"] == outcome

    def test_returns_false_for_missing_date(self, tmp_path):
        log_path = _make_log(tmp_path, ["2026-05-30"])
        assert write_outcome("2026-05-31", "dry", log_path=log_path) is False

    def test_returns_false_for_missing_file(self, tmp_path):
        assert write_outcome("2026-05-30", "dry", log_path=str(tmp_path / "nope.csv")) is False

    def test_only_target_row_updated(self, tmp_path):
        log_path = _make_log(tmp_path, ["2026-05-29", "2026-05-30", "2026-05-31"])
        write_outcome("2026-05-30", "dry", log_path=log_path)
        rows = _read_log(log_path)
        assert rows[0]["outcome"] == ""    # 2026-05-29 untouched
        assert rows[1]["outcome"] == "dry" # 2026-05-30 updated
        assert rows[2]["outcome"] == ""    # 2026-05-31 untouched

    def test_preserves_all_columns(self, tmp_path):
        log_path = _make_log(tmp_path, ["2026-05-30"])
        before = _read_log(log_path)[0]
        write_outcome("2026-05-30", "dry", log_path=log_path)
        after = _read_log(log_path)[0]
        for col in before:
            if col != "outcome":
                assert after[col] == before[col]

    def test_outcome_can_be_overwritten(self, tmp_path):
        """A delayed reply or correction can overwrite a previous outcome."""
        log_path = _make_log(tmp_path, ["2026-05-30"])
        write_outcome("2026-05-30", "dry",  log_path=log_path)
        write_outcome("2026-05-30", "damp", log_path=log_path)
        assert _read_log(log_path)[0]["outcome"] == "damp"


# ---------------------------------------------------------------------------
# log.read_band
# ---------------------------------------------------------------------------

class TestReadBand:

    def test_returns_band_for_existing_date(self, tmp_path):
        log_path = _make_log(tmp_path, ["2026-05-30"])
        assert read_band("2026-05-30", log_path=log_path) == Band.GOOD.value

    def test_returns_none_for_missing_date(self, tmp_path):
        log_path = _make_log(tmp_path, ["2026-05-30"])
        assert read_band("2026-05-31", log_path=log_path) is None

    def test_returns_none_for_missing_file(self, tmp_path):
        assert read_band("2026-05-30", log_path=str(tmp_path / "nope.csv")) is None


# ---------------------------------------------------------------------------
# evening.py — prompt gating
# ---------------------------------------------------------------------------

class TestEveningGating:

    def _run_evening(self, log_path: str, tmp_path, band: Band = Band.GOOD) -> list[str]:
        """Run evening.main() with a log entry for today; return sent chat_ids."""
        import evening
        from log import append_prediction
        from scorer import WindowConfig
        today = date.today().isoformat()
        result = ScoreResult(
            raw_score=75.0, display_score=75, band=band,
            will_dry=True, override=False, best_window=(9, 14),
            gust_flag=False, skipped=False,
        )
        cfg = WindowConfig(hang_hour=9, bring_in_hour=18, dusk_hour=21)
        hours = [
            HourForecast(hour=i, temp_c=18.0, rh_pct=60.0, vpd_kpa=0.7,
                         wind_mph=8.0, solar_wm2=300.0, precip_mm=0.0,
                         precip_prob_pct=5.0)
            for i in range(24)
        ]
        append_prediction(date.today(), result, cfg, hours, log_path=log_path)

        sent_to = []
        def fake_send_with_keyboard(msg, kb, token, chat_id):
            sent_to.append(chat_id)

        with patch.dict(os.environ, {"TELEGRAM_TOKEN": "tok", "TELEGRAM_CHAT_ID": "111"}), \
             patch("evening.read_band", return_value=band.value), \
             patch("evening.send_with_keyboard", fake_send_with_keyboard):
            evening.main()
        return sent_to

    @pytest.mark.parametrize("band", [Band.CRACK, Band.GOOD, Band.MARGINAL])
    def test_sends_prompt_on_positive_bands(self, band, tmp_path):
        sent = self._run_evening(str(tmp_path / "log.csv"), tmp_path, band=band)
        assert sent == ["111"]

    def test_skips_prompt_on_tumble(self, tmp_path):
        sent = self._run_evening(str(tmp_path / "log.csv"), tmp_path, band=Band.TUMBLE)
        assert sent == []

    def test_skips_prompt_when_no_log_entry(self, tmp_path):
        import evening
        with patch.dict(os.environ, {"TELEGRAM_TOKEN": "tok", "TELEGRAM_CHAT_ID": "111"}), \
             patch("evening.read_band", return_value=None), \
             patch("evening.send_with_keyboard") as mock_send:
            evening.main()
        mock_send.assert_not_called()


# ---------------------------------------------------------------------------
# outcome.py — processing callback queries
# ---------------------------------------------------------------------------

def _make_callback_update(update_id: int, callback_data: str) -> dict:
    return {
        "update_id": update_id,
        "callback_query": {
            "id": f"cq_{update_id}",
            "data": callback_data,
            "from": {"id": 607945161},
        },
    }


class TestOutcomeProcessor:

    def _run_outcome(self, updates: list, log_path: str, offset_path: str) -> None:
        """Run outcome.main() with mocked Telegram and real log."""
        import outcome
        with patch.dict(os.environ, {"TELEGRAM_TOKEN": "fake-token"}), \
             patch("outcome.get_updates", return_value=updates), \
             patch("outcome.answer_callback"), \
             patch("outcome.OFFSET_FILE", offset_path), \
             patch("outcome.write_outcome", side_effect=lambda d, o: write_outcome(d, o, log_path=log_path)):
            outcome.main()

    @pytest.mark.parametrize("outcome", ["dry", "damp"])
    def test_response_written_to_log(self, outcome, tmp_path):
        log_path = _make_log(tmp_path, ["2026-05-30"])
        updates = [_make_callback_update(101, f"{outcome}:2026-05-30")]
        self._run_outcome(updates, log_path, str(tmp_path / ".offset"))
        assert _read_log(log_path)[0]["outcome"] == outcome

    def test_unknown_callback_data_ignored(self, tmp_path):
        log_path = _make_log(tmp_path, ["2026-05-30"])
        self._run_outcome([_make_callback_update(101, "something_unexpected")], log_path, str(tmp_path / ".offset"))
        assert _read_log(log_path)[0]["outcome"] == ""

    def test_no_updates_is_a_no_op(self, tmp_path):
        log_path = _make_log(tmp_path, ["2026-05-30"])
        self._run_outcome([], log_path, str(tmp_path / ".offset"))
        assert _read_log(log_path)[0]["outcome"] == ""

    def test_offset_advanced_after_processing(self, tmp_path):
        log_path    = _make_log(tmp_path, ["2026-05-30"])
        offset_path = str(tmp_path / ".offset")
        self._run_outcome([_make_callback_update(200, "dry:2026-05-30")], log_path, offset_path)
        assert Path(offset_path).read_text().strip() == "201"

    def test_offset_not_advanced_on_no_updates(self, tmp_path):
        log_path    = _make_log(tmp_path, ["2026-05-30"])
        offset_path = str(tmp_path / ".offset")
        Path(offset_path).write_text("50")
        self._run_outcome([], log_path, offset_path)
        assert Path(offset_path).read_text().strip() == "50"

    def test_missing_log_row_does_not_crash(self, tmp_path):
        log_path = _make_log(tmp_path, ["2026-05-30"])
        self._run_outcome([_make_callback_update(101, "dry:2026-05-28")], log_path, str(tmp_path / ".offset"))
        assert _read_log(log_path)[0]["outcome"] == ""


# ---------------------------------------------------------------------------
# notify.py extensions
# ---------------------------------------------------------------------------

class TestNotifyExtensions:

    def test_send_with_keyboard_posts_reply_markup(self):
        captured = []
        def fake_urlopen(req, timeout=None):
            captured.append(json.loads(req.data.decode()))
            resp = MagicMock()
            resp.__enter__ = lambda s: s
            resp.__exit__ = MagicMock(return_value=False)
            resp.read.return_value = json.dumps({"ok": True, "result": {}}).encode()
            return resp

        with patch("urllib.request.urlopen", fake_urlopen):
            from notify import send_with_keyboard
            send_with_keyboard("test", [[{"text": "👍", "callback_data": "dry:2026-05-30"}]], "token", "123")

        assert "reply_markup" in captured[0]
        assert captured[0]["reply_markup"]["inline_keyboard"][0][0]["callback_data"] == "dry:2026-05-30"

    def test_get_updates_returns_result_list(self):
        updates = [{"update_id": 1, "callback_query": {"id": "x", "data": "dry:2026-05-30"}}]
        body = json.dumps({"ok": True, "result": updates}).encode()
        mock_resp = MagicMock()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_resp.read.return_value = body

        with patch("urllib.request.urlopen", MagicMock(return_value=mock_resp)):
            from notify import get_updates
            result = get_updates("token", offset=0)

        assert len(result) == 1
        assert result[0]["update_id"] == 1
