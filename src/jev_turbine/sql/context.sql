-- Per-event 10-minute measurement numbers for the context builder (context.py).
--
-- Expects a temp table `_context_events(idx INTEGER, turbine INTEGER, start
-- TIMESTAMPTZ)`, one row per event to build context for (`idx` is just the
-- caller's own row number, 0-based, used to match results back to events without
-- ever fetching a TIMESTAMPTZ value out of DuckDB into Python). Joins each event
-- against its own turbine's rows in `measurements`.
--
-- Timestamps in `measurements` mark the START of each 10-minute period: a row
-- stamped ts covers [ts, ts + 10min). A row counts as "before" an event only if
-- its whole period ends at or before the event start; "after" only if its whole
-- period starts at or after the event start. A row whose period straddles the
-- event start (starts before it, ends after it) counts toward neither mean,
-- since it blends pre- and post-event readings.
--
-- before_power / before_wind: mean over the 60 minutes fully before the start.
-- after_power / rotor_after: mean over the 60 minutes fully after the start.
-- freq_min/max, volt_min/max: over the one hour fully within +/-30 minutes of the
-- start ("the hour around the event" in the rendered text).
-- low_power_seconds: seconds from the start until power is first known to be at
-- least 50 kW again, looking at every known (non-NULL) reading from the start
-- onward (not just the 60-minute after-window, since a real recovery can take
-- much longer): NULL if there is no known power reading at all after the start;
-- 0 if the first known reading is already >= 50 kW (it did not drop); otherwise
-- the seconds to the first known reading >= 50 kW within 24 hours of the start,
-- or 86400 (the 24-hour cap, in seconds) if none is found that soon.

WITH before_agg AS (
    SELECT e.idx,
           AVG(m.power_kw) AS before_power,
           AVG(m.wind_ms) AS before_wind
    FROM _context_events e
    JOIN measurements m
      ON m.turbine = e.turbine
     AND m.ts >= e.start - INTERVAL 60 MINUTE
     AND m.ts + INTERVAL 10 MINUTE <= e.start
    GROUP BY e.idx
),
after_agg AS (
    SELECT e.idx,
           AVG(m.power_kw) AS after_power,
           AVG(m.rotor_rpm) AS rotor_after
    FROM _context_events e
    JOIN measurements m
      ON m.turbine = e.turbine
     AND m.ts >= e.start
     AND m.ts + INTERVAL 10 MINUTE <= e.start + INTERVAL 60 MINUTE
    GROUP BY e.idx
),
around_agg AS (
    SELECT e.idx,
           MIN(m.grid_hz) AS freq_min, MAX(m.grid_hz) AS freq_max,
           MIN(m.grid_v) AS volt_min, MAX(m.grid_v) AS volt_max
    FROM _context_events e
    JOIN measurements m
      ON m.turbine = e.turbine
     AND m.ts >= e.start - INTERVAL 30 MINUTE
     AND m.ts + INTERVAL 10 MINUTE <= e.start + INTERVAL 30 MINUTE
    GROUP BY e.idx
),
first_known AS (
    -- The power reading of the earliest fully-after-start row with a known
    -- (non-NULL) power value, regardless of how far out it is.
    SELECT e.idx,
           ARG_MIN(m.power_kw, m.ts) AS first_known_power
    FROM _context_events e
    JOIN measurements m
      ON m.turbine = e.turbine
     AND m.ts >= e.start
     AND m.power_kw IS NOT NULL
    GROUP BY e.idx
),
recovery AS (
    -- The earliest fully-after-start timestamp, within 24 hours, whose power is
    -- already >= 50 kW.
    SELECT e.idx,
           MIN(m.ts) AS recovery_ts
    FROM _context_events e
    JOIN measurements m
      ON m.turbine = e.turbine
     AND m.ts >= e.start
     AND m.ts <= e.start + INTERVAL 24 HOUR
     AND m.power_kw >= 50
    GROUP BY e.idx
)
SELECT
    e.idx,
    b.before_power,
    b.before_wind,
    a.after_power,
    a.rotor_after,
    g.freq_min,
    g.freq_max,
    g.volt_min,
    g.volt_max,
    CASE
        WHEN fk.first_known_power IS NULL THEN NULL
        WHEN fk.first_known_power >= 50 THEN 0
        WHEN rec.recovery_ts IS NOT NULL THEN date_diff('second', e.start, rec.recovery_ts)
        ELSE 86400
    END AS low_power_seconds
FROM _context_events e
LEFT JOIN before_agg b ON b.idx = e.idx
LEFT JOIN after_agg a ON a.idx = e.idx
LEFT JOIN around_agg g ON g.idx = e.idx
LEFT JOIN first_known fk ON fk.idx = e.idx
LEFT JOIN recovery rec ON rec.idx = e.idx
ORDER BY e.idx;
