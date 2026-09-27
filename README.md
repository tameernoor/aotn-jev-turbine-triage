# jev-turbine-triage

A small companion example for the aotn series. It reads real wind turbine event logs
from the Kelmarsh wind farm, asks Jev five literal yes/no questions about each kind of
event, and lets plain code sort the stream into act now, monitor, or no action. It also
scores Jev honestly against the wind farm operator's own fault category for each event,
a label this project did not write and did not tune against after seeing Jev's answers.

## What it shows

Six Senvion MM92 turbines logged 14,019 events in 2016, a mix of alarms, stops,
warnings and informational messages. Most of that is noise: 12,549 of the events are
informational and never reach Jev. The other 1,470 events are where triage matters.

For each of those, Jev is asked five literal yes/no questions, once per distinct
`(status, message)` pair rather than once per event, since the same alarm text repeats
thousands of times. Each question asks one narrow thing about the message text; code
combines the five answers into a cause, and combines the cause with a few checks that
need no model at all (an alarm that repeats fast is chattering, a pile of alarms across
the farm at once is a flood, a stop that drags on is a long stop) to sort every event
into one of three classes:

- **act_now**: a safety-related event, or physical damage to a part.
- **monitor**: a fault that might clear itself remotely, a warning while the turbine
  is still running, a long stop, or an uncertain read the rules refuse to guess past.
- **no_action**: informational noise, or a planned or external event with nothing to
  do about it.

For the events step 1 leaves monitor because the cause is uncertain or unclear, a
second step reads the turbine's own production numbers and settles some of them in
plain code. Jev is not asked again. See "Step 2" below.

Once triage is done, the operator's own IEC 61400-26 category (present on 959 of the
1,470 non-informational events) is used to check how often the derived cause agrees
with what the operator actually recorded.

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
`data/raw/kelmarsh.duckdb`, the 10-minute measurements step 2 reads its production
numbers from:

```
uv run python scripts/fetch_kelmarsh.py
```

Run the full pipeline against it. This needs `TYPESAFE_API_KEY` in the environment
(`.env`, loaded by `--env-file`) for step 1; step 2 never calls Jev, so it needs no key
either way:

```
uv run --env-file .env python -m jev_turbine run
```

Run against the committed sample instead, without fetching anything (this also uses
the sample's own small `data/sample/Turbine_Data_*.csv` slices for step 2, in place
of `data/raw/kelmarsh.duckdb`):

```
uv run --env-file .env python -m jev_turbine run --sample
```

`run` does step 1, then step 2 (see "Step 2" below), by default. `--no-context` skips
step 2 entirely, reading no production numbers and opening no DuckDB at all, not even
to check that `data/raw/kelmarsh.duckdb` exists:

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
- `out/triage.jsonl`: one JSON line per event, in event order, with the turbine,
  start and end timestamps, duration, status, message, the derived cause, the final
  triage class and the reasons behind it, and whether it was flagged as chattering or
  part of a flood, plus `step1_triage`, `step1_reasons` and `context`, all `null` for
  an event that step 1 was not uncertain about and so was never looked at again in
  step 2.
- `out/evaluation.json`: the accuracy report described below, comparing step 1 alone
  against step 1 plus step 2.
- `out/summary.json`: the same summary printed at the end, as JSON: triage counts,
  the top act_now messages, evaluation accuracy (step 1 alone, and with step 2), and
  how many Jev calls step 1 made, at what token count, cost and wall time, plus the
  model ids seen.

Jev itself is only ever built the first time a question is actually asked in step 1,
so a run whose cache already covers every pair needs no `TYPESAFE_API_KEY` and makes
no call at all. `--cache FILE` seeds the run from a committed cache (for example
`results/judgments-2016.json`), instead of `out/judgments.json`, without ever writing
back to `FILE` itself; the merged result still lands in `out/judgments.json` as usual.
A `FILE` that does not exist is not an error: it is treated as an empty starting
cache.

To reproduce `## Measured` without calling Jev, once `data/raw/kelmarsh.duckdb`
exists (step 1 is seeded from the cache; step 2 reads production numbers from that
file, no key needed either way):

```
uv run python -m jev_turbine run --cache results/judgments-2016.json
```

Add `--no-context` to reproduce step 1 alone, without needing
`data/raw/kelmarsh.duckdb` at all.

