"""
Peg — weekly accuracy summary.

Sends a Monday recap of last week's prediction accuracy to all configured
Telegram recipients.  Only runs when there are at least 3 outcomes to report.

Run manually or via a Monday GitHub Actions cron.
"""

from __future__ import annotations

import os
import sys
from datetime import date, timedelta

import config
from notify import broadcast, send
from prediction_log import Day, Outcome, PredictionLog, accuracy


def _last_week(log: PredictionLog) -> list[Day]:
    """Days in the 7 days ending yesterday."""
    yesterday = date.today() - timedelta(days=1)
    return log.days(yesterday - timedelta(days=6), yesterday)


def _build_summary(days: list[Day]) -> str | None:
    """
    Build a one-paragraph summary string, or None if there is too little data.
    """
    dry   = sum(1 for d in days if d.outcome == Outcome.DRY)
    damp  = sum(1 for d in days if d.outcome == Outcome.DAMP)
    skips = sum(1 for d in days if d.outcome == Outcome.SKIP)

    correct, total_with_outcome = accuracy(days)
    if total_with_outcome < 3:
        return None

    skip_line = f", {skips} didn't hang ⏭️" if skips else ""
    acc_pct   = round(100 * correct / total_with_outcome)

    return (
        f"🧺 <b>Peg's weekly report</b>\n"
        f"Last 7 days: {dry} dry ✅, {damp} damp ❌{skip_line}\n"
        f"Accuracy: {correct}/{total_with_outcome} ({acc_pct}%) 🎯"
    )


def _build_alert(days: list[Day]) -> str | None:
    """
    Health check: if the 3 most recent answerable days all went unanswered,
    the feedback buttons are probably broken — even if earlier days in the
    week were answered fine (a loop that dies mid-week must not be masked
    by a healthy-looking Monday and Tuesday).

    Only Answerable Days count — tumble-dryer Days get no evening
    prompt, so a missing Outcome there is expected, not a fault.
    """
    recent = [d for d in days if d.answerable][-3:]

    if len(recent) >= 3 and not any(d.answered for d in recent):
        return (
            f"🔧 <b>Peg's feedback loop looks broken.</b>\n"
            f"The last {len(recent)} drying days got no 👍/👎 answer. "
            f"The buttons may not be reaching me — worth a check."
        )
    return None


def _health_line(days: list[Day]) -> str:
    """One-line ops footer: is Peg doing its job, at a glance."""
    answerable = [d for d in days if d.answerable]
    answered   = [d for d in answerable if d.answered]
    return (
        f"🩺 {len(days)}/7 forecasts logged · "
        f"{len(answered)}/{len(answerable)} prompts answered"
    )


def main(log: PredictionLog | None = None) -> None:
    days = _last_week(log or PredictionLog())

    # The alert is deliberately NOT a fallback: a loop that breaks mid-week
    # must fire the alert even when there are enough early-week outcomes
    # for a normal summary.
    parts = [p for p in (_build_summary(days), _build_alert(days)) if p]

    if not parts:
        print("Not enough outcomes to summarise — skipping.")
        return

    parts.append(_health_line(days))
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
