"""Loads Kelmarsh 10-minute Turbine_Data CSVs into a DuckDB `measurements` table
(one row per turbine per 10-minute period, allowed columns only) and opens that
table for querying by context.py.

Each file starts with `#` comment lines, then a header line starting `# Date and
time,...`; column names contain commas, so the header is parsed with the `csv`
module rather than split by hand, scanning only the first `_MAX_HEADER_SCAN_LINES`
lines rather than the whole ~90 MB file. Turbine_Data files carry several hundred
columns (Lost Production, Availability, IEC, Contractual, Curtailment, Energy
Budget, Potential power, Capacity, Data Availability and more); the `measurements`
table has room only for the five allowed columns plus turbine and timestamp, so no
other column can reach a Jev prompt through this loader, structurally. The bulk
load is DuckDB's own `read_csv` against the source file directly; "NaN" becomes
NULL.

Timestamps mark the START of each 10-minute period, UTC (a row stamped ts covers
[ts, ts+10min)); the session time zone is set to UTC before casting, since the
source timestamps are naive but known to be UTC.

DuckDB's Python client here cannot fetch a raw TIMESTAMPTZ value back into Python.
Timestamps only ever appear inside DuckDB SQL; results that leave DuckDB for
Python are plain numbers.
"""

from __future__ import annotations

import csv
import itertools
from collections.abc import Sequence
from pathlib import Path

import duckdb

TIMESTAMP_COLUMN = "Date and time"
HEADER_PREFIX = f"# {TIMESTAMP_COLUMN}"
_MAX_HEADER_SCAN_LINES = 50

DB_FILENAME = "kelmarsh.duckdb"
TABLE_NAME = "measurements"
TURBINE_COUNT = 6

# The only 10-minute columns the context builder (context.py) may ever see. Every
# other column in a Turbine_Data CSV is the operator's own classification or
# production-loss accounting and must never be loaded.
ALLOWED_COLUMNS: tuple[str, ...] = (
    "Power (kW)",
    "Wind speed (m/s)",
    "Rotor speed (RPM)",
    "Grid frequency (Hz)",
    "Grid voltage (V)",
)

# The `measurements` table's own column name for each allowed CSV column.
FIELD_NAMES: dict[str, str] = {
    "Power (kW)": "power_kw",
    "Wind speed (m/s)": "wind_ms",
    "Rotor speed (RPM)": "rotor_rpm",
    "Grid frequency (Hz)": "grid_hz",
    "Grid voltage (V)": "grid_v",
}

CREATE_TABLE_SQL = f"""
CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
    turbine INTEGER,
    ts TIMESTAMPTZ,
    power_kw DOUBLE,
    wind_ms DOUBLE,
    rotor_rpm DOUBLE,
    grid_hz DOUBLE,
    grid_v DOUBLE
)
"""


class LoadError(RuntimeError):
    """A Turbine_Data CSV did not match the expected shape."""


def connect(db_path: Path) -> duckdb.DuckDBPyConnection:
    """Open (or create) the persisted DuckDB file at `db_path`: UTC session time
    zone, `measurements` table created if it doesn't already exist."""
    con = duckdb.connect(str(db_path))
    _prepare(con)
    return con


def connect_in_memory() -> duckdb.DuckDBPyConnection:
    """A fresh in-memory database, `measurements` table created empty. Used for
    `run --sample`, built from data/sample/'s Turbine_Data CSVs."""
    con = duckdb.connect(":memory:")
    _prepare(con)
    return con


def connect_for_read(db_path: Path) -> duckdb.DuckDBPyConnection:
    """Open the persisted database at `db_path` for reading, the run/escalation
    path. Unlike `connect`, this never creates or silently accepts an empty
    table: it raises LoadError if the file, the `measurements` table, or data for
    one or more turbines is missing, since a run must never build context from an
    empty measurements table."""
    db_path = Path(db_path)
    if not db_path.exists():
        raise LoadError(f"{db_path} does not exist; run fetch_kelmarsh first.")
    con = duckdb.connect(str(db_path))
    con.execute("SET TimeZone='UTC'")
    tables = con.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_name = ?", [TABLE_NAME]
    ).fetchall()
    if not tables:
        con.close()
        raise LoadError(f"{db_path} has no '{TABLE_NAME}' table; run fetch_kelmarsh first.")
    if not is_populated(con):
        con.close()
        raise LoadError(
            f"{db_path}'s '{TABLE_NAME}' table is missing data for one or more turbines; run fetch_kelmarsh first."
        )
    return con


