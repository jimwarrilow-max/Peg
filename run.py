"""
Peg — daily forecast entrypoint.

Fetch → score → format → notify (Telegram) → log (CSV).

Environment variables (set as GitHub Actions secrets in production;
omit locally to print-only mode):
  TELEGRAM_TOKEN     — bot token
  TELEGRAM_CHAT_ID   — primary recipient chat ID
  TELEGRAM_CHAT_ID_2 — optional second recipient chat ID
"""

from __future__ import annotations

import os
import sys
from datetime import date, timedelta, timezone

import config
from fetch import FetchError, fetch_forecast
from log import append_prediction
from messages import SKIPPED_MSG, format_message
from notify import NotifyError, broadcast, send
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
    message = format_message(result, config.HANG_HOUR, config.BRING_IN_HOUR, dusk_hour)
    print(message)

    # --- Notify ----------------------------------------------------------
    token    = os.environ.get("TELEGRAM_TOKEN")
    chat_ids = config.chat_ids()

    if token and chat_ids:
        def _send_one(chat_id: str) -> None:
            send(message, token, chat_id)
            print(f"Telegram: sent to {chat_id}.")
        failures = broadcast(chat_ids, _send_one)
        if failures == len(chat_ids):
            sys.exit(1)
    else:
        print("Telegram: TELEGRAM_TOKEN / TELEGRAM_CHAT_ID not set — skipping send.")

    # --- Log -------------------------------------------------------------
    tomorrow = date.today() + timedelta(days=1)
    append_prediction(tomorrow, result, cfg, hours)
    print(f"Log: row written for {tomorrow}.")


def _fail(reason: str) -> None:
    """Send a failure ping if credentials are available, then exit non-zero."""
    print(f"\nPeg's drawn a blank — {reason}", file=sys.stderr)
    token = os.environ.get("TELEGRAM_TOKEN")
    if token:
        broadcast(config.chat_ids(), lambda cid: send(SKIPPED_MSG, token, cid))
    sys.exit(1)


if __name__ == "__main__":
    main()
