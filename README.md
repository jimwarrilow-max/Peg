# Peg 🧺

> *Pegs out, or leave it. Peg knows.*

Peg is a personal weather bot that tells you each morning whether today is a good day to hang washing outside. It sends a Telegram message with a verdict, a score, and the best window to get it out and back in — before you've even thought about it.

## How it works

Every morning at 06:45 GMT (07:45 BST), a GitHub Actions workflow:

1. Fetches today's hourly forecast from [Open-Meteo](https://open-meteo.com) for your location
2. Scores the drying conditions using a physics-based model (Vapour Pressure Deficit + wind + solar radiation)
3. Sends a Telegram verdict
4. Appends a prediction row to `log.csv` for later calibration

## The scoring model

The score (0–100) is built on **Vapour Pressure Deficit (VPD)** — the air's unsatisfied thirst for water. VPD already encodes temperature and humidity in their correct physical relationship, so cold-dry-breezy correctly beats warm-muggy-still.

```
es(T)  = 0.6108 · exp(17.27·T / (T + 237.3))   # saturation vapour pressure, kPa
VPD    = es(T) · (1 − RH/100)                    # the drying driver

hourly = 0.50·vpd_score + 0.30·wind_score + 0.20·solar_score
score  = clamp(50 × Σhourly / 4.0, 0, 100)
```

Rain gates any hour to zero potential. A "risky bring-in" override triggers if rain is forecast in the final 2 hours of the window.

| Score | Verdict |
|---|---|
| 80–100 | Crack open the pegs |
| 55–79 | Good drying day |
| 35–54 | Marginal |
| 0–34 | Tumble-dryer weather |

## Setup

1. **Fork or clone** this repo
2. **Edit `config.py`** — set your lat/lon, hang time, and bring-in time
3. **Add two GitHub Actions secrets:**
   - `TELEGRAM_TOKEN` — from [@BotFather](https://t.me/BotFather)
   - `TELEGRAM_CHAT_ID` — your chat ID (message [@userinfobot](https://t.me/userinfobot))
4. **Merge to `main`** — the scheduler starts automatically

To send a test message immediately: **Actions → Peg — morning forecast → Run workflow**.

## Running locally

```bash
python run.py
```

Prints the verdict to stdout. Telegram is skipped if `TELEGRAM_TOKEN` / `TELEGRAM_CHAT_ID` are not set.

## Project structure

| File | Purpose |
|---|---|
| `scorer.py` | Pure scoring function — no I/O |
| `fetch.py` | Open-Meteo fetch and transform |
| `messages.py` | Telegram message templates (Peg's voice) |
| `notify.py` | Telegram transport |
| `log.py` | CSV prediction log |
| `run.py` | Daily entrypoint |
| `config.py` | Set-once location and times |
| `log.csv` | Prediction history (committed daily) |
| `.github/workflows/peg.yml` | Scheduler |

## Tests

```bash
pip install pytest hypothesis
pytest
```

110 tests covering the scoring invariants (property-based), fetch-transform logic, message formatting, and log idempotency.
