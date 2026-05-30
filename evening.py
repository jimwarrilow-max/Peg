"""
Peg — evening outcome prompt.

Sends a Telegram message with 👍/👎 inline buttons so the user can record
whether the washing actually dried.  The callback_data encodes today's date
so outcome.py knows which log row to update.
"""

from __future__ import annotations

import os
import sys
from datetime import date

from notify import NotifyError, send_with_keyboard


def main() -> None:
    token   = os.environ.get("TELEGRAM_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        print("TELEGRAM_TOKEN / TELEGRAM_CHAT_ID not set — skipping.")
        return

    today = date.today().isoformat()
    keyboard = [[
        {"text": "👍 Bone dry",   "callback_data": f"dry:{today}"},
        {"text": "👎 Still damp", "callback_data": f"damp:{today}"},
    ]]

    try:
        send_with_keyboard(
            "<b>Evening! How'd I do — did it dry?</b>\n"
            "Honest answers make me sharper.",
            keyboard, token, chat_id,
        )
        print("Evening prompt sent.")
    except NotifyError as exc:
        print(f"Failed to send evening prompt: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
