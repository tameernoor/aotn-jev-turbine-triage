from datetime import datetime, timezone
from pathlib import Path

import pytest

from jev_turbine.measurements import (
    ALLOWED_COLUMNS,
    FIELD_NAMES,
    LoadError,
    build_database,
    build_database_from_dir,
    connect_in_memory,
    is_populated,
)

# A header shaped like the real Turbine_Data files: some columns whose names contain
# commas and are quoted (which would break a naive split on ","), some forbidden
# columns that share a prefix with an allowed one (regression check that comma
# parsing, not prefix matching, decides what is kept), and the five allowed columns
# out of order and interspersed with forbidden ones.
_HEADER_COLUMNS = [
    "Date and time",
    "Wind speed (m/s)",
    "Wind speed, Standard deviation (m/s)",
    "Lost Production (Contractual) (kWh)",
    "Power (kW)",
    "Power, Standard deviation (kW)",
    "Potential power learned PC (kW)",
    "Availability (%)",
    "Grid voltage (V)",
    "Grid voltage, Max (V)",
    "Rotor speed (RPM)",
    "IEC category",
    "Contractual Availability (%)",
    "Curtailment reason",
    "Energy Budget - Default (kWh)",
    "Capacity factor (%)",
    "Data Availability (%)",
    "Grid frequency (Hz)",
]


def _header(turbine: str) -> str:
    lines = [
        "# This file was exported by Greenbyte at 2022-01-27 10:33:05. Please see "
        "https://www.greenbyte.com for more information about Greenbyte.\n",
        "#\n",
        f"# Turbine: {turbine}\n",
        "# Turbine type: Senvion MM92\n",
        "# Time zone: UTC\n",
        "# Time interval: 2016-01-01 00:00:00 - 2017-01-01 00:00:00 (366 days)\n",
        "#\n",
        '# Data that is missing or is erroneous has been marked with the value "NaN"\n',
        "#\n",
    ]
    header_line = "# " + ",".join(f'"{c}"' if "," in c else c for c in _HEADER_COLUMNS) + "\n"
    lines.append(header_line)
    return "".join(lines)


def _row(values: dict[str, str]) -> str:
    return ",".join(values.get(c, "") for c in _HEADER_COLUMNS) + "\n"


def _write(path: Path, turbine: str, rows: list[str]) -> None:
    path.write_text(_header(turbine) + "".join(rows), encoding="utf-8")


def test_loads_and_keeps_only_allowed_columns(tmp_path):
    row = _row(
        {
            "Date and time": "2016-01-24 16:50:00",
            "Wind speed (m/s)": "7.97",
            "Wind speed, Standard deviation (m/s)": "1.23",
            "Lost Production (Contractual) (kWh)": "45.6",
            "Power (kW)": "75.34",
            "Power, Standard deviation (kW)": "9.9",
            "Potential power learned PC (kW)": "500",
            "Availability (%)": "100",
            "Grid voltage (V)": "693.26",
            "Grid voltage, Max (V)": "700",
            "Rotor speed (RPM)": "13.2",
            "IEC category": "Forced outage",
            "Contractual Availability (%)": "95",
            "Curtailment reason": "Grid",
            "Energy Budget - Default (kWh)": "10",
            "Capacity factor (%)": "40",
            "Data Availability (%)": "99",
            "Grid frequency (Hz)": "49.96",
        }
    )
    path = tmp_path / "Turbine_Data_Kelmarsh_1_2016.csv"
    _write(path, "Kelmarsh 1", [row])

    con = connect_in_memory()
    build_database(con, [path])

    result = con.execute(
        "SELECT turbine, power_kw, wind_ms, rotor_rpm, grid_hz, grid_v FROM measurements"
    ).fetchall()
    assert result == [(1, 75.34, 7.97, 13.2, 49.96, 693.26)]

    # Every measurements column beyond turbine/ts is exactly the five allowed
    # fields; a forbidden CSV column can never occupy a column that doesn't exist.
    columns = {row[1] for row in con.execute("PRAGMA table_info('measurements')").fetchall()}
    assert columns == {"turbine", "ts", *FIELD_NAMES.values()}


def test_forbidden_columns_never_appear_in_the_table_schema():
    columns = set()
    con = connect_in_memory()
    for row in con.execute("PRAGMA table_info('measurements')").fetchall():
        columns.add(row[1])

    forbidden_markers = (
        "Lost Production",
        "Availability",
        "IEC",
        "Contractual",
        "Curtailment",
        "Energy Budget",
        "Potential power",
        "Capacity",
    )
    assert columns == {"turbine", "ts", *FIELD_NAMES.values()}
    for column in columns:
        assert not any(marker.lower() in column.lower() for marker in forbidden_markers)


