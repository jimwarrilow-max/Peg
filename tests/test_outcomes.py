"""
Tests for outcome capture and reporting: evening.py, outcome.py, summary.py.
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

from prediction_log import Day, Outcome, PredictionLog
from scorer import Band, HourForecast, ScoreResult, WindowConfig, round_display


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_log(tmp_path, dates: list[str], band: Band = Band.GOOD) -> PredictionLog:
    log = PredictionLog(str(tmp_path / "log.csv"))
    for d in dates:
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
        log.record(date.fromisoformat(d), result, cfg, hours)
    return log


def _outcome(log: PredictionLog, iso: str):
    return log.day(date.fromisoformat(iso)).outcome


# ---------------------------------------------------------------------------
# evening.py — prompt gating
# ---------------------------------------------------------------------------

class TestEveningGating:

    def _run_evening(self, log: PredictionLog) -> list[str]:
        """Run evening.main() against `log`; return the chat_ids sent to."""
        import evening
        sent_to = []
        def fake_send_with_keyboard(msg, kb, token, chat_id):
            sent_to.append(chat_id)

        with patch.dict(os.environ, {"TELEGRAM_TOKEN": "tok", "TELEGRAM_CHAT_ID": "111"}), \
             patch("evening.send_with_keyboard", fake_send_with_keyboard):
            evening.main(log)
        return sent_to

    @pytest.mark.parametrize("band", [Band.CRACK, Band.GOOD, Band.MARGINAL])
    def test_sends_prompt_on_positive_bands(self, band, tmp_path):
        log = _make_log(tmp_path, [date.today().isoformat()], band=band)
        assert self._run_evening(log) == ["111"]

    def test_skips_prompt_on_tumble(self, tmp_path):
        log = _make_log(tmp_path, [date.today().isoformat()], band=Band.TUMBLE)
        assert self._run_evening(log) == []

    @pytest.mark.parametrize("band", [Band.CRACK, Band.GOOD, Band.MARGINAL])
    def test_prompt_names_today_and_its_band(self, band, tmp_path):
        """The question must not be confused with tomorrow's forecast (#43)."""
        import evening
        prompts = []
        log = _make_log(tmp_path, [date.today().isoformat()], band=band)
        with patch.dict(os.environ, {"TELEGRAM_TOKEN": "tok", "TELEGRAM_CHAT_ID": "111"}), \
             patch("evening.send_with_keyboard", lambda msg, kb, tok, cid: prompts.append(msg)):
            evening.main(log)
        assert "Today's washing" in prompts[0]
        assert band.value in prompts[0]

    def test_skips_prompt_when_no_log_entry(self, tmp_path):
        log = _make_log(tmp_path, ["2026-05-30"])
        assert self._run_evening(log) == []


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

    def _run_outcome(self, updates: list, log: PredictionLog, offset_path: str, send=None) -> None:
        """Run outcome.main() with mocked Telegram and a real temporary log."""
        import outcome
        with patch.dict(os.environ, {"TELEGRAM_TOKEN": "fake-token"}), \
             patch("outcome.get_updates", return_value=updates), \
             patch("outcome.answer_callback"), \
             patch("outcome.send", side_effect=send), \
             patch("outcome.OFFSET_FILE", offset_path):
            outcome.main(log)

    @pytest.mark.parametrize("outcome", ["dry", "damp", "skip"])
    def test_response_written_to_log(self, outcome, tmp_path):
        log = _make_log(tmp_path, ["2026-05-30"])
        updates = [_make_callback_update(101, f"{outcome}:2026-05-30")]
        self._run_outcome(updates, log, str(tmp_path / ".offset"))
        assert _outcome(log, "2026-05-30") == Outcome(outcome)

    def test_unknown_callback_data_ignored(self, tmp_path):
        log = _make_log(tmp_path, ["2026-05-30"])
        self._run_outcome([_make_callback_update(101, "something_unexpected")], log, str(tmp_path / ".offset"))
        assert _outcome(log, "2026-05-30") is None

    def test_unreadable_date_ignored(self, tmp_path):
        log = _make_log(tmp_path, ["2026-05-30"])
        self._run_outcome([_make_callback_update(101, "dry:yesterday")], log, str(tmp_path / ".offset"))
        assert _outcome(log, "2026-05-30") is None

    def test_no_updates_is_a_no_op(self, tmp_path):
        log = _make_log(tmp_path, ["2026-05-30"])
        self._run_outcome([], log, str(tmp_path / ".offset"))
        assert _outcome(log, "2026-05-30") is None

    def test_offset_advanced_after_processing(self, tmp_path):
        log         = _make_log(tmp_path, ["2026-05-30"])
        offset_path = str(tmp_path / ".offset")
        self._run_outcome([_make_callback_update(200, "dry:2026-05-30")], log, offset_path)
        assert Path(offset_path).read_text().strip() == "201"

    def test_offset_not_advanced_on_no_updates(self, tmp_path):
        log         = _make_log(tmp_path, ["2026-05-30"])
        offset_path = str(tmp_path / ".offset")
        Path(offset_path).write_text("50")
        self._run_outcome([], log, offset_path)
        assert Path(offset_path).read_text().strip() == "50"

    def test_missing_log_row_does_not_crash(self, tmp_path):
        log = _make_log(tmp_path, ["2026-05-30"])
        self._run_outcome([_make_callback_update(101, "dry:2026-05-28")], log, str(tmp_path / ".offset"))
        assert _outcome(log, "2026-05-30") is None

    def test_confirmation_sent_after_outcome(self, tmp_path):
        """A confirmation message is sent to the user after recording any outcome."""
        log = _make_log(tmp_path, ["2026-05-30"])
        sent_confirms = []
        self._run_outcome([_make_callback_update(101, "dry:2026-05-30")], log, str(tmp_path / ".offset"),
                          send=lambda msg, tok, cid: sent_confirms.append(cid))
        assert sent_confirms == ["607945161"]


