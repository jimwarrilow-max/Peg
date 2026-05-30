"""
Peg — hand-runnable entrypoint for Phase 2.

Usage:
    python run.py

Fetches today's forecast, scores it, and prints the verdict to stdout.
Telegram delivery comes in Phase 3.
"""

from __future__ import annotations

import sys

import config
from fetch import FetchError, fetch_forecast
from scorer import Band, ScoreResult, WindowConfig, score


def main() -> None:
    print("Peg is checking the forecast…")

    try:
        hours, dusk_hour = fetch_forecast(config.LAT, config.LON, config.TIMEZONE)
    except FetchError as exc:
        print(f"\nPeg's drawn a blank — {exc}")
        print("No verdict rather than a bad one. Back tomorrow.")
        sys.exit(1)

    cfg = WindowConfig(
        hang_hour=config.HANG_HOUR,
        bring_in_hour=config.BRING_IN_HOUR,
        dusk_hour=dusk_hour,
    )
    result = score(hours, cfg)
    _print_verdict(result)


def _fmt_hour(h: int) -> str:
    return f"{h:02d}:00"


def _print_verdict(result: ScoreResult) -> None:
    sep = "─" * 44

    if result.skipped:
        print(f"\n{sep}")
        print("  Peg's drawn a blank.")
        print(f"  {result.reason}")
        print(sep)
        return

    print(f"\n{sep}")

    if result.override:
        print("  ⚠  Risky bring-in")
        print(f"  {result.reason}")
    else:
        print(f"  {result.band.value}")

    print(f"  Score: {result.display_score}/100  (raw {result.raw_score:.1f})")
    print(f"  Towels will dry: {'yes' if result.will_dry else 'no'}")

    if not result.override:
        print(f"  {result.reason}")

    if result.best_window:
        start, end = result.best_window
        print(f"  Best window: {_fmt_hour(start)} – {_fmt_hour(end)}")
    else:
        print("  No contiguous window reaches the drying target today.")

    if result.gust_flag:
        print("  ⚠  Gusts >32 mph in window — washing may not be safe outside.")

    print(sep)


if __name__ == "__main__":
    main()
