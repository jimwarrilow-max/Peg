"""
Telegram notification transport for Peg.

Deliberately thin — takes a pre-formatted string and sends it.
Swap this module to change the delivery channel without touching
any other code.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

_TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"


class NotifyError(Exception):
    """Raised when the message could not be delivered."""


def send(message: str, token: str, chat_id: str) -> None:
    """
    Send `message` to `chat_id` via the Telegram Bot API.

    Uses HTML parse mode so the §8 templates render correctly.
    Raises NotifyError on any delivery failure.
    """
    url = _TELEGRAM_API.format(token=token)
    payload = json.dumps({
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "HTML",
    }).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status != 200:
                raise NotifyError(f"Telegram returned HTTP {resp.status}")
    except urllib.error.URLError as exc:
        raise NotifyError(f"Network error sending to Telegram: {exc}") from exc
