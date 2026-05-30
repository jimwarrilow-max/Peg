"""
Message formatting for Peg — the §8 templates, rendered as Telegram HTML.

format_message(result, hang_hour, bring_in_hour, dusk_hour, hours) → str

All copy decisions live here. Nothing else in the codebase should know
about Peg's voice.
"""

from __future__ import annotations

from scorer import (
    Band,
    HourForecast,
    LATE_RAIN_HOURS,
    RAIN_MM_GATE,
    RAIN_PROB_GATE,
    ScoreResult,
)

_COLD_GLOAT_THRESHOLD_C = 15.0   # mean window temp below which Peg gloats
_GLOAT_LINE = " Nippy, isn't it? Cold, dry and breezy beats warm and muggy every time."


def format_message(
    result: ScoreResult,
    hang_hour: int,
    bring_in_hour: int,
    dusk_hour: int,
    hours: list[HourForecast],
) -> str:
    """Return a Telegram-HTML-formatted verdict string."""
    if result.skipped:
        return (
            "<b>Peg's drawn a blank today</b> — couldn't get a clean read, "
            "so no verdict rather than a bad one. Back tomorrow."
        )

    score = result.display_score
    hang_str = _fmt_hour(hang_hour)

    if result.override:
        rain_hour = _first_late_rain_hour(hours, bring_in_hour, dusk_hour)
        rain_str = _fmt_hour(rain_hour) if rain_hour is not None else "later"
        return (
            f"⚠️ <b>Peg's waving you off.</b> Lovely till {rain_str}, then rain "
            f"before you'd get it down. Tempting — it's a trap. Sit this one out."
        )

    end_hour = result.best_window[1] if result.best_window else min(bring_in_hour, dusk_hour)
    dry_by_str = _fmt_hour(end_hour)

    if result.band == Band.CRACK:
        gloat = _GLOAT_LINE if _is_cold(hours, hang_hour, bring_in_hour, dusk_hour) else ""
        return (
            f"🧺 <b>Peg here. Today's a belter — {score}/100.</b> "
            f"Out by {hang_str} and it'll be crisp by {dry_by_str}.{gloat}"
        )

    if result.band == Band.GOOD:
        return (
            f"🧺 <b>Peg's verdict: {score}/100. A solid one.</b> "
            f"Out by {hang_str}, in by {dry_by_str}. "
            f"Won't break records, but it'll get the job done."
        )

    if result.band == Band.MARGINAL:
        return (
            f"🧺 <b>Peg's on the fence — {score}/100.</b> "
            f"It'll <i>probably</i> dry if you're about to dash it in, "
            f"but the heavy stuff might sulk. I'd risk a light load, not the towels."
        )

    # Band.TUMBLE
    return (
        f"🧺 <b>Peg says don't bother. {score}/100.</b> "
        f"Air's too damp to take anything off your hands today. "
        f"Tumble dryer, or wait for tomorrow."
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fmt_hour(h: int) -> str:
    """Return a natural-language hour string: 9am, 12pm, 1pm, etc."""
    if h == 0:
        return "midnight"
    if h == 12:
        return "12pm"
    if h < 12:
        return f"{h}am"
    return f"{h - 12}pm"


def _first_late_rain_hour(
    hours: list[HourForecast],
    bring_in_hour: int,
    dusk_hour: int,
) -> int | None:
    """Return the first rain-gated hour in the final LATE_RAIN_HOURS of the window."""
    end_hour = min(bring_in_hour, dusk_hour)
    late_start = end_hour - LATE_RAIN_HOURS + 1
    by_hour = {h.hour: h for h in hours}
    for hnum in range(late_start, end_hour + 1):
        h = by_hour.get(hnum)
        if h and _is_rain_gated(h):
            return hnum
    return None


def _is_rain_gated(h: HourForecast) -> bool:
    if h.precip_prob_pct is not None and h.precip_prob_pct > RAIN_PROB_GATE:
        return True
    if h.precip_mm is not None and h.precip_mm > RAIN_MM_GATE:
        return True
    return False


def _is_cold(
    hours: list[HourForecast],
    hang_hour: int,
    bring_in_hour: int,
    dusk_hour: int,
) -> bool:
    """True when mean temp over the window is below the gloat threshold."""
    end_hour = min(bring_in_hour, dusk_hour)
    temps = [
        h.temp_c for h in hours
        if hang_hour <= h.hour <= end_hour and h.temp_c is not None
    ]
    if not temps:
        return False
    return (sum(temps) / len(temps)) < _COLD_GLOAT_THRESHOLD_C