def _prepare(con: duckdb.DuckDBPyConnection) -> None:
    con.execute("SET TimeZone='UTC'")
    con.execute(CREATE_TABLE_SQL)


def is_populated(con: duckdb.DuckDBPyConnection, expected_turbines: int = TURBINE_COUNT) -> bool:
    """True if `measurements` already has at least one row for each of
    `expected_turbines` distinct turbines."""
    [count] = con.execute(f"SELECT COUNT(DISTINCT turbine) FROM {TABLE_NAME}").fetchone()
    return count >= expected_turbines


def build_database(con: duckdb.DuckDBPyConnection, csv_paths: Sequence[Path]) -> None:
    """Fresh load: clears `measurements`, then loads every Turbine_Data CSV in
    `csv_paths` into it, allowed columns only. The turbine number comes from each
    file's own `# Turbine: Kelmarsh <n>` comment line, the same as loader.py reads
    for the Status CSVs. Raises LoadError if a file doesn't have the expected
    comment/header shape."""
    con.execute(f"DELETE FROM {TABLE_NAME}")
    for path in csv_paths:
        header_idx, header, turbine_number = _scan_header(path)
        _load_one(con, path, turbine_number, header_idx, header)


def build_database_from_dir(con: duckdb.DuckDBPyConnection, folder: Path) -> list[Path]:
    """Convenience wrapper: build_database from every
    Turbine_Data_Kelmarsh_*.csv file in `folder` (data/raw/ for a real run,
    data/sample/ for `run --sample`). Returns the paths it loaded."""
    paths = sorted(Path(folder).glob("Turbine_Data_Kelmarsh_*.csv"))
    build_database(con, paths)
    return paths


def _scan_header(path: Path) -> tuple[int, list[str], int]:
    """Returns (header line index, column names, turbine number), scanning only
    the leading comment lines of `path` (well under _MAX_HEADER_SCAN_LINES for
    every real Kelmarsh file) rather than reading the whole CSV into Python."""
    turbine_number: int | None = None
    with path.open(encoding="utf-8") as f:
        for i, line in enumerate(itertools.islice(f, _MAX_HEADER_SCAN_LINES)):
            if line.startswith("# Turbine:"):
                name = line.split(":", 1)[1].strip()
                digits = "".join(ch for ch in name if ch.isdigit())
                if digits:
                    turbine_number = int(digits)
            if line.startswith(HEADER_PREFIX):
                if turbine_number is None:
                    raise LoadError(f"{path}: no '# Turbine:' header line before the column header")
                header = next(csv.reader([line[2:]]))
                return i, header, turbine_number
    raise LoadError(f"{path}: no '{HEADER_PREFIX}' header line in the first {_MAX_HEADER_SCAN_LINES} lines")


def _load_one(con: duckdb.DuckDBPyConnection, path: Path, turbine_number: int, header_idx: int, header: list[str]) -> None:
    types = {
        name: ("TIMESTAMP" if name == TIMESTAMP_COLUMN else "DOUBLE" if name in ALLOWED_COLUMNS else "VARCHAR")
        for name in header
    }
    columns_literal = _duckdb_columns_literal(types)
    select_cols = ", ".join(
        f'CASE WHEN isnan("{column}") THEN NULL ELSE "{column}" END AS {FIELD_NAMES[column]}'
        for column in ALLOWED_COLUMNS
    )
    sql = (
        f"INSERT INTO {TABLE_NAME} "
        f'SELECT {turbine_number} AS turbine, "{TIMESTAMP_COLUMN}"::TIMESTAMPTZ AS ts, {select_cols} '
        f"FROM read_csv({_sql_literal(str(path))}, skip={header_idx}, header=True, columns={columns_literal})"
    )
    con.execute(sql)


def _sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _duckdb_columns_literal(types: dict[str, str]) -> str:
    return "{" + ", ".join(f"{_sql_literal(name)}: {_sql_literal(kind)}" for name, kind in types.items()) + "}"
