-- Per-event 10-minute measurement numbers for the context builder (context.py).
--
-- Expects a temp table `_context_events(idx INTEGER, turbine INTEGER, start
-- TIMESTAMPTZ, before_end TIMESTAMPTZ, after_start TIMESTAMPTZ)`, one row per
-- event (idx is the caller's own row number, used to match results back to
-- events without ever fetching a TIMESTAMPTZ out of DuckDB into Python).
-- `before_end`/`after_start` are computed in Python (see
-- context._grid_boundaries): the row whose [ts, ts+10min) period contains
-- `start` straddles it and is excluded from both means; `before_end` is that
-- row's own start, `after_start` its end. When `start` falls exactly on the
-- 10-minute grid there is no straddling row and before_end = after_start =
-- start.
--
-- before_power / before_wind: mean over the 6 rows ending at before_end (a full
-- 60 minutes, always 6 rows, never fewer, regardless of where in its 10-minute
-- slot the event start falls).
-- after_power / rotor_after: mean over the 6 rows starting at after_start.
-- freq_min/max, volt_min/max: the straddling row (or, on the grid, the row at
-- `start`) plus 3 rows before it and 3 after (7 rows; kept the same rendered
-- phrase "the hour around the event" even though it is 70 minutes of rows).
-- low_power_recovered_seconds / low_power_gap_seconds: see below.

WITH before_agg AS (
    SELECT e.idx,
           AVG(m.power_kw) AS before_power,
           AVG(m.wind_ms) AS before_wind
    FROM _context_events e
    JOIN measurements m
      ON m.turbine = e.turbine
     AND m.ts >= e.before_end - INTERVAL 60 MINUTE
     AND m.ts <= e.before_end - INTERVAL 10 MINUTE
    GROUP BY e.idx
),
after_agg AS (
    SELECT e.idx,
           AVG(m.power_kw) AS after_power,
           AVG(m.rotor_rpm) AS rotor_after
    FROM _context_events e
    JOIN measurements m
      ON m.turbine = e.turbine
     AND m.ts >= e.after_start
     AND m.ts <= e.after_start + INTERVAL 50 MINUTE
    GROUP BY e.idx
),
around_agg AS (
    SELECT e.idx,
           MIN(m.grid_hz) AS freq_min, MAX(m.grid_hz) AS freq_max,
           MIN(m.grid_v) AS volt_min, MAX(m.grid_v) AS volt_max
    FROM _context_events e
    JOIN measurements m
      ON m.turbine = e.turbine
     AND m.ts >= e.before_end - INTERVAL 30 MINUTE
     AND m.ts <= e.before_end + INTERVAL 30 MINUTE
    GROUP BY e.idx
),

-- Below-50kW duration: walk forward from the true event start (not the grid
-- boundaries above), over every KNOWN (non-NULL power) reading up to 24 hours
-- out, looking for whichever comes first: a recovery to >= 50 kW, or the data
-- stopping (a gap of more than 30 minutes since the previous known reading, or
-- since the start for the very first one).
after_known AS (
    SELECT e.idx, e.start, m.ts, m.power_kw,
           LAG(m.ts) OVER (PARTITION BY e.idx ORDER BY m.ts) AS prev_ts
    FROM _context_events e
    JOIN measurements m
      ON m.turbine = e.turbine
     AND m.ts >= e.start
     AND m.ts <= e.start + INTERVAL 24 HOUR
     AND m.power_kw IS NOT NULL
),
after_known_gap AS (
    SELECT idx, start, ts, power_kw, prev_ts,
           date_diff('second', COALESCE(prev_ts, start), ts) AS gap_since_prev_seconds
    FROM after_known
),
first_stop AS (
    SELECT idx, MIN(ts) AS stop_ts
    FROM after_known_gap
    WHERE power_kw >= 50 OR gap_since_prev_seconds > 1800
    GROUP BY idx
),
stop_detail AS (
    -- The row that resolves the search: either the recovery reading, or the
    -- first reading found past a 30-minute gap (in which case the *previous*
    -- reading, prev_ts, is the last one we can still vouch for).
    SELECT g.idx, g.start, g.ts, g.power_kw, g.prev_ts
    FROM after_known_gap g
    JOIN first_stop fs ON fs.idx = g.idx AND fs.stop_ts = g.ts
),
last_known AS (
    -- Used only when the search above found neither a recovery nor a gap: the
    -- last known reading in the 24-hour window, to check whether the data
    -- simply stops (for good) before reaching the 24-hour mark.
    SELECT idx, start, MAX(ts) AS last_ts
    FROM after_known_gap
    GROUP BY idx, start
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
    -- NULL unless a recovery was seen: 0 if the very first known reading (no
    -- prev_ts) was already >= 50 kW (it did not drop, so there is no drop
    -- duration to report), otherwise the exact seconds to the first known
    -- reading >= 50 kW.
    CASE
        WHEN sd.power_kw >= 50 AND sd.prev_ts IS NULL THEN 0
        WHEN sd.power_kw >= 50 THEN date_diff('second', sd.start, sd.ts)
    END AS low_power_recovered_seconds,
    -- Non-NULL only when the data stopped (a gap) before a recovery was seen and
    -- before the 24-hour cap: seconds from the start to the end of the last
    -- reading we can still vouch for. NULL and low_power_recovered_seconds also
    -- NULL, with after_power present, means the readings stayed low and known
    -- (no gap) all the way to the 24-hour cap.
    CASE
        WHEN sd.idx IS NOT NULL AND sd.power_kw < 50
            THEN date_diff('second', sd.start, COALESCE(sd.prev_ts + INTERVAL 10 MINUTE, sd.start))
        WHEN sd.idx IS NULL AND lk.idx IS NOT NULL
             AND date_diff('second', lk.last_ts + INTERVAL 10 MINUTE, lk.start + INTERVAL 24 HOUR) > 1800
            THEN date_diff('second', lk.start, lk.last_ts + INTERVAL 10 MINUTE)
    END AS low_power_gap_seconds
FROM _context_events e
LEFT JOIN before_agg b ON b.idx = e.idx
LEFT JOIN after_agg a ON a.idx = e.idx
LEFT JOIN around_agg g ON g.idx = e.idx
LEFT JOIN stop_detail sd ON sd.idx = e.idx
LEFT JOIN last_known lk ON lk.idx = e.idx
ORDER BY e.idx;
