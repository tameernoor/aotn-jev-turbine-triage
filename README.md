# jev-turbine-triage

A small companion example for the aotn series. It reads real wind turbine event logs
from the Kelmarsh wind farm, asks Jev a few narrow questions about each kind of event,
and lets plain code decide what to do about the stream: act now, monitor, or no action.
It also scores Jev honestly against the wind farm operator's own fault category for
each event, a label this project did not write and did not tune against after seeing
Jev's answers.

## What it shows

Six Senvion MM92 turbines logged 14,019 events in 2016: alarms, stops, warnings and
informational messages. Most of that is noise: 12,549 of the events are informational
and never reach Jev. The other 1,470 events are where triage matters.

For each of those, Jev is asked three narrow questions, once per distinct
`(status, message)` pair rather than once per event, since the same alarm text repeats
thousands of times. Plain code then combines Jev's answers with a few checks that need
no model at all (an alarm that repeats fast is chattering, a pile of alarms across the
farm at once is a flood, a stop that drags on is a long stop) to sort every event into
one of three classes:

- **act_now**: a safety-related event, or a fault that needs a technician on site.
- **monitor**: a fault that might clear itself remotely, a warning while the turbine
  is still running, a long stop, or an uncertain read the rules refuse to guess past.
- **no_action**: informational noise, or a planned or external event with nothing to
  do about it.

Once triage is done, the operator's own IEC 61400-26 category (present on 959 of the
1,470 non-informational events) is used to check how often Jev's read of the cause
agrees with what the operator actually recorded.

## Data and licence

