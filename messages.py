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

_UV_LABELS = [
    (0,   "Low"),
    (3,   "Moderate"),
    (6,   "High"),
    (8,   "Very High"),
    (11,  "Extreme"),
]


def _uv_label(uv: float) -> str:
    label = _UV_LABELS[0][1]
    for threshold, name in _UV_LABELS:
        if uv >= threshold:
            label = name
    return label


def _peak_uv_line(hours: list[HourForecast], hang_hour: int, bring_in_hour: int, dusk_hour: int) -> str:
    end_hour = min(bring_in_hour, dusk_hour)
    uv_vals = [
        h.uv_index for h in hours
        if hang_hour <= h.hour <= end_hour and h.uv_index is not None
    ]
    if not uv_vals:
        return ""
    peak = max(uv_vals)
    return f"\n☀️ UV index: {peak:.0f} ({_uv_label(peak)})"


def _conditions_line(hours: list[HourForecast], hang_hour: int, end_hour: int) -> str:
    """One-line summary of mean conditions over hang_hour..end_hour (inclusive)."""
    window = [h for h in hours if hang_hour <= h.hour <= end_hour]
    parts = []
    temps = [h.temp_c  for h in window if h.temp_c  is not None]
    winds = [h.wind_mph for h in window if h.wind_mph is not None]
    rhs   = [h.rh_pct   for h in window if h.rh_pct   is not None]
    if temps:
        parts.append(f"🌡️ {round(sum(temps)/len(temps))}°C")
    if winds:
        parts.append(f"💨 {round(sum(winds)/len(winds))}mph")
    if rhs:
        parts.append(f"💧 {round(sum(rhs)/len(rhs))}% humidity")
    return ("\n" + " · ".join(parts)) if parts else ""


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
            "<b>Peg's drawn a blank</b> — couldn't get a clean read on tomorrow, "
            "so no verdict rather than a bad one. Back tomorrow evening."
        )

    score = result.display_score
    hang_str = _fmt_hour(hang_hour)

    window_end = min(bring_in_hour, dusk_hour)
    uv_line = _peak_uv_line(hours, hang_hour, bring_in_hour, dusk_hour)

    if result.override:
        rain_hour = _first_late_rain_hour(hours, bring_in_hour, dusk_hour)
        rain_str = _fmt_hour(rain_hour) if rain_hour is not None else "later"
        good_end = (rain_hour - 1) if rain_hour is not None else window_end
        cond = _conditions_line(hours, hang_hour, good_end)
        return (
            f"⚠️ <b>Peg's cautious — {score}/100.</b> Good drying till {rain_str}, "
            f"then rain before bring-in time. Fine if you're home to dash it in early "
            f"— risky if you're out all day."
            f"{cond}{uv_line}"
        )

    end_hour = result.best_window[1] if result.best_window else window_end
    dry_by_str = _fmt_hour(end_hour)
    cond = _conditions_line(hours, hang_hour, window_end)

    if result.band == Band.CRACK:
        gloat = _GLOAT_LINE if _is_cold(hours, hang_hour, bring_in_hour, dusk_hour) else ""
        return (
            f"🧺 <b>Peg here. Tomorrow's a belter — {score}/100.</b> "
            f"Out by {hang_str} and it'll be crisp by {dry_by_str}.{gloat}"
            f"{cond}{uv_line}"
        )

    if result.band == Band.GOOD:
        return (
            f"🧺 <b>Peg's verdict: {score}/100. A solid one.</b> "
            f"Out by {hang_str}, in by {dry_by_str}. "
            f"Won't break records, but it'll get the job done."
            f"{cond}{uv_line}"
        )

    if result.band == Band.MARGINAL:
        return (
            f"🧺 <b>Peg's on the fence — {score}/100.</b> "
            f"It'll <i>probably</i> dry if you're about to dash it in, "
            f"but the heavy stuff might sulk. I'd risk a light load, not the towels."
            f"{cond}{uv_line}"
        )

    # Band.TUMBLE
    return (
        f"🧺 <b>Peg says don't bother tomorrow. {score}/100.</b> "
        f"Air'll be too damp to take anything off your hands. "
        f"Tumble dryer, or hold on."
        f"{cond}{uv_line}"
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
