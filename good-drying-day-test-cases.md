# Good Drying Day — Test Cases & QA Notes

**Tested against:** PRD v1.3 · **Last updated:** 30 May 2026 · **Author:** QA

---

## 0. Testing strategy (read first)

Two things shape how this app must be tested:

- **The oracle problem.** There is no ground-truth "correct score" for a day — the only real oracle is whether the washing actually dried, which arrives later via the calibration log. So unit tests must assert **behaviour and invariants** (cold-dry beats warm-humid; rain forces 0; bounds hold), **not magic numbers** like "this day = 73". Absolute-value assertions belong only to a small, deliberately-maintained fixture set.
- **The weights are meant to change.** The §7 constants are calibration knobs. If tests pin exact expected scores, every calibration will produce false failures. So split tests into:
  - **Invariant tests** (§3) — never change, regardless of calibration.
  - **Baseline fixtures** (§2) — a handful of canonical days with expected values that are **re-baselined on purpose** when weights are tuned, and reviewed as part of that change.

Most test value sits in the **pure scoring function**: deterministic, fast, hand-checkable, no I/O. Integration and end-to-end tests are thinner; judgement calls go to exploratory.

---

## 1. Spec gaps found during inspection (fix before/while building)

| # | Gap | Why it matters | Recommendation |
|---|---|---|---|
| G1 | **Round-then-band ordering is unspecified.** §8 rounds the score to the nearest 5 *and* defines integer band ranges (0–34, 35–54…). A raw 33 rounds to 35 — Tumble-dryer if banded on the raw score, Marginal if banded on the rounded one. | Different verdicts at boundaries. | **Band on the raw score; round only for display.** Add to PRD §8. |
| G2 | **Band ranges don't align to the rounding grid.** Boundaries 34/54/79 aren't multiples of 5, so a displayed score can never land *on* them. | Cosmetic confusion + reinforces G1. | Re-express bands on raw score, or move boundaries to multiples of 5. |
| G3 | **`will_dry` vs band coherence.** A short winter window of 3 perfect hours → cumulative 3.0 → score ~40 (Marginal) but `will_dry = false` for towels (yet *would* dry the 2.5 light target). | Band text may imply it'll dry when the towel model says it won't. | Decide whether the headline verdict speaks for towels or light items, and make band copy consistent with `will_dry`. |
| G4 | **Late-rain "risky" cap output is undefined.** §7 says cap the verdict to "risky" if rain is in the final ~2h, but there's no "risky" band in §8. | Ambiguous output state. | Define how "risky" presents (e.g. overrides band label, keeps score for the log). |
| G5 | **Null/partial field handling unspecified.** What happens if one hour's `vapour_pressure_deficit` is null, or arrays are short on an early run? | Silent `null→0` would understate drying. | Define: skip hour / fail the day / etc. |

---

## 2. Unit tests — scoring function (baseline fixtures)

*Sub-score curves: `vpd=clamp(VPD/1.0)`, `wind=clamp(0.25+mph/16)`, `solar=clamp(rad/450)`; `hourly=0.5·vpd+0.3·wind+0.2·solar` (×0 if rain-gated); `score=clamp(50·Σhourly/4.0,0,100)`.*