Source: Kelmarsh wind farm SCADA status data, 2016, from Cubico Sustainable
Investments, via Zenodo record
[16807551](https://doi.org/10.5281/zenodo.16807551), licensed
[CC-BY-4.0](https://creativecommons.org/licenses/by/4.0/).

The full 2016 archive is about 98 MB and is not committed here (`data/raw/` is
git-ignored). A small hand-picked sample, `data/sample/`, is committed instead, with
its own attribution in `data/sample/README.md`, so the CLI can be run without the
download.

## How to fetch and run

Copy `.env.example` to `.env` and set `TYPESAFE_API_KEY`:

```
cp .env.example .env
```

Fetch the real 2016 data into `data/raw/` (downloads from Zenodo, or copies from a
local mirror if `--from DIR` or `KELMARSH_LOCAL_DIR` points at one). This also builds
`data/raw/kelmarsh.duckdb`, the 10-minute measurements step 2 reads its context from:

```
uv run python scripts/fetch_kelmarsh.py
```

Run the full pipeline against it. This needs `TYPESAFE_API_KEY` in the environment
(`.env`, loaded by `--env-file`):

```
uv run --env-file .env python -m jev_turbine run
```

Run against the committed sample instead, without fetching anything (this also uses
the sample's own small `data/sample/Turbine_Data_*.csv` slices for step 2, in place
of `data/raw/kelmarsh.duckdb`):

```
uv run --env-file .env python -m jev_turbine run --sample
```

`run` does step 1, then step 2 (see "Step 2: context" below), by default. `--no-context`
skips step 2 entirely, building no context and opening no DuckDB at all, not even to
check that `data/raw/kelmarsh.duckdb` exists.

```
uv run --env-file .env python -m jev_turbine run --no-context
```

`--data DIR` points at a different folder of Status CSVs, and `--out DIR` changes
where output is written (default `out/`). Each run writes:

- `out/judgments.json`: step 1's answers, cached and keyed by status and then message,
  so it is one entry per distinct `(status, message)` pair. A pair already in this
  file is never asked again, so a second run against the same data only pays for
  whatever is new. The file also carries a hash of `questions/event.yaml`; if that
  hash does not match the questions this run is using, the cache is not trusted, is
  ignored instead of silently serving stale answers, and the run says so, on stdout
  and in `out/summary.json`.
- `out/judgments-context.json`: step 2's answers, the same idea but keyed by the exact
  `status`/`message`/`context` state sent to Jev, since the context is specific to one
  event rather than shared across a whole distinct pair. Carries a hash of
  `questions/event_with_context.yaml`, checked and reported the same way. Not written
  at all when `--no-context` is given.
- `out/triage.jsonl`: one JSON line per event, in event order, with the turbine,
  start and end timestamps, duration, status, message, the final triage class and the
  reasons behind it, and whether it was flagged as chattering or part of a flood, plus
  `step1_triage`, `step1_reasons`, `context` and `step2_judgments`, all `null` for an
  event that step 1 was not uncertain about and so was never escalated.
- `out/evaluation.json`: the accuracy report described below, now also comparing step
  1 alone against step 1 plus step 2.
- `out/summary.json`: the same summary printed at the end, as JSON: triage counts,
  the top act_now messages, evaluation accuracy, and how many Jev calls step 1 made,
  at what token count, cost and wall time, plus the model ids seen, and the same five
  numbers again for step 2, kept separate.

Jev itself is only ever built the first time a question is actually asked, in either
step, so a run whose caches already cover everything needs no `TYPESAFE_API_KEY` and
makes no call at all. One client is shared between step 1 and step 2 and closed once,
after both are done. `--cache FILE` and `--context-cache FILE` seed the run from
committed answers (each seeds the run from that file instead of the matching `out/`
file, without ever writing back to it; the merged result still lands in
`out/judgments.json` / `out/judgments-context.json` as usual), but a `FILE` that does
not exist is not an error: it is treated as an empty starting cache, so a `--cache`
without a matching `--context-cache` (or `--no-context`) still runs step 2 for real,
against Jev, paying for every escalated call.

To reproduce `## Measured` (step 1 only) without calling Jev:

```
uv run --env-file .env python -m jev_turbine run \
  --cache results/judgments-2016.json --no-context
```

With `results/judgments-context-2016.json` (see `## Measured: step 2`), this
reproduces both steps without calling Jev at all:

```
uv run --env-file .env python -m jev_turbine run \
  --cache results/judgments-2016.json \
  --context-cache results/judgments-context-2016.json
```

## Questions

Jev sees only `status` and `message` for each distinct pair, never the operator's own
category, the service contract label, or the vendor code.

| id | type | asks |
| --- | --- | --- |
| `cause` | choice: fault / planned / external / running | What most likely caused this event? |
| `safety_related` | yes/no | Does this event indicate a risk to people or to the turbine's structure? |
| `needs_site_visit` | yes/no | Would a technician most likely have to go to the turbine to fix this? |

## Triage rules

Safety comes first. In order:

1. An informational event that is not chattering needs no action, and Jev is not
   asked about it at all.
2. Anything Jev reads as safety-related, confidently, is act_now, whatever else is
   true about it.
3. A confident fault that would need a site visit is also act_now, even if some other
   read on the same event was uncertain.
4. Past that, if any answer the rules actually looked at was uncertain, the event
   goes to monitor with an "uncertain" reason. It is never silently dropped to
   no_action.
5. A fault that does not need a site visit goes to monitor, since a remote reset
   might clear it.
6. A warning while the turbine is still running goes to monitor.
7. Everything else, planned or external activity, or running without a warning, is
   no_action.

Three checks run over the whole stream with no model involved, and layer on top of
that decision: a message repeating three or more times within ten minutes on one
turbine is chattering (an informational event that chatters is bumped to monitor;
anything else just gets the reason added); more than ten non-informational events
starting within ten minutes anywhere on the farm is flagged as a flood; and a stop
lasting more than 24 hours is a long stop, which floors the event at monitor and adds
its own reason.

## Step 2: context

Step 1 leaves some events at monitor because an answer was genuinely uncertain, not
because of a code check like chattering or a long stop. For exactly those events,
step 2 asks Jev the same three questions again, now with a paragraph of plain-English
context added, computed by code from the turbine's own 10-minute measurements and its
event history, rather than left for Jev to guess at from three or four words of alarm
text.

The measurement numbers in that paragraph, mean power and wind before the event, mean
power and rotor speed after it, how long power stayed below 50 kW, and grid frequency
and voltage around the event, come from one SQL query, `src/jev_turbine/sql/context.sql`,
run once over the whole batch of events being escalated rather than once per event. A
reader can check the arithmetic directly there instead of trusting a description of
it. The few counts alongside them, how many times the same message started on this
turbine in the previous 7 days, what non-informational event happened just before it,
whether another turbine also stopped in the same 10 minutes, come from plain Python
instead (`_history_lines` in `context.py`), reading the in-memory event list rather
than the database. Everything is rounded before it reaches Jev, power to 10 kW, wind
to 0.1 m/s, frequency to 0.01 Hz, voltage to 1 V, durations to the nearest 10 minutes.
Missing data is said, not guessed, for example "No 10-minute data around this event."

A real example, Kelmarsh 1, a Stop with the message "Frequency converter error",
2016-01-24 16:51:17:

```
Before the event: power 600 kW, wind 7.7 m/s (60-minute averages).
After the event: power 0 kW; power stayed below 50 kW for at least 17 h 30 min, then no power data.
Grid in the hour around the event: frequency 49.93 to 50.02 Hz, voltage 690 to 697 V.
Same message on this turbine in the previous 7 days: 0 times.
Other turbines stopped in the same 10 minutes: no.
```

`questions/event_with_context.yaml` asks the same three questions as step 1, word for
word, with `status`, `message` and now this `context` text as the basis for each
answer. Every escalated event's final triage is step 2's, whatever that turns out to
be, not only when it is confident or different from step 1's; step 1's own triage and
reasons are kept alongside it (`step1_triage`, `step1_reasons` in `out/triage.jsonl`)
rather than discarded. If step 2 is itself uncertain, the event stays at monitor with
step 2's own uncertain reason instead of step 1's. Two escalated events whose
`status`, `message` and rendered `context` are all identical share one answer instead
of asking Jev twice.

Only five 10-minute columns ever reach the context builder, `Power (kW)`, `Wind speed
(m/s)`, `Rotor speed (RPM)`, `Grid frequency (Hz)` and `Grid voltage (V)`, physical
measurements a turbine's own sensors record regardless of who is judging the event.
Every other column in the source data, anything about lost production, availability,
curtailment, contractual energy budgets or capacity, and the event log's own `Service
contract category`, `IEC category` and `Code` fields, is excluded structurally. The
loader (`measurements.py`) only has room for the five allowed columns in the table it
builds, so nothing else can reach a Jev prompt through it. Those excluded columns are
exactly the operator's own classification, the same answer key the evaluation below
scores Jev against, so letting any of them into the context would leak the answer into
the question.

## Why the evaluation is honest

The answer key is not ours. It is the operator's own IEC 61400-26 category, recorded
by the wind farm operator independently of this project. Jev never sees it: only
`status` and `message` go into the questions.

The question wording in `questions/event.yaml` was frozen before the first run
against real data, so there was no chance to adjust the wording after seeing how Jev
did.

The wording was drafted with the operator's categories in view, and one phrase was
placed to match them: a stop requested by the park controller counts as planned,
because the operator books "Park master stop" as Requested Shutdown.

The mapping from that IEC category to the `cause` bucket the questions actually ask
about is ours, not the operator's, and is stated plainly in `evaluate.py` and in every
`out/evaluation.json` this project writes:

| IEC category | cause |
| --- | --- |
| Forced outage | fault |
| Scheduled Maintenance | planned |
| Technical Standby | planned |
| Requested Shutdown | planned |
| Out of Electrical Specification | external |
| Out of Environmental Specification | external |
| Full Performance | running |
| Partial Performance | running |

Accuracy is reported two ways: per event, which weighs frequent alarms more heavily,
and per distinct message, which does not. A confusion matrix and the specific
messages where Jev disagreed with the operator, with Jev's own probabilities for that
call, are both in the output, alongside a count of how many distinct messages Jev
itself flagged as uncertain.

Step 2 is scored against the same answer key, with the same mapping. The mapping from
IEC category to `cause` above is ours, not the operator's; the category itself stays
the operator's own label whether an event was escalated or not. `out/evaluation.json`
reports cause accuracy twice, once for step 1 alone and once for step 1 with step 2's
cause substituted in wherever an event was escalated, both against the same operator
category, over the same events. Among escalated events that carry a category, it also
counts how many changed cause at all, how many moved towards the operator's answer,
how many moved away from it, and how many were still an uncertain monitor after the
second question. `questions/event_with_context.yaml` was committed before the first
run against real data and not changed after, the same rule step 1's wording follows.

## Limits

The IEC category and the `cause` bucket are not a clean one-to-one mapping. A
"Forced outage" is not always a defect Jev could see coming from the message text
alone, and "Technical Standby" and "Requested Shutdown" both land on the same
`planned` bucket despite being different things operationally. Treat the mapping as a
reasonable approximation, not a certified equivalence.

The operator's own data has a quirk worth knowing about: "Manual stop - remote" is
recorded as a forced outage in the source data, even though it was requested, not a
failure. That is the operator's labelling choice, not a bug in this project's mapping,
and it is left as is rather than second-guessed.

## Measured

One run against `jev-1.13.0` over all of 2016 for the six turbines, with an empty
cache. The question wording was committed before any run and not changed after it.
The answers from this run are in `results/judgments-2016.json` and the usage in
`results/summary-2016.json`, so the numbers below can be reproduced without calling
Jev, with `--cache results/judgments-2016.json --no-context` (see "How to fetch and
run" above; step 2 runs by default, so `--no-context` is what keeps this call-free).

### Speed and cost

- 14,019 events, 98 distinct kinds of non-informational event, so 98 Jev requests
  (one per kind, all three questions in each). Informational events never go to Jev.
- 29 seconds for the whole year, with the requests sent one after another. 57,867
  input tokens, $0.0024 in total.

### Cause, against the operator's own category

959 non-informational events carry the operator's IEC 61400-26 category. Mapped to
`cause` with the table above:

- Jev agreed with the operator on 717 of 959 events (75 %), and on 48 of 61 distinct
  messages (79 %). An earlier run with the same wording gave 709 of 959: answers
  near a boundary move a little between runs.
- `planned` 508 of 508 and `external` 48 of 48 agreed. The disagreements are all on
  `fault` and `running`.
- Where they disagree, it is often the question rather than the reading. "Maximum
  grid frequency" is external by our wording and a forced outage in the operator's
  books. "Manual stop - remote" reads as planned and is booked as a forced outage.
  Warnings such as "Vane 2 defect" or "Error brake resistor CHP" describe a defect,
  so Jev says fault, while the operator books them as full performance because the
  turbine kept producing. `cause` mixes why something happened with whether the
  turbine is running, and these rows show it.
- Jev was genuinely split on a few: "WEC shut down" 0.48 planned and 0.38 fault,
  "No speed development" 0.49 external and 0.42 fault. Its `cause` read was below
  0.6 confidence on 12 of the 61 scored messages (23 of all 98 asked).

### Triage

- 69 events went to act now (the top ones: "Oscillation encoder tower", "Safety
  chain open", "High rotor speed nacelle", "Tower oscillation X level 2", "Emergency
  stop base box"), 1,341 to monitor, 12,609 to no action.
- Most of the monitor pile is uncertainty, not faults: 1,200 of the 1,341. For
  "Battery test" Jev put `safety_related` at 0.25, just above the 0.2 that counts
  as no, which alone sends 258 routine tests to monitor. `needs_site_visit` sat
  between 0.3 and 0.6 for 62 of the 98 messages: from three or four words of vendor
  text, whether someone has to drive out is often not knowable, and Jev says so.
- 124 informational events went to monitor for chattering alone.

What this shows: a narrow question works when the text carries the answer (planned,
external, safety chains, emergency stops). When it does not, the model hovers in the
middle, and the code's thresholds turn that into "look at it". Tightening the
thresholds or the wording would change these numbers; we did not, so they stay
honest.

## Measured: step 2

One run against `jev-1.13.0` over all of 2016 for the six turbines, with an empty
step-2 cache, on top of the step-1 answers above (read from
`results/judgments-2016.json`, so step 1 is exactly the run described in "Measured").
`questions/event_with_context.yaml` was committed before the run and not changed after
it. The step-2 answers are in `results/judgments-context-2016.json` and the run's
summary in `results/summary-context-2016.json`, so the numbers below reproduce without
calling Jev.

### Speed and cost

- Step 1 left 1,200 events uncertain. Their status, message and context came to 1,167
  distinct states, so step 2 made 1,167 Jev requests, one per state, 20 at a time.
- Step 2 took 21 seconds, 866,473 input tokens and $0.036. That is about 740 tokens per
  request, against about 590 for a step-1 request: the context adds only a few lines.

### Cause, with context, against the operator's own category

- Over the same 959 events with an IEC category, `cause` agreed with the operator for
  717 (75 %) with step 1 alone and 808 (84 %) with step 2 added.
- 703 of the escalated events carry an IEC category. Step 2 changed the cause for 148
  of them: 118 from wrong to right, 27 from right to wrong, 3 from one wrong cause to
  another. 449 of the 703 were still uncertain after step 2.
- The gain is narrow. 117 of the 118 corrections are the three "Overload generator fan"
  warnings, which step 1 called `running` and the operator files as forced outages;
  with the context in front of it Jev called them `fault`.
- The biggest loss is "Cable autounwind" (18 events). Step 1 called it `planned`, which
  matches the operator; with the context, which shows the turbine stopped and produced
  nothing for a while, Jev called it `fault`.
- Some disagreements do not move at all. "Comm. failure FPM" (65 events) is `fault`
  both times, while the operator files it as full performance: the turbine kept
  producing, and a lost communication link is not something a power curve shows.

### Triage, before and after step 2

- act_now went from 69 to 201, monitor from 1,341 to 955, no action from 12,609 to
  12,863.
- Of the 1,200 escalated events, 254 went to no action, 132 to act_now and 814 stayed
  in monitor.
- The 132 new act_now events are all "fault needing a site visit": "Brake accumulator
  defect" (95), "Breakdown obstacle light" (16), "Brake pads worn" (7) and a handful of
  others. 119 of the 132 have no IEC category in the operator's log, so this data
  cannot say whether they deserved it. "Brake pads worn" plausibly does; 95 brake
  accumulator warnings is a lot of site visits.

What this shows: a second, narrower look with a few computed numbers is cheap (a few
cents for a year of a wind farm) and it does change answers, mostly in the right
direction on the events the operator labelled. It does not settle what the text and
a power curve cannot show, and two thirds of the escalated events stayed uncertain.
Those are the ones where a person, or a third step with other data such as the
turbine's own fault codes or maintenance log, would have to decide. As with step 1,
the question wording was not tuned against these results.
