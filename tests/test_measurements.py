from datetime import datetime, timezone
from pathlib import Path

import pytest

from jev_turbine.measurements import ALLOWED_COLUMNS, LoadError, load_measurements

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
    header_line = "# " + ",".join(
        f'"{c}"' if "," in c else c for c in _HEADER_COLUMNS
    ) + "\n"
    lines.append(header_line)
    return "".join(lines)


def _row(values: dict[str, str]) -> str:
    return ",".join(values.get(c, "") for c in _HEADER_COLUMNS) + "\n"


def _write(path: Path, turbine: str, rows: list[str]) -> None:
    path.write_text(_header(turbine) + "".join(rows), encoding="utf-8")


def test_parses_header_and_keeps_only_allowed_columns(tmp_path):
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
    _write(tmp_path / "Turbine_Data_Kelmarsh_1_2016.csv", "Kelmarsh 1", [row])

    by_turbine = load_measurements(tmp_path)

    assert set(by_turbine) == {1}
    [entry] = by_turbine[1]
    assert entry.timestamp == datetime(2016, 1, 24, 16, 50, 0, tzinfo=timezone.utc)
    assert entry.values == {
        "Power (kW)": 75.34,
        "Wind speed (m/s)": 7.97,
        "Rotor speed (RPM)": 13.2,
        "Grid frequency (Hz)": 49.96,
        "Grid voltage (V)": 693.26,
    }


def test_forbidden_columns_never_appear_in_any_row(tmp_path):
    row = _row(
        {
            "Date and time": "2016-01-24 16:50:00",
            "Power (kW)": "75.34",
            "Lost Production (Contractual) (kWh)": "45.6",
            "Availability (%)": "100",
            "IEC category": "Forced outage",
            "Contractual Availability (%)": "95",
            "Curtailment reason": "Grid",
            "Energy Budget - Default (kWh)": "10",
            "Capacity factor (%)": "40",
            "Data Availability (%)": "99",
        }
    )
    _write(tmp_path / "Turbine_Data_Kelmarsh_1_2016.csv", "Kelmarsh 1", [row])
    by_turbine = load_measurements(tmp_path)

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
    for rows in by_turbine.values():
        for entry in rows:
            assert set(entry.values) <= set(ALLOWED_COLUMNS)
            for key in entry.values:
                assert not any(marker in key for marker in forbidden_markers)


def test_nan_values_become_none(tmp_path):
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
    _write(tmp_path / "Turbine_Data_Kelmarsh_1_2016.csv", "Kelmarsh 1", [row])

    [entry] = load_measurements(tmp_path)[1]

    assert entry.values == {c: None for c in ALLOWED_COLUMNS}


def test_timestamps_are_utc(tmp_path):
    row = _row({"Date and time": "2016-01-24 16:50:00", "Power (kW)": "1"})
    _write(tmp_path / "Turbine_Data_Kelmarsh_1_2016.csv", "Kelmarsh 1", [row])

    [entry] = load_measurements(tmp_path)[1]

    assert entry.timestamp.tzinfo == timezone.utc


def test_rows_within_a_turbine_are_sorted_by_timestamp(tmp_path):
    rows = [
        _row({"Date and time": "2016-01-03 00:20:00", "Power (kW)": "3"}),
        _row({"Date and time": "2016-01-03 00:00:00", "Power (kW)": "1"}),
        _row({"Date and time": "2016-01-03 00:10:00", "Power (kW)": "2"}),
    ]
    _write(tmp_path / "Turbine_Data_Kelmarsh_1_2016.csv", "Kelmarsh 1", rows)

    entries = load_measurements(tmp_path)[1]

    assert [e.values["Power (kW)"] for e in entries] == [1, 2, 3]


def test_multiple_turbines_are_grouped_by_number(tmp_path):
    _write(
        tmp_path / "Turbine_Data_Kelmarsh_1_2016.csv",
        "Kelmarsh 1",
        [_row({"Date and time": "2016-01-03 00:00:00", "Power (kW)": "1"})],
    )
    _write(
        tmp_path / "Turbine_Data_Kelmarsh_6_2016.csv",
        "Kelmarsh 6",
        [_row({"Date and time": "2016-01-03 00:00:00", "Power (kW)": "6"})],
    )

    by_turbine = load_measurements(tmp_path)

    assert set(by_turbine) == {1, 6}
    assert by_turbine[1][0].values["Power (kW)"] == 1
    assert by_turbine[6][0].values["Power (kW)"] == 6


def test_only_turbine_data_files_are_read(tmp_path):
    _write(
        tmp_path / "Turbine_Data_Kelmarsh_1_2016.csv",
        "Kelmarsh 1",
        [_row({"Date and time": "2016-01-03 00:00:00", "Power (kW)": "1"})],
    )
    (tmp_path / "Status_Kelmarsh_1_2016.csv").write_text("not a turbine data file\n", encoding="utf-8")
    (tmp_path / "Metmast_Kelmarsh_2016.csv").write_text("not a turbine data file either\n", encoding="utf-8")

    by_turbine = load_measurements(tmp_path)

    assert set(by_turbine) == {1}
    assert len(by_turbine[1]) == 1


def test_missing_header_line_raises_load_error(tmp_path):
    path = tmp_path / "Turbine_Data_Kelmarsh_1_2016.csv"
    path.write_text("# Turbine: Kelmarsh 1\n#\nno header here\n", encoding="utf-8")

    with pytest.raises(LoadError):
        load_measurements(tmp_path)


def test_missing_turbine_line_raises_load_error(tmp_path):
    path = tmp_path / "Turbine_Data_Kelmarsh_1_2016.csv"
    path.write_text(
        "# no turbine line here\n# Date and time,Power (kW)\n2016-01-03 00:00:00,1\n",
        encoding="utf-8",
    )

    with pytest.raises(LoadError):
        load_measurements(tmp_path)
