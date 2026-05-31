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
    token    = os.environ.get("TELEGRAM_TOKEN")
    chat_ids = _chat_ids()

    if token and chat_ids:
        for chat_id in chat_ids:
            try:
                send(message, token, chat_id)
                print(f"Telegram: sent to {chat_id}.")
            except NotifyError as exc:
                print(f"Telegram: failed for {chat_id} — {exc}", file=sys.stderr)
    else:
        print("Telegram: TELEGRAM_TOKEN / TELEGRAM_CHAT_ID not set — skipping send.")

    # --- Log -------------------------------------------------------------
    tomorrow = date.today() + timedelta(days=1)
    append_prediction(tomorrow, result, cfg, hours)
    print(f"Log: row written for {tomorrow}.")


def _chat_ids() -> list[str]:
    """Return a list of configured chat IDs (primary + optional second)."""
    ids = []
    for key in ("TELEGRAM_CHAT_ID", "TELEGRAM_CHAT_ID_2"):
        val = os.environ.get(key, "").strip()
        if val:
            ids.append(val)
    return ids


def _fail(reason: str) -> None:
    """Send a failure ping if credentials are available, then exit non-zero."""
    print(f"\nPeg's drawn a blank — {reason}", file=sys.stderr)
    token = os.environ.get("TELEGRAM_TOKEN")
    for chat_id in _chat_ids():
        if token:
            try:
                send(
                    "<b>Peg's drawn a blank</b> — couldn't get a clean read on tomorrow, "
                    "so no verdict rather than a bad one. Back tomorrow evening.",
                    token, chat_id,
                )
            except NotifyError:
                pass
    sys.exit(1)


if __name__ == "__main__":
    main()
