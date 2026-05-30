"""
Set-once configuration for Peg (§6a of the PRD).
Edit this file once; never ask the user again.
"""

# Washing line location
LAT: float = 52.387189
LON: float = -1.881414

# Daily window
HANG_HOUR: int = 9    # earliest the washing goes out
BRING_IN_HOUR: int = 18  # latest it must be in (dusk caps this automatically)

# Open-Meteo settings
TIMEZONE: str = "Europe/London"
