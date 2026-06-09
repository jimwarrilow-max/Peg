"""
Message formatting for Peg — the §8 templates, rendered as Telegram HTML.

format_message(result, hang_hour, bring_in_hour, dusk_hour) → str

All copy decisions live here. Nothing else in the codebase should know
about Peg's voice.  All weather analysis lives in scorer.ScoreResult —
messages.py only formats.
"""

from __future__ import annotations

from scorer import Band, ScoreResult

SKIPPED_MSG = (
    "<b>Peg's drawn a blank</b> — couldn't get a clean read on tomorrow, "
    "so no verdict rather than a bad one. Back tomorrow around 5pm."
)

_UV_LABELS = [
    (0,   "Low"),
    (3,   "Moderate"),
    (6,   "High"),
    (8,   "Very High"),
    (11,  "Extreme"),
]


def _uv_label(uv: float) -> str:
    return next((name for threshold, name in reversed(_UV_LABELS) if uv >= threshold), "Low")


def _fmt_hour(h: int) -> str:
    """Return a natural-language hour string: 9am, 12pm, 1pm, etc."""
    if h == 0:
        return "midnight"
    if h == 12:
        return "12pm"
    if h < 12:
        return f"{h}am"
    return f"{h - 12}pm"


def _conditions_line(result: ScoreResult) -> str:
    parts = []
    if result.mean_temp_c  is not None: parts.append(f"🌡️ {round(result.mean_temp_c)}°C")
    if result.mean_wind_mph is not None: parts.append(f"💨 {round(result.mean_wind_mph)}mph")
    if result.mean_rh_pct   is not None: parts.append(f"💧 {round(result.mean_rh_pct)}% humidity")
    return ("\n" + " · ".join(parts)) if parts else ""


def _rain_line(result: ScoreResult, hang_hour: int) -> str:
    if result.window_rain_hour is None:
        return ""
    prob_str = f" ({int(result.window_rain_prob)}%)" if result.window_rain_prob is not None else ""
    if result.window_rain_hour <= hang_hour:
        return f"\n🌧️ Rain from {_fmt_hour(result.window_rain_hour)}{prob_str}"
    return (
        f"\n🌧️ Dry till {_fmt_hour(result.window_rain_hour - 1)}"
        f" · Rain from {_fmt_hour(result.window_rain_hour)}{prob_str}"
    )


def _uv_line(result: ScoreResult) -> str:
    if result.peak_uv is None:
        return ""
    return f"\n☀️ UV index: {result.peak_uv:.0f} ({_uv_label(result.peak_uv)})"


def _near_rain_line(result: ScoreResult) -> str:
    if result.near_rain_hour is None or result.near_rain_prob is None:
        return ""
    return f"\n⚠️ Note: {int(result.near_rain_prob)}% chance of rain at {_fmt_hour(result.near_rain_hour)}"


def _window_tip(best_window: tuple[int, int] | None) -> str:
    if not best_window:
        return ""
    return f" Best window: {_fmt_hour(best_window[0])}–{_fmt_hour(best_window[1])}."


def format_message(
    result: ScoreResult,
    hang_hour: int,
    bring_in_hour: int,
    dusk_hour: int,
) -> str:
    """Return a Telegram-HTML-formatted verdict string."""
    if result.skipped:
        return SKIPPED_MSG

    score = result.display_score
    cond      = _conditions_line(result)
    rain      = _rain_line(result, hang_hour)
    uv        = _uv_line(result)
    near_rain = _near_rain_line(result)

    if result.override:
        # Use the first rain in the full window for the headline timing.
        # first_rain_hour covers only the final 2h; window_rain_hour covers everything.
        rain_hour = result.window_rain_hour if result.window_rain_hour is not None else result.first_rain_hour
        rain_str = _fmt_hour(rain_hour) if rain_hour is not None else "later"
        if result.band == Band.TUMBLE:
            return (
                f"🧺 <b>Peg says don't bother tomorrow. {score}/100.</b> "
                f"Air'll be too damp to dry anything — and rain arrives at {rain_str} "
                f"before bring-in time anyway."
                f"{cond}{rain}{near_rain}{uv}"
            )
        window_tip = _window_tip(result.best_window)
        return (
            f"⚠️ <b>Peg's cautious — {score}/100.</b> Drying conditions hold till {rain_str}, "
            f"then rain before bring-in time. Fine if you're home to dash it in early "
            f"— risky if you're out all day.{window_tip}"
            f"{cond}{rain}{near_rain}{uv}"
        )

    # Recommended hang and bring-in times from the best scoring window
    window_end = min(bring_in_hour, dusk_hour)
    start_hour = result.best_window[0] if result.best_window else hang_hour
    end_hour   = result.best_window[1] if result.best_window else window_end
    start_str  = _fmt_hour(start_hour)
    dry_by_str = _fmt_hour(end_hour)
    # "Out by 9am" when window starts at the earliest possible time; "Hold off till 1pm" otherwise
    hang_advice = f"Out by {start_str}" if start_hour == hang_hour else f"Hold off till {start_str}"

    if result.band == Band.CRACK:
        return (
            f"🧺 <b>Peg here. Tomorrow's a belter — {score}/100.</b> "
            f"{hang_advice} — it'll be crisp by {dry_by_str}."
            f"{cond}{rain}{near_rain}{uv}"
        )

    if result.band == Band.GOOD:
        return (
            f"🧺 <b>Peg's verdict: {score}/100. A solid one.</b> "
            f"{hang_advice}, in by {dry_by_str}. "
            f"Won't break records, but it'll get the job done."
            f"{cond}{rain}{near_rain}{uv}"
        )

    if result.band == Band.MARGINAL:
        window_tip = _window_tip(result.best_window)
        return (
            f"🧺 <b>Peg's on the fence — {score}/100.</b> "
            f"It'll <i>probably</i> dry if you're about to dash it in, "
            f"but the heavy stuff might sulk. I'd risk a light load, not the towels."
            f"{window_tip}"
            f"{cond}{rain}{near_rain}{uv}"
        )

    # Band.TUMBLE
    return (
        f"🧺 <b>Peg says don't bother tomorrow. {score}/100.</b> "
        f"Air'll be too damp to take anything off your hands. "
        f"Tumble dryer, or hold on."
        f"{cond}{rain}{near_rain}{uv}"
    )
