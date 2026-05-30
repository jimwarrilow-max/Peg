"""
Peg — daily forecast entrypoint.

Fetch → score → format → notify (Telegram) → log (CSV).

Environment variables (set as GitHub Actions secrets in production;
omit locally to print-only mode):
  TELEGRAM_TOKEN    — bot token
  TELEGRAM_CHAT_ID  — recipient chat ID
"""

from __future__ import annotations

import os
import sys
from datetime import date, timezone

import config
from fetch import FetchError, fetch_forecast
from log import append_prediction
from messages import format_message
from notify import NotifyError, send
from scorer import WindowConfig, score


def main() -> None:
    print("Peg is checking the forecast…")

    # --- Fetch -----------------------------------------------------------
    try:
        hours, dusk_hour = fetch_forecast(config.LAT, config.LON, config.TIMEZONE)
    except FetchError as exc:
        _fail(f"Fetch failed: {exc}")

    # --- Score -----------------------------------------------------------
    cfg = WindowConfig(
        hang_hour=config.HANG_HOUR,
        bring_in_hour=config.BRING_IN_HOUR,
        dusk_hour=dusk_hour,
    )
    result = score(hours, cfg)

    # --- Format ----------------------------------------------------------
    message = format_message(result, config.HANG_HOUR, config.BRING_IN_HOUR, dusk_hour, hours)
    print(message)

    # --- Notify ----------------------------------------------------------
    token   = os.environ.get("TELEGRAM_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")

    if token and chat_id:
        try:
            send(message, token, chat_id)
            print("Telegram: sent.")
        except NotifyError as exc:
            # Delivery failure is logged but doesn't abort the log step.
            print(f"Telegram: failed — {exc}", file=sys.stderr)
    else:
        print("Telegram: TELEGRAM_TOKEN / TELEGRAM_CHAT_ID not set — skipping send.")

    # --- Log -------------------------------------------------------------
    today = date.today()
    append_prediction(today, result, cfg, hours)
    print(f"Log: row written for {today}.")


def _fail(reason: str) -> None:
    """Send a failure ping if credentials are available, then exit non-zero."""
    print(f"\nPeg's drawn a blank — {reason}", file=sys.stderr)
    token   = os.environ.get("TELEGRAM_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if token and chat_id:
        try:
            send(
                "<b>Peg's drawn a blank today</b> — couldn't get a clean read, "
                "so no verdict rather than a bad one. Back tomorrow.",
                token, chat_id,
            )
        except NotifyError:
            pass
    sys.exit(1)


if __name__ == "__main__":
    main()
