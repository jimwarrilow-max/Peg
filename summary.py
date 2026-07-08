"""
Peg — weekly accuracy summary.

Sends a Monday recap of last week's prediction accuracy to all configured
Telegram recipients.  Only runs when there are at least 3 outcomes to report.

Run manually or via a Monday GitHub Actions cron.
"""

from __future__ import annotations

import csv
import os
import sys
from datetime import date, timedelta

import config
from log import LOG_PATH, VALID_OUTCOMES, is_answerable, prediction_correct
from notify import broadcast, send


def _last_week_rows() -> list[dict]:
    """Return log rows whose date falls in the 7 days ending yesterday."""
    if not os.path.isfile(LOG_PATH):
        return []
    today = date.today()
    cutoff_end   = today - timedelta(days=1)         # yesterday
    cutoff_start = cutoff_end - timedelta(days=6)    # 7 days ago
    rows = []
    with open(LOG_PATH, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            try:
                d = date.fromisoformat(row["date"])
            except (KeyError, ValueError):
                continue
            if cutoff_start <= d <= cutoff_end:
                rows.append(row)
    return rows


def _build_summary(rows: list[dict]) -> str | None:
    """
    Build a one-paragraph summary string, or None if there is too little data.
    """
    dry   = sum(1 for r in rows if r.get("outcome") == "dry")
    damp  = sum(1 for r in rows if r.get("outcome") == "damp")
    skips = sum(1 for r in rows if r.get("outcome") == "skip")
    total_with_outcome = dry + damp

    if total_with_outcome < 3:
        return None

    correct = sum(
        1 for r in rows
        if r.get("outcome") in ("dry", "damp")
        and prediction_correct(r.get("band", ""), r["outcome"])
    )

    skip_line = f", {skips} didn't hang ⏭️" if skips else ""
    acc_pct   = round(100 * correct / total_with_outcome)

    return (
        f"🧺 <b>Peg's weekly report</b>\n"
        f"Last 7 days: {dry} dry ✅, {damp} damp ❌{skip_line}\n"
        f"Accuracy: {correct}/{total_with_outcome} ({acc_pct}%) 🎯"
    )


def _build_alert(rows: list[dict]) -> str | None:
    """
    Health check: if the 3 most recent answerable days all went unanswered,
    the feedback buttons are probably broken — even if earlier days in the
    week were answered fine (a loop that dies mid-week must not be masked
    by a healthy-looking Monday and Tuesday).

    TUMBLE days are excluded via is_answerable — they get no evening
    prompt, so a missing outcome there is expected, not a fault.
    """
    answerable = [r for r in rows if is_answerable(r.get("band"))]
    recent     = answerable[-3:]

    if len(recent) >= 3 and all(r.get("outcome") not in VALID_OUTCOMES for r in recent):
        return (
            f"🔧 <b>Peg's feedback loop looks broken.</b>\n"
            f"The last {len(recent)} drying days got no 👍/👎 answer. "
            f"The buttons may not be reaching me — worth a check."
        )
    return None


def _health_line(rows: list[dict]) -> str:
    """One-line ops footer: is Peg doing its job, at a glance."""
    answerable = [r for r in rows if is_answerable(r.get("band"))]
    answered   = [r for r in answerable if r.get("outcome") in VALID_OUTCOMES]
    return (
        f"🩺 {len(rows)}/7 forecasts logged · "
        f"{len(answered)}/{len(answerable)} prompts answered"
    )


def main() -> None:
    rows = _last_week_rows()

    # The alert is deliberately NOT a fallback: a loop that breaks mid-week
    # must fire the alert even when there are enough early-week outcomes
    # for a normal summary.
    parts = [p for p in (_build_summary(rows), _build_alert(rows)) if p]

    if not parts:
        print("Not enough outcomes to summarise — skipping.")
        return

    parts.append(_health_line(rows))
    msg = "\n\n".join(parts)

    print(msg)

    token    = os.environ.get("TELEGRAM_TOKEN")
    chat_ids = config.chat_ids()

    if token and chat_ids:
        def _send_one(chat_id: str) -> None:
            send(msg, token, chat_id)
            print(f"Summary sent to {chat_id}.")
        failures = broadcast(chat_ids, _send_one)
        if failures == len(chat_ids):
            sys.exit(1)
    else:
        print("Telegram: TELEGRAM_TOKEN / TELEGRAM_CHAT_ID not set — skipping send.")


if __name__ == "__main__":
    main()
