# Good Drying Day — Product Requirements Document (v1.7)

**Status:** Draft · **Owner:** Jim · **Last updated:** 30 May 2026
*Brand: **Peg** · App concept: Good Drying Day*

> **Changelog**
> - **v1.7** — Added **Phase 4: Conversational Peg** to the roadmap (ad-hoc questions via a Telegram webhook + serverless listener; LLM parses intent; the pure scorer stays the sole judge — "parse, don't judge"). Multi-user → Phase 5, intra-day/ML → Phase 6.
> - **v1.6** — Added brand & voice (Peg) and ready-to-paste message templates for every band and state to §8.
> - **v1.5** — **VPD is computed from temp+RH again**, reversing v1.3. Live testing (31 May 2026, Wythall) found Open-Meteo's `vapour_pressure_deficit` field returning **0.00 across all hours despite 50–66% RH** — a silent error that understated the day by ~40 points (would have read "Marginal 45" instead of "Crack open the pegs 85"). Temp + RH are reliably populated, so VPD is derived from them with a sanity check. `es(T)` formula reinstated.
> - **v1.4** — Closed five QA spec gaps: raw-score banding; score/band/will_dry tied; "Risky bring-in" override; null/partial-field handling.
> - **v1.3** — (superseded by v1.5) VPD/ET₀ fetched directly; `es(T)` removed.
> - **v1.2** — VPD three-layer model; parameter table; output contract; conservative-bias principle.
> - **v1.1** — Calibration log, outcome capture, operational/analytical state split, scheduler specifics, selection-bias caveat.

---

## 1. Summary

A personal app that proactively tells you, each morning, whether today is a good day to hang washing outside — with a verdict, a plain-English reason, and the best window to get it out and back in. It keeps a quiet logbook of prediction versus outcome, so its accuracy improves over time. Built for a two-person household; designed so it *can* grow later without being over-built now. The bot's persona is **Peg** — a weatherwise, quietly smug British line-drying lookout (voice & templates in §8).

## 2. Problem

The real failure isn't *not knowing* whether it's a good drying day — it's **finding out too late** (realising at 11am that it's perfect, window half gone). A page you have to remember to open doesn't fix this. So the product's defining job is to **reach out first**.

Secondary: "a good drying day" is widely misjudged (warm = good is wrong), so people read the sky and get it wrong. The app should correct this *and* learn from real outcomes for this specific garden.

## 3. Goals & Non-Goals

**Goals (v1)**
- Proactive morning notification with a verdict + recommended window.
- Trustworthy — never call a day "great" when rain is coming.
- Zero per-use friction to *get* the verdict.
- Explain *why* (and settle warm-vs-windy).
- Improve its own accuracy over time from logged outcomes.

**Non-Goals (v1)**
- No native iOS/Android app.
- No multi-user, accounts, or per-person locations.
- No WhatsApp delivery (Telegram for now).
- No intra-day "it just turned good" alerting.
- No conversational/chat interaction — Peg speaks on a schedule in v1; ad-hoc questions are Phase 4 (§12).
- **No managed database.** A flat-file/spreadsheet *logbook* is in scope; database infrastructure is not, until multi-user forces it.

## 4. Users

- **Primary:** Jim + partner — one home, one washing line.
- **The implicit "user": the washing line.** Location is a property of the *line*, not the phone — the forecast must be where the washing hangs. Hence location is stored, not detected live.

## 5. Product Principles

- **It's not about warmth.** Humidity and wind dominate; cold-dry-sunny beats warm-humid. (The VPD model in §7 makes this fall out of the physics.)
- **Ask once, never again.** Day-invariant facts are set-once config, not daily questions.
- **The "does it change the output?" test.** Any input/setting that can't flip the verdict is clutter.
- **Design for the limiting fabric.** Score for heavy items (towels). Score, band, and `will_dry` all speak for this fabric.
- **Score the usable window, hour by hour — never a daily average.**
- **Separate operational state from analytical state.** The pipeline needs no memory to do its job; the logbook is a write-only side-channel read offline.
- **Supply a decision, not a number.** The band, reason, and window are the product; the 0–100 score is texture.
- **Bias conservative — the costs are asymmetric.** A false "great day" that ends soggy destroys trust; a missed decent day costs nothing.
- **Validate external data; don't blindly trust derived fields.** A plausible-but-wrong value (e.g. a VPD field returning 0 despite real humidity — observed in testing) is worse than a missing one. Compute from reliable primitives when a derived field can't be trusted.
- **Never emit a confident verdict on incomplete data.** Missing inputs mean skip, not guess.

