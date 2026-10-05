# Peg

Peg tells you each day whether it is a good day to dry washing outside, then asks whether it actually dried, so its record can be checked.

## Language

**Prediction Log**:
The record of every Day Peg has forecast, with the Outcome for each.
_Avoid_: log, history, CSV

**Day**:
One forecast date in the Prediction Log: Peg's Band for that date and the Outcome, if there is one.
_Avoid_: row, entry, record

**Band**:
Peg's verdict for a Day: Crack open the pegs, Good drying day, Marginal, or Tumble-dryer weather.
_Avoid_: rating, category, level

**Outcome**:
The user's answer about a Day: dry, damp, or didn't hang.
_Avoid_: result, feedback, response

**Answerable Day**:
A Day on which Peg asks for an Outcome. Every Band except Tumble-dryer weather.
_Avoid_: prompted day

**Accuracy**:
The number of Days on which Peg was right, out of the Days with a dry or damp Outcome. Peg is right when it predicted drying (Good or Crack) and the Outcome was dry, or predicted no drying (Marginal or Tumble) and the Outcome was damp.
_Avoid_: score, hit rate
