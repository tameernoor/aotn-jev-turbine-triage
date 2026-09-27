# results

Holds the artifacts from the real runs this project's README `## Measured` and
`## Measured: step 2` sections describe: `results/judgments-2016.json` (step 1's full
Jev judgments cache for the 2016 Kelmarsh data, in the same format `out/judgments.json`
uses), `results/judgments-context-2016.json` (step 2's cache, same idea, in the same
format `out/judgments-context.json` uses), `results/summary-2016.json` (the step-1 run's `out/summary.json`) and
`results/summary-context-2016.json` (the step-2 run's `out/summary.json`; its step-1
usage is zero because step 1 was read from `results/judgments-2016.json`).

None of these files is written by this repository's code. They are placed here by
hand after a real run against the full dataset, so a reader can reproduce the numbers
in `## Measured` and `## Measured: step 2` without spending anything on Jev: see the
README's "How to fetch and run" section for `--cache results/judgments-2016.json`
(with `--no-context`, to reproduce step 1 alone) and `--context-cache
results/judgments-context-2016.json` alongside it, to reproduce both steps.