## 6. Inputs

### 6a. Set-once preferences (stored config)

| Input | Why it's needed | Notes |
|---|---|---|
| Home location (lat/long) | Forecast must be for the line | Property of the line; looked up once |
| Likely hang time | Feasibility + which hours to score | The realistic time, not a 7am fiction |
| Latest bring-in time | End of the window | Capped by dusk — dew re-wets |
| Recipients / channel | Who gets the ping | Telegram chat ID(s) |

### 6b. Fetched-fresh data (Open-Meteo)

| Factor | Role | Open-Meteo field |
|---|---|---|
| Rain | **Hard gate** | `precipitation`, `precipitation_probability` (hourly) |
| Temp + RH | **Required — VPD is computed from these** (also logged as context) | `temperature_2m`, `relative_humidity_2m` (hourly) |
| **VPD** | Dominant continuous driver — **computed from temp+RH** (API field unreliable, §7) | *derived in the transform* |
| Wind | Accelerator, capped | `wind_speed_10m` (hourly, mph) |
| Gust | Practicality flag | `wind_gusts_10m` (hourly) |
| Solar | Energy input | `shortwave_radiation` (hourly, W/m²) |
| Reference ET₀ | Reserved for the future "proper route"; logged now — **validate before relying on it** | `et0_fao_evapotranspiration` (hourly) |
| Daylight / dusk | Window cap | `sunrise`, `sunset` (daily) |

**API request settings:** add `wind_speed_unit=mph` and `timezone=Europe/London`. The docs page builds the exact query URL when you tick variables.

### 6c. Captured outcomes (for calibration)

| Input | Why it's needed | Source |
|---|---|---|
| Outcome label ("did it dry?") | Ground truth to calibrate against | Human — evening Telegram tap (👍/👎) or spreadsheet cell |

## 7. Scoring Methodology

**Route (v1):** a physically-anchored **weighted model built on Vapour Pressure Deficit (VPD)** — the air's unsatisfied thirst for water, which dries washing. VPD already contains temperature and humidity in their correct physical relationship, so temperature is *not* scored separately. Upgradeable later via ET₀.

**VPD source: computed in the transform from temp + RH.**
```
es(T) = 0.6108 · exp( 17.27·T / (T + 237.3) )     # saturation vapour pressure, kPa
VPD   = es(T) · (1 − RH/100)                        # the drying driver, kPa
```
*Why computed, not fetched:* live testing (31 May 2026, Wythall) found Open-Meteo's `vapour_pressure_deficit` returning **0.00 every hour** despite 50–66% RH — a silent, plausible-looking error. Temp and RH are reliably populated, so we derive VPD ourselves. **Sanity check:** VPD must be ≥ 0 and broadly consistent with RH; if a fetched VPD is ever used, accept it only when non-zero at RH < ~95%, else fall back to the computed value.
*Hand-test fixtures:* cool-dry **10°C/50% RH → VPD ≈ 0.61 kPa** out-dries warm-muggy **22°C/85% RH → VPD ≈ 0.40 kPa**. Fog (RH ≈ 100%) → VPD ≈ 0, rejected for free.

### Layer 1 — Gates (hard, not weighted)
- **Rain:** an hour with `precipitation_probability > 50%` or `precipitation > 0.2 mm` scores **0**. If gated in the final ~2h of the window → trigger the **"Risky bring-in" override** (§8).
- **Window:** must hold enough usable daylight hours to reach the drying target.
- **Missing/partial data:** an hour missing any required scoring field (temp, RH, wind, solar, precipitation, precipitation_probability) is **unscorable** and **excluded** — never treated as zero. If **>25% of window hours are unscorable**, or the scorable hours can't reach `DRY_TARGET` even at full potential, **skip the day** (fail quiet + ping).

### Layer 2 — Per-hour drying potential (0–1)