## Questions

Jev sees only `status` and `message` for each distinct pair, never the operator's own
category, the service contract label, or the vendor code.

| id | asks |
| --- | --- |
| `names_safety_hazard` | Does `message` name an emergency stop, a safety chain, overspeed, excessive rotor speed, tower oscillation or vibration, fire or smoke? |
| `names_physical_damage` | Does `message` say that a part is worn, broken, leaking or defective? |
| `names_routine` | Does `message` describe a test, maintenance, a manual or remote stop by people, a stop requested by the owner or park controller, or a routine procedure such as cable unwinding or oil flushing? |
| `names_outside_condition` | Does `message` name the power grid, grid frequency, grid voltage, a grid loss, or the wind being too weak, too strong or from the wrong direction? |
| `names_turbine_problem` | Does `message` name an error, fault, failure, defect, timeout, overload or trip, or a reading that is too high, too low, at its limit, deviating or implausible, in a part or system of the turbine itself? |

All five are yes/no questions (`noul`), all sent to Jev in one request per distinct
`(status, message)` pair. `src/jev_turbine/triage.py` reads `names_routine`, then
`names_outside_condition`, then `names_turbine_problem`, in that order, and stops at
the first one that decides the cause (`planned`, `external` or `fault`). If all three
say no, the cause is `running` when the status is Warning, otherwise `unclear`. An
uncertain answer anywhere in that chain stops it early too, and the cause is
`unclear`. `names_safety_hazard` and `names_physical_damage` are read for every
non-informational event regardless of what the cause chain decides.

### Why these questions

An earlier version of this project asked three broader questions instead: a hidden
cause picked from four words, a yes/no on safety, and a forecast of whether a
technician would need to visit. TypeSafe's own documentation, at docs.typesafe.ai,
says why that was the wrong shape for a yes/no model.

> System One models work best when each question asks one specific, well-scoped
> thing.

> If the question you want to ask would require extended reasoning or weighs multiple
> independent factors, decompose it.

> Ask each factor as a separate question, then combine the results with logic in your
> code.

A `noul` question is meant for "the kind of judgment a highly knowledgeable person
could make in a few seconds", not a forecast and not four competing factors folded
into one word. `cause` asked for exactly that kind of hidden, multi-factor judgment;
`needs_site_visit` asked Jev to predict the future. The five questions above each ask
about one thing the message text either does or does not say, and `triage.py` does
the combining.

## Rules

Safety and physical damage come first. In order:

1. An informational event that is not chattering needs no action, and Jev is not
   asked about it at all.
2. `names_safety_hazard` read as a confident yes is act_now, whatever else is true
   about the event.
3. `names_physical_damage` read as a confident yes is also act_now.
4. Past that, if any question the rules actually read was uncertain, or the derived
   cause came out unclear, the event goes to monitor with an "uncertain" or "cause
   unclear" reason. It is never silently dropped to no_action.
5. A cause of `fault` goes to monitor, since a remote reset might clear it.
6. A cause of `running` while the status is Warning goes to monitor.
7. Everything else, a cause of `planned` or `external`, is no_action.

Three checks run over the whole stream with no model involved, and layer on top of
that decision. A message repeating three or more times within ten minutes on one
turbine is chattering (an informational event that chatters is bumped to monitor;
anything else just gets the reason added); more than ten non-informational events
starting within ten minutes anywhere on the farm is flagged as a flood; and a stop
lasting more than 24 hours is a long stop, which floors the event at monitor and adds
its own reason.

## Step 2: production numbers as code rules

Step 1 leaves some events at monitor because the derived cause came out unclear, not
because of a code check like chattering or a long stop. For exactly those events
(other than ones where the uncertainty was in `names_safety_hazard` or
`names_physical_damage`, which always stay monitor untouched), step 2 looks at the
turbine's own production numbers and decides two things with plain code, no Jev call:

- **Kept producing**: the mean power in the hour after the event is known, at least
  50 kW, and it never dropped below 50 kW afterwards. The cause becomes `running`,
  the triage becomes no_action, and the reason is "kept producing".
- **Stopped**: the mean power after the event is known and below 50 kW. The triage
  stays monitor, with the reason "stopped, cause unclear" in place of the earlier
  uncertainty reason. The cause stays `unclear`.