# ---------------------------------------------------------------------------
# evening.py — third outcome button
# ---------------------------------------------------------------------------

class TestEveningKeyboard:

    def test_keyboard_has_three_buttons(self, tmp_path):
        """Evening prompt keyboard includes Bone dry, Still damp, and Didn't hang."""
        import evening
        captured_keyboards = []
        def fake_send_with_keyboard(msg, kb, token, chat_id):
            captured_keyboards.append(kb)

        log = _make_log(tmp_path, [date.today().isoformat()])
        with patch.dict(os.environ, {"TELEGRAM_TOKEN": "tok", "TELEGRAM_CHAT_ID": "111"}), \
             patch("evening.send_with_keyboard", fake_send_with_keyboard):
            evening.main(log)

        assert len(captured_keyboards) == 1
        callback_datas = [b["callback_data"] for b in captured_keyboards[0][0]]
        assert any(d.startswith("dry:") for d in callback_datas)
        assert any(d.startswith("damp:") for d in callback_datas)
        assert any(d.startswith("skip:") for d in callback_datas)


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


# ---------------------------------------------------------------------------
# summary.py — _build_summary
# ---------------------------------------------------------------------------

def _summary_row(band: str, outcome: str) -> Day:
    """A Day as summary.py's builders read it."""
    return Day(date(2026, 5, 30), Band(band), Outcome(outcome) if outcome else None)


class TestBuildSummary:

    def test_returns_none_when_fewer_than_3_outcomes(self):
        from summary import _build_summary
        rows = [_summary_row(Band.GOOD.value, "dry"), _summary_row(Band.GOOD.value, "dry")]
        assert _build_summary(rows) is None

    def test_summary_contains_dry_and_damp_counts(self):
        from summary import _build_summary
        rows = [
            _summary_row(Band.GOOD.value,  "dry"),
            _summary_row(Band.GOOD.value,  "dry"),
            _summary_row(Band.GOOD.value,  "damp"),
        ]
        msg = _build_summary(rows)
        assert msg is not None
        assert "2 dry" in msg
        assert "1 damp" in msg

    def test_summary_shows_accuracy(self):
        from summary import _build_summary
        rows = [
            _summary_row(Band.GOOD.value,  "dry"),   # correct
            _summary_row(Band.GOOD.value,  "dry"),   # correct
            _summary_row(Band.GOOD.value,  "damp"),  # wrong
        ]
        msg = _build_summary(rows)
        assert "2/3" in msg

    def test_summary_shows_skip_count_when_nonzero(self):
        from summary import _build_summary
        rows = [
            _summary_row(Band.GOOD.value, "dry"),
            _summary_row(Band.GOOD.value, "dry"),
            _summary_row(Band.GOOD.value, "dry"),
            _summary_row(Band.GOOD.value, "skip"),
        ]
        msg = _build_summary(rows)
        assert "⏭️" in msg
        assert "1" in msg

    def test_skip_not_counted_as_outcome(self):
        from summary import _build_summary
        rows = [
            _summary_row(Band.GOOD.value, "dry"),
            _summary_row(Band.GOOD.value, "skip"),
            _summary_row(Band.GOOD.value, "skip"),
        ]
        # Only 1 outcome with dry/damp — not enough for summary
        assert _build_summary(rows) is None

    def test_html_bold_present(self):
        from summary import _build_summary
        rows = [_summary_row(Band.GOOD.value, "dry") for _ in range(3)]
        msg = _build_summary(rows)
        assert "<b>" in msg and "</b>" in msg


