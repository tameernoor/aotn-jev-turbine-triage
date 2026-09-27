# results

Holds the artifacts from the one real run this project's README `## Measured` section
describes. `results/judgments-2016.json` is step 1's
full Jev judgments cache for the 2016 Kelmarsh data, in the same format
`out/judgments.json` uses, and `results/summary-2016.json` is that run's
`out/summary.json`. Step 2 (the turbine's production numbers) needs no cache of its
own, since it never calls Jev; it only reads `data/raw/kelmarsh.duckdb`.

Neither file is written by this repository's code. They are placed here by hand after
a real run against the full dataset, so a reader can reproduce the numbers in
`## Measured` without spending anything on Jev: see the
README's "How to fetch and run" section for `--cache results/judgments-2016.json`
(add `--no-context` to reproduce step 1 alone, without needing
`data/raw/kelmarsh.duckdb` at all).