| Feature | Sub-score curve | Reaches 1.0 at |
|---|---|---|
| **VPD** | `clamp(VPD / 1.0, 0, 1)` | VPD ≥ 1.0 kPa |
| **Wind** | `clamp(0.25 + mph/16, 0, 1)` | ~12 mph (0.25 floor in still air) |
| **Solar** | `clamp(rad / 450, 0, 1)` | ~450 W/m² |

```
hourly_potential = 0.50·vpd + 0.30·wind + 0.20·solar     # × 0 if rain-gated
```
Wind has a floor (washing dries in still air, slowly) and saturates around the 8–12 mph sweet spot (beyond that it only risks the load — separate `wind_gusts_10m > 32 mph` flag).

### Layer 3 — Integrate across the window
```
cumulative = Σ hourly_potential        # over scorable hours in [hang → min(bring_in, dusk)]
will_dry   = cumulative ≥ DRY_TARGET    # 4.0 towels, ~2.5 light
```
`DRY_TARGET = 4.0` = "four perfect hours," for the **limiting fabric** (towels). **Best window** = earliest contiguous run of scorable hours reaching the target.
**Coherence guarantee:** since `score = 50 × cumulative/DRY_TARGET` (§8), `will_dry` (towels) is true **exactly when `score ≥ 50`** — band and flag cannot contradict.

### Parameters — physics vs calibration knobs

| Constant | Value | Status |
|---|---|---|
| es coefficients | 0.6108, 17.27, 237.3 | **Physics — fixed** |
| VPD_full | 1.0 kPa | Calibrate |
| Wind floor / full | 0.25 / 12 mph | Calibrate |
| Gust flag | 32 mph | Safety |
| Solar_full | 450 W/m² | Calibrate |
| Weights (VPD/wind/solar) | 0.50 / 0.30 / 0.20 | **Prime calibration targets** |
| Rain gate | 50% prob or 0.2 mm/h | Calibrate |
| Late-rain window | final 2h | Calibrate |
| Unscorable-hours limit | 25% of window | Calibrate |
| DRY_TARGET | 4.0 towels / 2.5 light | Calibrate |

## 8. Output & Notification

### Output contract (the score the user sees)
```
ratio = cumulative / DRY_TARGET        # towel target
score = clamp(50 × ratio, 0, 100)      # raw score; floored if rain-gated
```
**Anchored endpoints:** **0** → won't dry / rain in window · **50** → *just* clears the towel bar (zero margin) · **100** → ~2× margin (bone dry, time to spare).

**Three rules on the number:**
1. **Round to the nearest 5 — for display only.** Rounding *never* affects the band (banding uses the raw score).
2. **Never shown alone — always with its band.**
3. **It is an index, NOT a probability.** A real probability only comes once the §10 log can fit a logistic regression (endgame).

### Verdict bands — evaluated on the **raw** score (half-open ranges)

| Raw score | Band | Meaning |
|---|---|---|
| `score < 35` | Tumble-dryer weather | Won't dry / rain in the window |
| `35 ≤ score < 55` | Marginal | Borderline — heavy items may not fully dry; only if you can dash out (lighter loads fare better) |
| `55 ≤ score < 80` | Good drying day | Will dry; out by X, in by Y |
| `80 ≤ score ≤ 100` | Crack open the pegs | Dries comfortably, margin to spare |

*Banding is on the raw score; the rounded display number can sit one step inside an edge and that's fine. "Marginal" straddles the `will_dry` point (score 50), so its copy stays borderline and never promises drying (conservative bias, §5).*

### "Risky bring-in" override
If rain is gated in the **final ~2h** of the window, regardless of the underlying score:
- The verdict **label is replaced** (e.g. *"Risky — rain due before you can get it in"*).
- The shown band is **capped at Marginal** — guarantees INV-07.
- The **raw score is still computed and logged** unchanged.

### Messages
- **Morning verdict:** verdict/override + (rounded) score + one-line reason + recommended window (e.g. *"Your usual 9am works — get it in by 5"*). Later: name the limiting factor; optionally note when towels won't dry but light loads will (§14).
- **Evening outcome prompt (optional):** one-tap 👍/👎, logged. Makes the bot *receive* as well as send.
- **Channel:** Telegram (v1), isolated/swappable `notify()`.

