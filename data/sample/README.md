# Kelmarsh sample data

A hand-picked subset of the Kelmarsh wind farm 2016 Status event log, for readers
running `jev_turbine` without downloading the full 98 MB Zenodo archive.

Source: Kelmarsh wind farm data, Cubico Sustainable Investments, Zenodo record
16807551 (https://doi.org/10.5281/zenodo.16807551), licensed CC-BY-4.0
(https://creativecommons.org/licenses/by/4.0/).

## What is in here

Six files, two per turbine (Status events and 10-minute measurements), in the same
format as the originals.

The Status files keep the same `#` comment header, then `Timestamp start,Timestamp
end,Duration,Status,Code,Message,Comment,Service contract category,IEC category`:

- `Status_Kelmarsh_1_sample.csv`: 155 events
- `Status_Kelmarsh_2_sample.csv`: 13 events (one hour only, see the flood below)
- `Status_Kelmarsh_6_sample.csv`: 125 events

The Turbine_Data files keep the same `#` comment header (so the same loader reads
them), but only five columns: `Power (kW)`, `Wind speed (m/s)`, `Rotor speed
(RPM)`, `Grid frequency (Hz)` and `Grid voltage (V)`, the only 10-minute columns
the context builder (`context.py`) is allowed to use. Every other column
(production-loss, availability, curtailment and the operator's own IEC
classification) is dropped. Each file covers its turbine's Status sample events,
plus 7 days before the earliest and 1 day after the latest, unless that span was
too large to keep the file small; Kelmarsh 1 and 6's Status events are spread
across most of the year, so those two instead cover only the 1 day either side of
each non-informational sample event:

- `Turbine_Data_Kelmarsh_1_sample.csv`: 4,226 rows, 13 windows around
  non-informational events
- `Turbine_Data_Kelmarsh_2_sample.csv`: 1,155 rows, full span (its Status sample
  is one hour only)
- `Turbine_Data_Kelmarsh_6_sample.csv`: 3,791 rows, 12 windows around
  non-informational events

All are restricted to a set of real 2016 time windows, chosen so the sample
includes:

- All four statuses: Stop, Warning, Informational, Communication
- 10 real long stops (a Stop lasting more than 24 hours), including the Kelmarsh 1
  emergency stop that starts on 14 January 2016 and runs 211 hours
- 7 real chattering runs (the same message starting 3 or more times within 10
  minutes on one turbine), for example "Brake accumulator defect" and "System OK"
  on Kelmarsh 1, and "Overload generator fan 1" on Kelmarsh 6
- A real farm-wide alarm flood: on 1 March 2016 around 17:20 to 17:55, a grid
  event hit five of the six turbines with repeated "Frequency converter not
  ready" and "Brake accumulator defect" alarms. Kelmarsh 1 and 6 alone only
  reach 8 non-informational events in any single 10-minute window during that
  hour, one short of the "more than 10 non-informational events farm-wide
  within 10 minutes" flood rule, so Kelmarsh 2's real events for that same
  hour are included too. With all three turbines, running the flood check
  against this sample does flag a real flood window.

The Status files are built by `scripts/build_sample.py` from `data/raw/`; see that
script for the exact windows and why each one was picked. The Turbine_Data files
were built once, directly from `data/raw/` and the Status samples above, by the
window rule described here; there is no separate committed script for that step.