| ID | Scenario | Key input | Expected |
|---|---|---|---|
| SCORE-01 | Perfect hour | vpd≥1.0, wind≥12mph, solar≥450 | hourly_potential = 1.0 |
| SCORE-02 | Still-air floor | wind = 0 mph | wind sub-score = 0.25 (not 0) |
| SCORE-03 | Wind saturation | wind = 16 mph | wind sub-score clamps to 1.0 (not 1.25) |
| SCORE-04 | VPD clamp | VPD = 1.5 kPa | vpd sub-score = 1.0 |
| SCORE-05 | Weighted mix | vpd0.6 / wind8mph / solar225 | hourly = 0.625 |
| SCORE-06 | Meets towel bar | 4 perfect hours | cumulative 4.0 → score 50 → Marginal → will_dry true |
| SCORE-07 | 2× margin | 8 perfect hours | cumulative 8.0 → score clamps to 100 → Crack open pegs |
| SCORE-08 | will_dry boundary | cumulative 3.99 vs 4.00 | false vs true |
| SCORE-09 | Rain gate (hour) | hour with precip_prob 60% | that hour's potential = 0 regardless of other fields |
| SCORE-10 | Rain gate boundary | precip_prob = 50% / 51%; precip = 0.2 / 0.21 mm | 50% & 0.2 not gated; 51% & 0.21 gated (`>` rule) |
| SCORE-11 | All-day rain | every hour gated | cumulative 0 → score 0 → Tumble-dryer |
| SCORE-12 | Rain beats everything | high VPD + wind but precip_prob 70% | hour potential 0 (rain dominates) |
| SCORE-13 | Rounding | raw 72.3 / 73.0 | displays 70 / 75 |
| SCORE-14 | Band edges (raw) | raw 34 / 35 / 54 / 55 / 79 / 80 | Tumble / Marginal / Marginal / Good / Good / Crack open pegs |
| SCORE-15 | Late-rain cap | score 85 but rain in final 2h of window | verdict capped to "risky", not "Crack open pegs" (see G4) |
| SCORE-16 | Best-window selection | 4 perfect morning hours, poor afternoon | recommended window = the morning run (earliest contiguous reaching target) |
| SCORE-17 | Hang time flips verdict | identical forecast; hang 08:00 vs 13:00 (rain at 16:00) | different verdict/band |

## 3. Invariant / property tests (never re-baselined)

| ID | Invariant |
|---|---|
| INV-01 | Score is always within 0–100. |
| INV-02 | Displayed score is always a multiple of 5. |
| INV-03 | Adding rain to any hour never *increases* the score. |
| INV-04 | Increasing wind from 0→12 mph never *decreases* the score. |
| INV-05 | Decreasing VPD never *increases* the score. |
| INV-06 | Recommended window always lies within `[hang_time, min(bring_in, dusk)]`. |
| INV-07 | If any hour in the final 2h is rain-gated, the verdict is never "good"/"Crack open pegs". |
| INV-08 | `will_dry` is true **iff** cumulative ≥ DRY_TARGET. |
| INV-09 | **Headline principle:** a cold-dry-windy day always scores ≥ a warm-humid-still day. |

## 4. Functional tests (mapped to PRD §9 ACs)

| ID | AC | Scenario / steps | Expected |
|---|---|---|---|
| FUNC-01 | Hands-free | Scheduled run with only stored config | Message arrives ~hang−1h, no user action |
| FUNC-02 | Content | Inspect morning message | Contains verdict + recommended window |
| FUNC-03 | Honest number | Inspect score presentation | Rounded to 5, shown with band, no "%"/"probability" wording |
| FUNC-04 | No crying wolf | Day with rain in window | Verdict never "good" |
| FUNC-05 | Hang time matters | Change hang time in config, same day | Verdict can change |
| FUNC-06 | Set once | First run after one-time setup | Verdict produced with no daily input |
| FUNC-07 | Outcome capture | Tap 👍/👎 on evening prompt | Reply written to that day's log row |
| FUNC-08 | Learns | Open the log | Inputs + prediction + outcome retrievable together in one row |

## 5. Integration / technical tests

| ID | Scenario | Expected |
|---|---|---|
| INT-01 | Fetch-transform | Parallel hourly arrays → per-hour objects aligned by index; sliced correctly to the window |
| INT-02 | **Units (mph)** | Wind interpreted as mph (`wind_speed_unit=mph`); regression guard so km/h can't sneak in |
| INT-03 | Timezone | Window hours & sunset computed for Europe/London; correct on a BST day |
| INT-04 | API non-200 / timeout | Fail quiet, skip the day, **no bogus verdict sent**, failure ping fired |
| INT-05 | Malformed / empty JSON | Handled without crashing |
| INT-06 | Missing field (null VPD for an hour) | Defined handling (not silent null→0) — see G5 |
| INT-07 | Telegram token missing/invalid | Fail quiet + log; no crash |
| INT-08 | Scheduler | Fires from default branch; `workflow_dispatch` manual run works |
| INT-09 | **Idempotency** | Manual + scheduled run same day → no double-notify, no duplicate log row |
| INT-10 | Log append | Exactly one row/day; correct columns incl. `et0`; appends, never overwrites |
| INT-11 | Outcome matching | Reply maps to correct date; a *delayed* reply (next morning) lands on the defined target row |
| INT-12 | Log schema evolution | Adding a column later doesn't corrupt or misalign existing rows |

