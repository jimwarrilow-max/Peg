"""
Peg — outcome processor.

Polls Telegram for pending 👍/👎 callback queries and writes the result
back to the matching row in log.csv.

State: the last-processed Telegram update ID is stored in .peg_offset
(committed to the repo) so updates are never processed twice.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from log import VALID_OUTCOMES, write_outcome
from notify import NotifyError, answer_callback, get_updates, send

OFFSET_FILE = ".peg_offset"

_CONFIRM_MSG = "Thanks — noted. Every answer makes Peg sharper 📊"


def main() -> None:
    token = os.environ.get("TELEGRAM_TOKEN")
    if not token:
        print("TELEGRAM_TOKEN not set — skipping outcome processing.")
        return

    offset = _load_offset()

    try:
        updates = get_updates(token, offset)
    except NotifyError as exc:
        print(f"Could not fetch Telegram updates: {exc}", file=sys.stderr)
        return

    if not updates:
        print("No new Telegram updates.")
        return

    new_offset = offset
    for update in updates:
        new_offset = max(new_offset, update["update_id"] + 1)

        cq = update.get("callback_query")
        if not cq:
            continue

        data = cq.get("data", "")
        parts = data.split(":", 1)
        if len(parts) != 2 or parts[0] not in VALID_OUTCOMES:
            continue

        outcome, date_str = parts

        if write_outcome(date_str, outcome):
            print(f"Outcome recorded: {date_str} → {outcome}")
        else:
            print(f"No log row for {date_str} — outcome not recorded.")

        try:
            answer_callback(cq["id"], token)
        except NotifyError as exc:
            print(f"Could not acknowledge callback: {exc}", file=sys.stderr)

        try:
            chat_id = str(cq["from"]["id"])
            send(_CONFIRM_MSG, token, chat_id)
        except (NotifyError, KeyError) as exc:
            print(f"Could not send confirmation: {exc}", file=sys.stderr)

    _save_offset(new_offset)
    print(f"Telegram offset advanced to {new_offset}.")


def _load_offset() -> int:
    try:
        return int(Path(OFFSET_FILE).read_text().strip())
    except (FileNotFoundError, ValueError):
        return 0


def _save_offset(offset: int) -> None:
    Path(OFFSET_FILE).write_text(str(offset))


if __name__ == "__main__":
    main()
