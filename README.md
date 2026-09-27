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
local mirror if `--from DIR` or `KELMARSH_LOCAL_DIR` points at one):

```
uv run python scripts/fetch_kelmarsh.py
```

Run the full pipeline against it. This needs `TYPESAFE_API_KEY` in the environment
(`.env`, loaded by `--env-file`):

```
uv run --env-file .env python -m jev_turbine run
```

Run against the committed sample instead, without fetching anything:

```
uv run --env-file .env python -m jev_turbine run --sample
```

`--data DIR` points at a different folder of Status CSVs, and `--out DIR` changes
where output is written (default `out/`). Each run writes:

- `out/judgments.json`: Jev's answers, cached and keyed by status and then message, so
  it is one entry per distinct `(status, message)` pair. A pair already in this file
  is never asked again, so a second run against the same data only pays for whatever
  is new. The file also carries a hash of `questions/event.yaml`; if that hash does
  not match the questions this run is using, the cache is not trusted, is ignored
  instead of silently serving stale answers, and the run says so, on stdout and in
  `out/summary.json`.
- `out/triage.jsonl`: one JSON line per event, in event order, with the turbine,
  start and end timestamps, duration, status, message, the triage class, the reasons
  behind it, and whether it was flagged as chattering or part of a flood.
- `out/evaluation.json`: the accuracy report described below.
- `out/summary.json`: the same summary printed at the end, as JSON: triage counts,
  the top act_now messages, evaluation accuracy, and how many Jev calls the run made,
  at what token count, cost and wall time, plus the model ids seen in those calls.

Jev itself is only ever built the first time a pair is actually asked, so a run whose
cache already covers every pair needs no `TYPESAFE_API_KEY` and makes no call at all.
To reproduce `## Measured` below without calling Jev, copy
`results/judgments-2016.json` to `out/judgments.json` and run, or point `--cache FILE`
at it directly (`--cache` seeds the run from that file instead of `out/judgments.json`
without ever writing back to it; the merged result still lands in
`out/judgments.json` as usual):

```
uv run --env-file .env python -m jev_turbine run --cache results/judgments-2016.json
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

One run against `jev-1.13.0` over all of 2016 for the six turbines. The question
wording was committed before this run and not changed after it.

### Speed and cost

- 14,019 events, 98 distinct kinds of non-informational event, so 98 Jev requests
  (one per kind, all three questions in each). Informational events never go to Jev.
- 28 seconds for the whole year, with the requests sent one after another. 57,867
  input tokens, $0.0024 in total.

### Cause, against the operator's own category

959 non-informational events carry the operator's IEC 61400-26 category. Mapped to
`cause` with the table above:

- Jev agreed with the operator on 709 of 959 events (74 %), and on 47 of 61 distinct
  messages (77 %).
- `planned` 508 of 508 and `external` 48 of 48 agreed. The disagreements are all on
  `fault` and `running`.
- Where they disagree, it is often the question rather than the reading. "Maximum
  grid frequency" is external by our wording and a forced outage in the operator's
  books. "Manual stop - remote" reads as planned and is booked as a forced outage.
  Warnings such as "Vane 2 defect" or "Error brake resistor CHP" describe a defect,
  so Jev says fault, while the operator books them as full performance because the
  turbine kept producing. `cause` mixes why something happened with whether the
  turbine is running, and these rows show it.
- Jev was genuinely split on a few: "High rotor speed nacelle" 0.49 fault and 0.49
  external, "WEC shut down" 0.43 planned and 0.40 fault.

### Triage

- 69 events went to act now (the top ones: "Oscillation encoder tower", "Safety
  chain open", "High rotor speed nacelle", "Tower oscillation X level 2", "Emergency
  stop base box"), 1,457 to monitor, 12,493 to no action.
- Most of the monitor pile is uncertainty, not faults. For "Battery test" Jev put
  `safety_related` at 0.22, just above the 0.2 that counts as no, which alone sends
  258 routine tests to monitor. `needs_site_visit` sat between 0.3 and 0.6 for most
  messages: from three or four words of vendor text, whether someone has to drive
  out is often not knowable, and Jev says so.
- 124 informational events went to monitor for chattering alone.

What this shows: a narrow question works when the text carries the answer (planned,
external, safety chains, emergency stops). When it does not, the model hovers in the
middle, and the code's thresholds turn that into "look at it". Tightening the
thresholds or the wording would change these numbers; we did not, so they stay
honest.