### Brand & voice — Peg
**Who Peg is:** your line-drying lookout — a weatherwise, quietly smug British character who shouts up the moment the sky's on your side. Trusted neighbour × weather nerd who's *always* right about the breeze. The name is the product itself, warm and gender-neutral, and sets up the catchphrase.

**Voice rules:**
1. **Brief and bright** — one or two lines, never waffle.
2. **Confident, with a wink** — opinions and a bit of swagger, especially when proven right.
3. **Warmly British** — dry, understated; never American-chirpy, never naggy.
4. **Always actionable** — ends with what to *do* (out by 9, or leave it).
5. **Honest over hype** — a washout gets called a washout (the §5 conservative bias, made human).

**Signature move (the shareable hook):** on a **cold-but-great** day, Peg gloats — *"Nippy, isn't it? Cold, dry and breezy beats warm and muggy every time."* This line settles the warm-vs-windy feud daily and is the bit users screenshot.

**Tagline:** *"Pegs out, or leave it. Peg knows."*

### Message templates (starting copy for `notify()`)
v1 uses these deterministic templates with interpolated placeholders (`{score}`, `{hang}`, `{dry_by}`, `{reason}`, `{rain_time}`); the tone is fixed, the specifics fill in. LLM-generated variety is a possible later flourish (§14) but not needed for v1.

| State (band) | Template |
|---|---|
| Crack open the pegs (80–100) | 🧺 **Peg here. Today's a belter — {score}/100.** {reason}. Out by {hang} and it'll be crisp by {dry_by}.`{gloat?}` |
| Good drying day (55–79) | 🧺 **Peg's verdict: {score}/100. A solid one.** Out by {hang}, in before tea. Won't break records, but it'll get the job done. |
| Marginal (35–54) | 🧺 **Peg's on the fence — {score}/100.** It'll *probably* dry if you're about to dash it in, but the heavy stuff might sulk. I'd risk a light load, not the towels. |
| Tumble-dryer weather (0–34) | 🧺 **Peg says don't bother. {score}/100.** Air's too damp to take anything off your hands today. Tumble dryer, or wait for tomorrow. |
| Risky bring-in (override) | ⚠️ **Peg's waving you off.** Lovely till {rain_time}, then rain before you'd get it down. Tempting — it's a trap. Sit this one out. |
| Evening outcome prompt | **Evening! How'd I do — did it dry?** [👍 Bone dry] · [👎 Still damp] — _honest answers make me sharper._ |
| No-data / skipped day | **Peg's drawn a blank today** — couldn't get a clean read, so no verdict rather than a bad one. Back tomorrow. |

`{gloat?}` = append the signature cold-day line when the day scores well *and* mean temp is low (cold-dry-windy). Copy guidelines: ≤2 lines; 🧺 for a verdict, ⚠️ for the override; never show `{score}` without its band word.

## 9. Key User Stories & Acceptance Criteria

- **Morning verdict, hands-free.** AC: message arrives daily (~hang_time − 1h) via Telegram, no interaction required.
- **Know if today works, and when.** AC: message always contains verdict + recommended window.
- **Honest number.** AC: rounded to nearest 5 for display, **banded on the raw score**, always shown with its band, never labelled a probability.
- **No crying wolf.** AC: rain in window → verdict never "good"; rain in final ~2h → "Risky bring-in" override, band capped at Marginal.
- **No verdict on bad data.** AC: >25% unscorable hours → day skipped (fail quiet + ping); missing fields never read as zero; **VPD computed from temp+RH, sanity-checked, never taken on faith from the API field**.
- **Hang time matters.** AC: same forecast + different hang time → can change the verdict.
- **Set once.** AC: no daily input required.
- **Capture the outcome.** AC: evening prompt; reply stored against that day's row.
- **Learns over time.** AC: inputs + prediction + outcome retrievable together.

## 10. Validation, Calibration & Analytics

**What's logged** — one row/day: date, location, inputs (temp, RH, computed VPD, wind, solar, rain, ET₀), the predicted raw score + band + window + any override, and (when supplied) the human outcome.

**How outcomes are captured** — prediction is automatic; the outcome is the new human input (evening Telegram tap or spreadsheet cell).