## 6. Edge cases

| ID | Scenario | Expected |
|---|---|---|
| EDGE-01 | Fog all day (RH≈100% → VPD≈0) | Low score, never "good" — falls out of VPD, no special-casing needed |
| EDGE-02 | Gale (gusts >32 mph) but great drying otherwise | High score **with** gust flag set — flag is independent of the score |
| EDGE-03 | Winter: cold + dry + sunny, short daylight | Scores via VPD + solar; check `will_dry` against the short window (see G3) |
| EDGE-04 | Config error: bring_in before hang_time | Graceful validation, no crash |
| EDGE-05 | Hang_time after sunset → empty window | Defined output (e.g. "no daylight window"), not a divide-by-zero |
| EDGE-06 | DST switch day (clocks change) | Window + scheduler hour correct; no missing/duplicated hour |
| EDGE-07 | Rain only in first hour, dry after | Best-window logic still finds the dry run (early gate shouldn't tank a later hang) |
| EDGE-08 | Very early run — today's arrays partial | Handles short arrays without index errors |
| EDGE-09 | Implausible API value (negative radiation at night, VPD spike) | Clamps/handles sanely; night hours excluded from window |
| EDGE-10 | Raw score exactly on a band boundary | Deterministic band assignment (depends on G1 resolution) |

## 7. Exploratory testing charters (session-based, ~30–60 min each)

Time-boxed missions — not scripted pass/fail. Note anything surprising.

- **EXP-01 · Adversarial weather.** Hunt for input combinations that produce a verdict contradicting common sense or the cold-dry-beats-warm-humid principle. *Watch for:* dimensions that should matter but don't move the score, and vice versa.
- **EXP-02 · Boundaries & rounding.** Probe the band edges and the round-vs-band ordering (G1/G2). *Watch for:* a displayed score whose band feels wrong by one step.
- **EXP-03 · Time & DST.** Push hang/bring-in near sunrise/sunset, the BST↔GMT switch days, and the longest/shortest days of the year. *Watch for:* window drift, off-by-one-hour, empty or negative windows.
- **EXP-04 · Failure injection.** Drop the network mid-fetch, feed empty/malformed/short JSON, expire the Telegram token, simulate an Open-Meteo field rename. *Watch for:* any path that sends a confident-but-wrong verdict instead of failing quiet.
- **EXP-05 · Data integrity over a simulated month.** Run many days into the log. *Watch for:* missing rows, duplicate runs, outcome written to the wrong date, schema drift, the 60-day keep-alive actually keeping it alive.
- **EXP-06 · Message coherence.** Read many generated messages. *Watch for:* a reason that contradicts the verdict/score, a window outside [hang, bring_in], the "risky" late-rain state rendering oddly (G4).
- **EXP-07 · Lived-memory oracle.** Replay a handful of real days you both remember (a glorious drying day, a washout). *Watch for:* verdicts that disagree with what actually happened — the cheapest real-world oracle you have before the log fills.
- **EXP-08 · False-positive hunt (trust).** Specifically try to find a "Good"+ verdict you personally would *not* have hung washing on. These are the trust-killers the conservative-bias principle exists to prevent — worth the most attention.

## 8. Where the bugs most likely are (QA instinct)

1. **Unit mismatch** — km/h vs mph silently shifting every wind score (INT-02).
2. **Timezone / DST** — window and scheduler hour drift (INT-03, EDGE-06, EXP-03).
3. **Round-then-band ordering** — boundary verdicts (G1, SCORE-14).
4. **Late-rain cap interaction** — a high score that should be downgraded (G4, SCORE-15).
5. **Double-run** — manual + scheduled producing duplicate notifications/log rows (INT-09).
6. **Null/partial fields** — silent understatement of drying (G5, INT-06, EDGE-08).
7. **Silent scheduler non-fire** — not unit-testable; needs the failure ping + keep-alive to be *observable*, which is itself worth a test.