- Anything else, including an event with no power data at all, is left exactly as
  step 1 had it.

Jev is not asked a second time, because these numbers are not a judgment call. Whether
a turbine kept producing after an event is a fact the SCADA log already records, not
something that needs weighing or wording. Re-asking Jev the same three questions with
a paragraph of context added, as an earlier version of this project did, spent money
asking a model to read a number a `>=` in Python reads for free.

The numbers themselves come from one DuckDB query
(`src/jev_turbine/context.py:measurement_stats`), run once over the whole batch of
events step 2 looks at rather than once per event, and the same query also renders
the plain-text context written to `out/triage.jsonl` (`step1_triage`, `step1_reasons`
and `context`) for every event step 2 looked at, whether or not the rules above ended
up changing anything, so a person reading the monitor pile can see the numbers behind
the decision. A real example, Kelmarsh 2, a Warning with the message "Comm. failure
FPM", 2016-02-09 13:43:36. Step 1 could not tell whether the message names a turbine
problem (`names_turbine_problem` came back uncertain), so the cause was unclear:

```
Before the event: power 1,440 kW, wind 9.8 m/s (60-minute averages).
After the event: power 690 kW; power did not drop below 50 kW.
Grid in the hour around the event: frequency 49.97 to 50.06 Hz, voltage 688 to 716 V.
Same message on this turbine in the previous 7 days: 0 times.
Other turbines stopped in the same 10 minutes: no.
```

The turbine kept producing, so step 2 sets the cause to `running` and the triage to
no action. The operator files this message as full performance, so here the rule
agrees with the answer key.