# ---------------------------------------------------------------------------
# summary.py — _build_alert (feedback-loop health check)
# ---------------------------------------------------------------------------

class TestBuildAlert:

    def test_alerts_when_drying_days_have_no_outcomes(self):
        from summary import _build_alert
        rows = [_summary_row(Band.GOOD.value, "") for _ in range(3)]
        msg = _build_alert(rows)
        assert msg is not None
        assert "broken" in msg.lower()

    def test_silent_when_an_outcome_was_recorded(self):
        from summary import _build_alert
        rows = [
            _summary_row(Band.GOOD.value, "dry"),
            _summary_row(Band.GOOD.value, ""),
            _summary_row(Band.GOOD.value, ""),
        ]
        assert _build_alert(rows) is None

    def test_silent_below_threshold(self):
        from summary import _build_alert
        rows = [_summary_row(Band.GOOD.value, "") for _ in range(2)]
        assert _build_alert(rows) is None

    def test_tumble_days_excluded_from_count(self):
        from summary import _build_alert
        # 2 answerable (no outcome) + 3 TUMBLE (never prompted) → below threshold.
        rows = [_summary_row(Band.GOOD.value, "") for _ in range(2)] + \
               [_summary_row(Band.TUMBLE.value, "") for _ in range(3)]
        assert _build_alert(rows) is None

    def test_skip_counts_as_a_recorded_answer(self):
        from summary import _build_alert
        rows = [
            _summary_row(Band.GOOD.value, "skip"),
            _summary_row(Band.GOOD.value, ""),
            _summary_row(Band.GOOD.value, ""),
        ]
        assert _build_alert(rows) is None

    def test_alerts_when_loop_dies_mid_week(self):
        """Early-week answers must not mask a loop that broke on Wednesday."""
        from summary import _build_alert
        rows = [_summary_row(Band.GOOD.value, "dry") for _ in range(3)] + \
               [_summary_row(Band.GOOD.value, "") for _ in range(3)]
        msg = _build_alert(rows)
        assert msg is not None
        assert "broken" in msg.lower()

    def test_recent_answer_keeps_alert_silent_despite_old_gaps(self):
        from summary import _build_alert
        rows = [_summary_row(Band.GOOD.value, "") for _ in range(3)] + \
               [_summary_row(Band.GOOD.value, "damp")]
        assert _build_alert(rows) is None


# ---------------------------------------------------------------------------
# summary.py — _health_line and main() composition
# ---------------------------------------------------------------------------

class TestHealthLine:

    def test_counts_forecasts_and_answers(self):
        from summary import _health_line
        rows = [
            _summary_row(Band.GOOD.value,   "dry"),
            _summary_row(Band.GOOD.value,   ""),
            _summary_row(Band.TUMBLE.value, ""),      # not answerable
            _summary_row(Band.CRACK.value,  "skip"),
        ]
        line = _health_line(rows)
        assert "4/7 forecasts logged" in line
        assert "2/3 prompts answered" in line

    def test_summary_and_alert_can_send_together(self):
        """A mid-week breakage sends the normal summary AND the alert."""
        from summary import _build_summary, _build_alert
        rows = [_summary_row(Band.GOOD.value, "dry") for _ in range(3)] + \
               [_summary_row(Band.GOOD.value, "") for _ in range(3)]
        assert _build_summary(rows) is not None
        assert _build_alert(rows) is not None
