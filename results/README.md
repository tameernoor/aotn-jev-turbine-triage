# results

Holds the artifacts from the real run this project's README `## Measured` section
describes: `results/judgments-2016.json` (a full Jev judgments cache for the 2016
Kelmarsh data, in the same format `out/judgments.json` uses) and
`results/summary-2016.json` (that run's `out/summary.json`).

Neither file is written by this repository's code. They are placed here by hand after
a real run against the full dataset, so a reader can reproduce the numbers in
`## Measured` without spending anything on Jev: see the README's "How to fetch and
run" section for the two ways to use `results/judgments-2016.json` as a starting
cache.