def test_nan_values_become_null(tmp_path):
    row = _row(
        {
            "Date and time": "2016-01-03 00:00:00",
            "Wind speed (m/s)": "NaN",
            "Power (kW)": "NaN",
            "Rotor speed (RPM)": "NaN",
            "Grid frequency (Hz)": "NaN",
            "Grid voltage (V)": "NaN",
        }
    )
    path = tmp_path / "Turbine_Data_Kelmarsh_1_2016.csv"
    _write(path, "Kelmarsh 1", [row])

    con = connect_in_memory()
    build_database(con, [path])

    result = con.execute(
        "SELECT power_kw, wind_ms, rotor_rpm, grid_hz, grid_v FROM measurements"
    ).fetchall()
    assert result == [(None, None, None, None, None)]


def test_timestamps_are_utc_and_survive_a_round_trip_through_a_bound_parameter(tmp_path):
    row = _row({"Date and time": "2016-01-24 16:50:00", "Power (kW)": "1"})
    path = tmp_path / "Turbine_Data_Kelmarsh_1_2016.csv"
    _write(path, "Kelmarsh 1", [row])

    con = connect_in_memory()
    build_database(con, [path])

    target = datetime(2016, 1, 24, 16, 50, 0, tzinfo=timezone.utc)
    [(matches,)] = con.execute("SELECT COUNT(*) FROM measurements WHERE ts = ?", [target]).fetchall()
    assert matches == 1
    # An hour-shifted timestamp must NOT match: proves this is a real UTC instant,
    # not the naive wall-clock value reinterpreted in some other zone.
    off_by_one_hour = datetime(2016, 1, 24, 17, 50, 0, tzinfo=timezone.utc)
    [(no_match,)] = con.execute("SELECT COUNT(*) FROM measurements WHERE ts = ?", [off_by_one_hour]).fetchall()
    assert no_match == 0


def test_multiple_turbines_are_loaded_and_distinguishable(tmp_path):
    path1 = tmp_path / "Turbine_Data_Kelmarsh_1_2016.csv"
    path6 = tmp_path / "Turbine_Data_Kelmarsh_6_2016.csv"
    _write(path1, "Kelmarsh 1", [_row({"Date and time": "2016-01-03 00:00:00", "Power (kW)": "1"})])
    _write(path6, "Kelmarsh 6", [_row({"Date and time": "2016-01-03 00:00:00", "Power (kW)": "6"})])

    con = connect_in_memory()
    build_database(con, [path1, path6])

    result = con.execute("SELECT turbine, power_kw FROM measurements ORDER BY turbine").fetchall()
    assert result == [(1, 1.0), (6, 6.0)]


def test_build_database_from_dir_only_reads_turbine_data_files(tmp_path):
    _write(
        tmp_path / "Turbine_Data_Kelmarsh_1_2016.csv",
        "Kelmarsh 1",
        [_row({"Date and time": "2016-01-03 00:00:00", "Power (kW)": "1"})],
    )
    (tmp_path / "Status_Kelmarsh_1_2016.csv").write_text("not a turbine data file\n", encoding="utf-8")
    (tmp_path / "Metmast_Kelmarsh_2016.csv").write_text("not a turbine data file either\n", encoding="utf-8")

    con = connect_in_memory()
    loaded = build_database_from_dir(con, tmp_path)

    assert [p.name for p in loaded] == ["Turbine_Data_Kelmarsh_1_2016.csv"]
    [(count,)] = con.execute("SELECT COUNT(*) FROM measurements").fetchall()
    assert count == 1


def test_build_database_clears_existing_rows_before_reloading(tmp_path):
    path = tmp_path / "Turbine_Data_Kelmarsh_1_2016.csv"
    _write(path, "Kelmarsh 1", [_row({"Date and time": "2016-01-03 00:00:00", "Power (kW)": "1"})])

    con = connect_in_memory()
    build_database(con, [path])
    build_database(con, [path])

    [(count,)] = con.execute("SELECT COUNT(*) FROM measurements").fetchall()
    assert count == 1  # not 2: the second build_database call cleared the first


def test_is_populated_counts_distinct_turbines(tmp_path):
    con = connect_in_memory()
    assert is_populated(con) is False

    path = tmp_path / "Turbine_Data_Kelmarsh_1_2016.csv"
    _write(path, "Kelmarsh 1", [_row({"Date and time": "2016-01-03 00:00:00", "Power (kW)": "1"})])
    build_database(con, [path])

    assert is_populated(con) is False  # only one of six turbines
    assert is_populated(con, expected_turbines=1) is True


def test_missing_header_line_raises_load_error(tmp_path):
    path = tmp_path / "Turbine_Data_Kelmarsh_1_2016.csv"
    path.write_text("# Turbine: Kelmarsh 1\n#\nno header here\n", encoding="utf-8")

    con = connect_in_memory()
    with pytest.raises(LoadError):
        build_database(con, [path])


def test_missing_turbine_line_raises_load_error(tmp_path):
    path = tmp_path / "Turbine_Data_Kelmarsh_1_2016.csv"
    path.write_text(
        "# no turbine line here\n# Date and time,Power (kW)\n2016-01-03 00:00:00,1\n",
        encoding="utf-8",
    )

    con = connect_in_memory()
    with pytest.raises(LoadError):
        build_database(con, [path])


def test_allowed_columns_matches_field_names_keys():
    assert set(ALLOWED_COLUMNS) == set(FIELD_NAMES)
