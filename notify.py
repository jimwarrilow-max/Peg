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

_TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"


class NotifyError(Exception):
    """Raised when the message could not be delivered."""


def send(message: str, token: str, chat_id: str) -> None:
    """
    Send `message` to `chat_id` via the Telegram Bot API.

    Uses HTML parse mode so the §8 templates render correctly.
    Raises NotifyError on any delivery failure.
    """
    _post(token, "sendMessage", {
        "chat_id":    chat_id,
        "text":       message,
        "parse_mode": "HTML",
    })


def send_with_keyboard(
    message: str,
    keyboard: list[list[dict]],
    token: str,
    chat_id: str,
) -> None:
    """Send a message with a Telegram inline keyboard."""
    _post(token, "sendMessage", {
        "chat_id":      chat_id,
        "text":         message,
        "parse_mode":   "HTML",
        "reply_markup": {"inline_keyboard": keyboard},
    })


def answer_callback(callback_query_id: str, token: str) -> None:
    """Acknowledge a callback query so Telegram clears the loading spinner."""
    _post(token, "answerCallbackQuery", {"callback_query_id": callback_query_id})


def get_updates(token: str, offset: int = 0) -> list[dict]:
    """
    Return pending updates (callback_query only) from the given offset.
    Offset 0 returns all recent updates; pass last_update_id + 1 to advance.
    """
    result = _post(token, "getUpdates", {
        "offset":          offset,
        "timeout":         0,
        "allowed_updates": ["callback_query"],
    })
    return result.get("result", [])


# ---------------------------------------------------------------------------
# Internal
# ---------------------------------------------------------------------------

def _post(token: str, method: str, payload: dict) -> dict:
    url  = _TELEGRAM_API.format(token=token, method=method)
    data = json.dumps(payload).encode("utf-8")
    req  = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        raise NotifyError(f"Network error contacting Telegram: {exc}") from exc
    if not body.get("ok"):
        raise NotifyError(f"Telegram API error: {body.get('description', body)}")
    return body