**Calibration method (v1 — manual):**
1. Eyeball the **disagreements** first.
2. **False positives** ("great" but stayed damp) — the dangerous ones; usually rain gate too loose or VPD curve too generous.
3. **False negatives** ("poor" but dried) — usually DRY_TARGET too high or wind floor too low.
4. Sort best- vs worst-rated days; see which input separates them.
5. Adjust the **calibration-flagged constants** in §7 by hand, commit. No self-tuning in v1.

**When to model (endgame only):** the log is a supervised dataset. A logistic regression could learn the weights and yield a real probability — but with tens of rows you'll overfit. Eyeball until a few hundred.

**Selection-bias caveat:** you only get outcomes for days you actually hang, and you won't hang on predicted-bad days. To trust the negatives, occasionally peg out a single test towel on a predicted-bad day.

**Success (hobby bar):** correct calls, no rain-soaked false positives, both of you rely on it.

## 11. Architecture & Solutioning

**Shape:** stateless scheduled pipeline — `trigger → fetch → score → notify` — with a write-only logbook as a side-channel.

**Daily flow:** `scheduler fires → fresh runner → checkout → fetch Open-Meteo → transform (compute VPD) → score (pure fn) → send Telegram verdict → append prediction row to log → runner destroyed.`
**Evening:** `outcome prompt → user taps 👍/👎 → bot writes outcome to that day's row.`

**Scheduler — GitHub Actions scheduled workflow** (no server owned):
- YAML in `.github/workflows/`; scheduled runs fire only from the **default branch**.
- `timezone: "Europe/London"`; add `workflow_dispatch` for manual "Run now".
- Timing is **best-effort** (5–30 min delays; can drop at peak) → schedule a little early, off the top of the hour.
- **Fails silently** and **auto-disables after 60 days of repo inactivity** → failure ping + daily log commit keeps it active.

**Fetch-and-transform:** maps Open-Meteo's parallel hourly arrays into per-hour forecast objects sliced to the window, **computing VPD per hour from temp+RH** (`es(T)·(1−RH/100)`) — the one meteorological calculation, done here because the API's VPD field proved unreliable. Also flags unscorable hours (any required field null/missing). ET₀ is fetched and logged but validated before any future use. The scorer still receives VPD ready-made, staying pure and hand-testable (e.g. `vpd: 0.61`).

**Scoring:** a **pure function** — hourly forecast in (VPD already computed) → gate → potential → integrate → `{raw_score, band, override, reason, window, will_dry}` out. Isolated from fetch/notify/log so §7 constants tune without touching plumbing.

**Notify:** isolated `notify()` (Telegram v1); also receives the evening outcome.

**State, split deliberately:**
- *Operational:* **none** — never reads yesterday to decide today.
- *Analytical:* the **calibration log** — written by the pipeline, read by you offline.

**Storage (the logbook) — no managed database. Cheapest-first:**
- a **CSV committed to the repo** (durable, version-controlled; daily commit doubles as keep-alive), or
- a **Google Sheet** (append via API; the tapped cell doubles as no-code outcome capture).

**Config & secrets:** home, times, recipients as values; Telegram token as a secret. No DB.
**Data source:** Open-Meteo (no key; CC-BY attribution; no SLA; derived fields not always trustworthy — see §5).

## 12. Phasing / Roadmap

| Phase | Scope | New thing learned |
|---|---|---|
| 1 | VPD scoring function (fed manual numbers) + thin Open-Meteo fetch-and-transform (incl. VPD compute) | The domain logic *and* calling an API (clean seam) |
| 2 | Live data flowing through end-to-end, triggered by hand | Wiring real data into the scorer |
| **3 (MVP)** | **+ scheduler + Telegram verdict + logs each prediction to a CSV** | **Code that runs itself** |
| 3.5 | + evening outcome capture + manual weight calibration | Closing the learning loop |
| 4 | **Conversational Peg** — ask ad-hoc questions ("would 11am be OK?") and get a reply | Peg goes from *speaks on a schedule* to *listens all the time* (see below) |
| 5 | Multi-user: accounts, per-home, per-person logs | The logbook breaks → a real database is earned |
| 6 | Intra-day "it just turned good" alert + fitted-probability model (ET₀ as a feature) | Intra-day state + supervised learning |

