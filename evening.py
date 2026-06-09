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

import config
from log import read_band
from notify import broadcast, send_with_keyboard
from scorer import Band


def main() -> None:
    token    = os.environ.get("TELEGRAM_TOKEN")
    chat_ids = config.chat_ids()

    if not token or not chat_ids:
        print("TELEGRAM_TOKEN / TELEGRAM_CHAT_ID not set — skipping.")
        return

    today = date.today().isoformat()
    band = read_band(today)
    if band is None:
        print(f"No prediction logged for {today} — skipping evening prompt.")
        return
    if band == Band.TUMBLE.value:
        print(f"Peg said don't bother today — skipping evening prompt.")
        return
    keyboard = [[
        {"text": "👍 Bone dry",      "callback_data": f"dry:{today}"},
        {"text": "👎 Still damp",    "callback_data": f"damp:{today}"},
        {"text": "⏭️ Didn't hang",  "callback_data": f"skip:{today}"},
    ]]

    prompt = (
        "<b>Evening! How'd I do — did it dry?</b>\n"
        "Honest answers make me sharper."
    )

    def _send_one(chat_id: str) -> None:
        send_with_keyboard(prompt, keyboard, token, chat_id)
        print(f"Evening prompt sent to {chat_id}.")

    failures = broadcast(chat_ids, _send_one)
    if failures == len(chat_ids):
        sys.exit(1)


if __name__ == "__main__":
    main()
