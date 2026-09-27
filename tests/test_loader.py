from pathlib import Path

from jev_turbine.loader import LoadError, load_events


def _header(turbine: str) -> str:
    return (
        "# This file was exported by Greenbyte at 2022-01-27 10:52:06. Please see "
        "https://www.greenbyte.com for more information about Greenbyte.\n"
        "#\n"
        f"# Turbine: {turbine}\n"
        "# Turbine type: Senvion MM92 (Senvion MM92 kW)\n"
        "# Time zone: UTC\n"
        "#\n"
        "Timestamp start,Timestamp end,Duration,Status,Code,Message,Comment,"
        "Service contract category,IEC category\n"
    )


def _write(path: Path, turbine: str, rows: list[str]) -> None:
    path.write_text(_header(turbine) + "".join(rows), encoding="utf-8")


def test_parses_header_and_columns(tmp_path):
    _write(
        tmp_path / "Status_Kelmarsh_1_2016.csv",
        "Kelmarsh 1",
        [
            "2016-01-14 19:28:03,2016-01-23 14:36:32,211:08:29,Stop,111,"
            "Emergency stop nacelle,,Emergency stop switch (Nacelle) (11),Forced outage\n"
        ],
    )

    events = load_events(tmp_path)

    assert len(events) == 1
    event = events[0]
    assert event.turbine == "Kelmarsh 1"
    assert event.status == "Stop"
    assert event.code == "111"
    assert event.message == "Emergency stop nacelle"
    assert event.iec_category == "Forced outage"
    assert event.duration_seconds == 211 * 3600 + 8 * 60 + 29


def test_hours_can_exceed_24_in_duration(tmp_path):
    _write(
        tmp_path / "Status_Kelmarsh_1_2016.csv",
        "Kelmarsh 1",
        ["2016-01-14 19:28:03,2016-01-23 14:36:32,211:08:29,Stop,111,X,,,\n"],
    )

    events = load_events(tmp_path)

    assert events[0].duration_seconds == 760109.0


def test_dash_end_and_duration_and_blank_iec_become_none(tmp_path):
    _write(
        tmp_path / "Status_Kelmarsh_1_2016.csv",
        "Kelmarsh 1",
        ["2016-01-23 14:11:56,-,-,Informational,3835,System OK,,Warnings (27),\n"],
    )

    events = load_events(tmp_path)

    assert events[0].end is None
    assert events[0].duration_seconds is None
    assert events[0].iec_category is None


def test_loads_and_sorts_across_multiple_files_by_start(tmp_path):
    _write(
        tmp_path / "Status_Kelmarsh_1_2016.csv",
        "Kelmarsh 1",
        ["2016-01-02 00:00:00,-,-,Informational,1,later,,,\n"],
    )
    _write(
        tmp_path / "Status_Kelmarsh_2_2016.csv",
        "Kelmarsh 2",
        ["2016-01-01 00:00:00,-,-,Informational,1,earlier,,,\n"],
    )

    events = load_events(tmp_path)

    assert [e.message for e in events] == ["earlier", "later"]
    assert [e.turbine for e in events] == ["Kelmarsh 2", "Kelmarsh 1"]


def test_only_status_csv_files_are_read(tmp_path):
    _write(
        tmp_path / "Status_Kelmarsh_1_2016.csv",
        "Kelmarsh 1",
        ["2016-01-02 00:00:00,-,-,Informational,1,included,,,\n"],
    )
    (tmp_path / "Metmast_Kelmarsh_2016.csv").write_text("not a status file\n", encoding="utf-8")

    events = load_events(tmp_path)

    assert len(events) == 1
    assert events[0].message == "included"


def test_missing_turbine_header_raises_load_error(tmp_path):
    path = tmp_path / "Status_Kelmarsh_1_2016.csv"
    path.write_text(
        "# no turbine line here\n"
        "Timestamp start,Timestamp end,Duration,Status,Code,Message,Comment,"
        "Service contract category,IEC category\n"
        "2016-01-02 00:00:00,-,-,Informational,1,x,,,\n",
        encoding="utf-8",
    )

    try:
        load_events(tmp_path)
        assert False, "expected LoadError"
    except LoadError:
        pass