**Phase 4 — Conversational Peg (notes).** Independent of multi-user; can follow the MVP whenever. Three parts:
- **Listen.** The 7am job fires and dies, so it can't receive a midday question. Add a second, event-driven entry point: a Telegram **webhook** → **serverless function** that wakes per message, replies, and sleeps (scales to zero, ~free at this volume). Same bot token; the morning push stays on GitHub Actions. (The v1 👍/👎 outcome tap is the first toe into this inbound world.)
- **Understand.** Turn "would 11am be OK?" into parameters (`{hang: 11:00, date}`). Keyword parsing is zero-dependency but brittle; an **LLM** handles natural phrasing ("after the school run") cleanly — the same fuzzy-vs-factual split as elsewhere.
- **Answer — parse, don't judge.** The verdict must come from the **same pure scoring function**, reused verbatim. The LLM only translates the question to parameters and optionally phrases Peg's reply; it must **never** score the weather itself (or it'll hallucinate a verdict). Flow: `inbound → parse to params → scorer(params) → Peg phrases the computed result → reply.`
- **Cost of the step-up:** changes the hosting model (adds an always-reachable listener) and likely adds an LLM dependency. The 7am push delivers the core value without any of it — so this is "Peg grows up," not MVP.

## 13. Edge Cases

| Case | Handling |
|---|---|
| Fog / mist (RH≈100% → VPD≈0) | Low score, never "good" — falls out of VPD |
| Overnight dew | Bring-in capped at dusk |
| Gale (`wind_gusts_10m > 32 mph`) | Practicality flag, **independent of the score** |
| Winter: cold + dry + sunny, short daylight | Scores via VPD + solar; short window may give `will_dry=false` while score/band stay coherent |
| Late-rain (final ~2h gated) | "Risky bring-in" override; band capped at Marginal |
| Missing/null field for an hour | Hour excluded as unscorable; >25% unscorable → skip the day |
| **External field silently wrong (e.g. VPD=0 despite humidity)** | **Compute VPD from temp+RH; sanity-check derived fields; never accept a plausible-but-impossible value** |
| Config error: bring_in before hang_time, or hang after sunset | Graceful validation / "no daylight window"; no crash |
| DST switch day | `timezone` handles window + scheduler hour |
| Rain only early, dry after | Best-window logic finds the dry run |
| Band boundary | Deterministic — evaluated on the raw score |
| Open-Meteo down / shape change | Fail quiet, skip the day, failure ping |
| Scheduler doesn't fire / auto-disables | Daily log commit keeps active + failure ping |

## 14. Open Decisions

- Logbook home: **CSV-in-repo vs Google Sheet**.
- Outcome capture: **Telegram buttons vs spreadsheet tap**.
- Light-fabric handling: whether to expose a light-load mode and/or add a "towels won't dry but light loads will" note when `cumulative` clears 2.5 but not 4.0.
- If/when to graduate from the index to a fitted-probability model (not before a few hundred rows).
- Exact morning send time relative to hang time.
- Peg's voice: deterministic templates (§8) for v1, vs LLM-generated lines later for variety (keeps it fresh, adds an API dependency — a flourish, not a need).

*Resolved: raw-score banding, will_dry/band coherence, risky-override, null-field handling (v1.4). VPD **computed** from temp+RH after live testing showed the API's VPD field returning 0 (v1.5) — reversing the v1.3 fetch-direct call. Route remains weighted-VPD, with ET₀ fetched and held in reserve (and validated before use).*

## 15. Dependencies & Risks

| Dependency | Risk | Posture |
|---|---|---|
| Open-Meteo | No SLA; non-commercial only; **derived fields can be silently wrong (observed VPD=0)** | Attribution; fail quiet; **compute VPD from temp+RH; validate derived fields** |
| Telegram | Delivery; token is the one secret; now also inbound | Token secret; channel swappable |
| Scheduler (GitHub Actions) | Best-effort timing; silent failure; 60-day auto-disable | Schedule early/off-hour; failure ping; daily commit keeps active |
| Calibration data | Selection bias; small-n overfitting; index mistaken for probability | Test towels; eyeball don't model until n large; never label the index a % |
