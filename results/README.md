# results

Holds the artifacts from the real runs this project's README `## Measured` and
`## Measured: step 2` sections describe: `results/judgments-2016.json` (step 1's full
Jev judgments cache for the 2016 Kelmarsh data, in the same format `out/judgments.json`
uses), `results/judgments-context-2016.json` (step 2's cache, same idea, in the same
format `out/judgments-context.json` uses) and `results/summary-2016.json` (that run's
`out/summary.json`, which by the time step 2 is wired in carries both steps' usage
numbers, kept separate).

None of these files is written by this repository's code. They are placed here by
hand after a real run against the full dataset, so a reader can reproduce the numbers
in `## Measured` and `## Measured: step 2` without spending anything on Jev: see the
README's "How to fetch and run" section for `--cache results/judgments-2016.json`
(with `--no-context`, to reproduce step 1 alone) and, once
`results/judgments-context-2016.json` also exists, `--context-cache
results/judgments-context-2016.json` alongside it, to reproduce both steps.