Only five 10-minute columns ever reach the context builder, `Power (kW)`, `Wind speed
(m/s)`, `Rotor speed (RPM)`, `Grid frequency (Hz)` and `Grid voltage (V)`, physical
measurements a turbine's own sensors record regardless of who is judging the event.
Every other column in the source data, anything about lost production, availability,
curtailment, contractual energy budgets or capacity, and the event log's own `Service
contract category`, `IEC category` and `Code` fields, is excluded structurally. The
loader (`measurements.py`) only has room for the five allowed columns in the table it
builds, so nothing else can reach step 2 through it. Those excluded columns are
exactly the operator's own classification, the same answer key the evaluation below
scores against, so letting any of them in would leak the answer into the rule.

## Why the evaluation is honest

The answer key is not ours. It is the operator's own IEC 61400-26 category, recorded
by the wind farm operator independently of this project. Jev never sees it: only
`status` and `message` go into the questions.

The question wording in `questions/event.yaml` was frozen before the first run
against real data (one wording change made from the question wording alone, before any
run: see "Previous version" below), so there was no chance to
adjust the wording after seeing how Jev did.

The mapping from the operator's IEC category to the `cause` bucket the questions
actually derive is ours, not the operator's, and is stated plainly in `evaluate.py`
and in every `out/evaluation.json` this project writes:

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

Accuracy is reported per event, which weighs frequent alarms more heavily, and per
distinct message, which does not. A confusion matrix and the specific
messages where the derived cause disagreed with the operator, with the ids (if any)
that were themselves uncertain, are both in the output, alongside a count of how many
distinct messages derived to `unclear`.

Step 2 is scored against the same answer key, with the same mapping. `out/evaluation.json`
reports cause accuracy twice, once for step 1 alone and once for step 1 with step 2's
own cause substituted in wherever an event was looked at again, both against the same
operator category, over the same events. It also counts how many events step 2
decided were "kept producing", how many of those carry an IEC category at all, and
how many of that subset agree with the operator (their category maps to `running`,
the only cause "kept producing" ever assigns).

## Limits

The IEC category and the `cause` bucket are not a clean one-to-one mapping. A
"Forced outage" is not always a defect the message text alone names, and "Technical
Standby" and "Requested Shutdown" both land on the same `planned` bucket despite being
different things operationally. Treat the mapping as a reasonable approximation, not a
certified equivalence.

The operator's own data has a quirk worth knowing about: "Manual stop - remote" is
recorded as a forced outage in the source data, even though it was requested, not a
failure. That is the operator's labelling choice, not a bug in this project's mapping,
and it is left as is rather than second-guessed.

"Kept producing" and "stopped" both read `after_power`, the mean power in the hour
after the event. A turbine that stopped and restarted within that hour, or one with
no 10-minute data at all, gets neither rule and stays exactly as step 1 left it: the
50 kW threshold is a plain reading of one number, not a model of what happened.

## Measured

One run against `jev-1.13.0` over all of 2016 for the six turbines, with the questions
frozen before it. The answers are in `results/judgments-2016.json` and the run's
summary in `results/summary-2016.json`, so the numbers reproduce without calling Jev.

### Speed and cost

98 Jev requests, one per distinct (status, message) pair, five questions each: 79,721
input tokens, $0.0033 and 28 seconds for the whole year. Step 2 makes no Jev call.

### Cause against the operator's own category

Over the 959 events with an IEC category:

| | Agrees with the operator | Cause unclear | Confidently wrong |
|---|---|---|---|
| Five literal questions (step 1) | 663 (69 %) | 253 | 43 |
| Plus the production rules (step 2) | 697 (73 %) | 211 | 51 |
| Previous version, three broad questions | 717 (75 %) | 120 unsure | 122 |

"Confidently wrong" means a cause the code acted on that does not match the
operator's category. In the previous version, "unsure" means a `cause` answer below
0.6 confidence.

- The literal questions are wrong with confidence far less often (43 events against
  122) and say "unclear" far more often (253 against 120). Headline agreement drops
  from 75 % to 69 %.
- Where a message uses the words a question looks for, the answer is clear. The three
  "Overload generator fan" warnings (117 events), which the previous version called
  `running`, now come out as `fault`, matching the operator.
- Where it does not, Jev hesitates, and it hesitates on exactly the question a reader
  would. "Cable autounwind" (68 events) came back near 0.5 on both "is this a routine
  procedure?" and "does this name a turbine problem?". "Frequency converter not
  ready", "Safety chain open" and the tower oscillation messages came back uncertain on
  "does this name an error, fault or failure?", because the words are not there.
- One uncertain answer on the cause questions makes the cause unclear. That is a rule
  in code, and it is strict on purpose: it sends the event to a person instead of
  guessing. A looser rule would score higher and guess more.
- By distinct message, 34 of 61 agree (56 %), and the cause is unclear for 22 of them.

### Step 2

Step 2 looked at 494 events whose cause was unclear. 109 kept producing and went to
no action with cause `running`; 42 of those carry an IEC category and 34 of them agree
with the operator. 236 had stopped and stayed at monitor as "stopped, cause unclear",
with the numbers written beside them. The other 149 were left as they were: 82 had no
power data after the event, and 67 produced in the hour after but had dipped below
50 kW first, which neither rule covers.

### Triage

act_now 181, monitor 817, no action 13,021. Before step 2, monitor was 926 and no
action 12,912. "Brake accumulator defect" (96 events) is most of act_now, because
`names_physical_damage` answers yes to "defect". The operator left most of those
without a category, so this data cannot say whether they deserved it.

What this shows: splitting one broad question into literal ones, as TypeSafe
recommends, did not raise the score. It changed the kind of mistake. The model stops
guessing where the text does not say, and the code sends those events to a person.
For triage, where a confident "no action" on a real fault is the expensive error,
that is arguably the better trade. The cost is a bigger pile for people to look at.

## Previous version

An earlier version of this project asked three broader questions instead of these
five (`cause`, a hidden four-way choice; `safety_related`; `needs_site_visit`, a
forecast). Against the same 959 events with an IEC category, that version agreed with
the operator on 717 of 959 (75%), and on 808 of 959 (84%) once Jev was re-asked a
second time with a paragraph of SCADA context. That work is at commit `d082b30`.

We have already seen which messages scored wrong under those broader questions. The
five questions above were written from TypeSafe's guidance and general turbine
vocabulary; they name no message that scored wrong, apart from the procedures the
earlier version already named (cable unwinding, oil flushing). They were frozen
before any run against real data, with one wording change made from the question
wording alone, before any run: abnormal readings such as
"too high" or "at its limit" had no question claiming them, so `names_turbine_problem`
was widened to cover them. They were not changed after that.

## results/

`results/` holds the artifacts from the one real run this project's `## Measured`
sections describe, placed there by hand so a reader can reproduce the numbers without
spending anything on Jev. See `results/README.md`.
