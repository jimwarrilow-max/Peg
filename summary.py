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
from log import LOG_PATH, prediction_correct
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


def main() -> None:
    rows   = _last_week_rows()
    msg    = _build_summary(rows)

    if msg is None:
        print("Not enough outcomes to summarise — skipping.")
        return

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
