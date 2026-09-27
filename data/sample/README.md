# Kelmarsh sample data

A hand-picked subset of the Kelmarsh wind farm 2016 Status event log, for readers
running `jev_turbine` without downloading the full 98 MB Zenodo archive.

Source: Kelmarsh wind farm data, Cubico Sustainable Investments, Zenodo record
16807551 (https://doi.org/10.5281/zenodo.16807551), licensed CC-BY-4.0.

## What is in here

Two files, one per turbine, in the same format as the original Status CSVs (the
same `#` comment header, then `Timestamp start,Timestamp end,Duration,Status,Code,
Message,Comment,Service contract category,IEC category`):

- `Status_Kelmarsh_1_sample.csv`: 155 events
- `Status_Kelmarsh_6_sample.csv`: 125 events

Both are restricted to a set of real 2016 time windows, chosen so the sample
includes:

- All four statuses: Stop, Warning, Informational, Communication
- 10 real long stops (a Stop lasting more than 24 hours), including the Kelmarsh 1
  emergency stop that starts on 14 January 2016 and runs 211 hours
- 7 real chattering runs (the same message starting 3 or more times within 10
  minutes on one turbine), for example "Brake accumulator defect" and "System OK"
  on Kelmarsh 1, and "Overload generator fan 1" on Kelmarsh 6
- A slice of a real farm-wide alarm flood on 1 March 2016, around 17:20 to 17:55: a
  grid event that hit five of the six turbines with repeated "Frequency converter
  not ready" and "Brake accumulator defect" alarms. Because this sample only has
  two of the six turbines, running the flood check against it alone will not cross
  the "more than 10 non-informational events farm-wide within 10 minutes"
  threshold; the full 6-turbine `data/raw/` (see `scripts/fetch_kelmarsh.py`) does.

Built by `scripts/build_sample.py` from `data/raw/`; see that script for the exact
windows and why each one was picked.
