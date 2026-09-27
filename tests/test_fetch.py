import json
import zipfile
from io import BytesIO
from pathlib import Path

import pytest

from jev_turbine import fetch as fetch_module
from jev_turbine import measurements
from jev_turbine.fetch import fetch_kelmarsh

STATUS_NAMES = [f"Status_Kelmarsh_{n}_2016.csv" for n in range(1, 7)]
TURBINE_DATA_NAMES = [f"Turbine_Data_Kelmarsh_{n}_2016.csv" for n in range(1, 7)]


def _make_source_csvs(dir_path: Path) -> None:
    dir_path.mkdir(parents=True, exist_ok=True)
    for name in STATUS_NAMES:
        (dir_path / name).write_text(f"# Turbine: {name}\ndata\n", encoding="utf-8")


def _turbine_data_csv_text(turbine_number: int) -> str:
    """A minimal but real-shaped Turbine_Data CSV: a proper '# Turbine:' line and
    '# Date and time,...' header (so jev_turbine.measurements can actually load
    it into DuckDB), one known row and one all-NaN row."""
    return (
        f"# Turbine: Kelmarsh {turbine_number}\n"
        "# Time zone: UTC\n"
        "#\n"
        "# Date and time,Power (kW),Wind speed (m/s),Rotor speed (RPM),Grid frequency (Hz),Grid voltage (V)\n"
        "2016-01-03 00:00:00,100.0,5.0,10.0,50.0,690.0\n"
        "2016-01-03 00:10:00,NaN,NaN,NaN,NaN,NaN\n"
    )


def _make_source_turbine_data_csvs(dir_path: Path) -> None:
    dir_path.mkdir(parents=True, exist_ok=True)
    for n, name in zip(range(1, 7), TURBINE_DATA_NAMES):
        (dir_path / name).write_text(_turbine_data_csv_text(n), encoding="utf-8")


def test_copies_from_local_directory_when_csvs_are_present(tmp_path):
    source = tmp_path / "source"
    _make_source_csvs(source)
    raw = tmp_path / "raw"

    result = fetch_kelmarsh(raw, local_dirs=[source])

    assert sorted(p.name for p in result) == sorted(STATUS_NAMES)
    for name in STATUS_NAMES:
        assert (raw / name).exists()
        assert (raw / name).read_text(encoding="utf-8") == (source / name).read_text(encoding="utf-8")


def test_is_a_noop_and_never_touches_the_network_when_raw_dir_is_already_populated(tmp_path, monkeypatch):
    raw = tmp_path / "raw"
    _make_source_csvs(raw)

    def boom(*args, **kwargs):
        raise AssertionError("should not touch the network when raw_dir is already populated")

    monkeypatch.setattr(fetch_module.urllib.request, "urlopen", boom)

    result = fetch_kelmarsh(raw, local_dirs=[tmp_path / "does-not-exist"])

    assert len(result) == 6


def test_extracts_only_status_csvs_from_a_local_zip(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    zip_path = source / fetch_module.ZENODO_FILENAME
    with zipfile.ZipFile(zip_path, "w") as zf:
        for name in STATUS_NAMES:
            zf.writestr(name, f"# Turbine: {name}\ndata\n")
        zf.writestr("Turbine_Kelmarsh_1_2016.csv", "not a status file\n")
        zf.writestr("Metmast_Kelmarsh_2016.csv", "not a status file either\n")

    raw = tmp_path / "raw"
    result = fetch_kelmarsh(raw, local_dirs=[source])

    assert sorted(p.name for p in result) == sorted(STATUS_NAMES)
    assert not (raw / "Turbine_Kelmarsh_1_2016.csv").exists()
    assert not (raw / "Metmast_Kelmarsh_2016.csv").exists()


def test_falls_through_local_dirs_in_order(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    source = tmp_path / "source"
    _make_source_csvs(source)
    raw = tmp_path / "raw"

    result = fetch_kelmarsh(raw, local_dirs=[empty, source])

    assert len(result) == 6


def test_zenodo_file_url_resolves_the_named_file_from_the_api(monkeypatch):
    payload = json.dumps(
        {
            "files": [
                {"key": "Other.zip", "links": {"self": "https://zenodo.org/x/other"}},
                {"key": fetch_module.ZENODO_FILENAME, "links": {"self": "https://zenodo.org/x/real"}},
            ]
        }
    ).encode()

    class FakeResponse:
        def __enter__(self):
            return BytesIO(payload)

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(fetch_module.urllib.request, "urlopen", lambda *a, **kw: FakeResponse())

    assert fetch_module._zenodo_file_url() == "https://zenodo.org/x/real"


def test_zenodo_file_url_raises_when_the_file_is_missing_from_the_record(monkeypatch):
    payload = json.dumps({"files": [{"key": "Other.zip", "links": {"self": "https://zenodo.org/x/other"}}]}).encode()

    class FakeResponse:
        def __enter__(self):
            return BytesIO(payload)

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(fetch_module.urllib.request, "urlopen", lambda *a, **kw: FakeResponse())

    with pytest.raises(fetch_module.FetchError):
        fetch_module._zenodo_file_url()


def test_fetch_reaches_the_download_path_without_a_local_source_but_does_not_download_in_tests(
    tmp_path, monkeypatch
):
    def no_network(*args, **kwargs):
        raise OSError("tests must not perform a real download")

    monkeypatch.setattr(fetch_module.urllib.request, "urlopen", no_network)

    with pytest.raises(OSError):
        fetch_kelmarsh(tmp_path / "raw", local_dirs=[tmp_path / "does-not-exist"])


def test_uses_kelmarsh_local_dir_env_var_when_local_dirs_is_not_given(tmp_path, monkeypatch):
    source = tmp_path / "source"
    _make_source_csvs(source)
    monkeypatch.setenv(fetch_module.LOCAL_DIR_ENV_VAR, str(source))

    result = fetch_kelmarsh(tmp_path / "raw")

    assert len(result) == 6


def test_no_env_var_and_no_local_dirs_reaches_the_download_path(tmp_path, monkeypatch):
    monkeypatch.delenv(fetch_module.LOCAL_DIR_ENV_VAR, raising=False)

    def no_network(*args, **kwargs):
        raise OSError("tests must not perform a real download")

    monkeypatch.setattr(fetch_module.urllib.request, "urlopen", no_network)

    with pytest.raises(OSError):
        fetch_kelmarsh(tmp_path / "raw")


def test_incomplete_local_mirror_raises_a_clear_error(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    for name in STATUS_NAMES[:3]:
        (source / name).write_text("data\n", encoding="utf-8")

    with pytest.raises(fetch_module.FetchError, match="3 Status CSV"):
        fetch_kelmarsh(tmp_path / "raw", local_dirs=[source])


# --- Turbine_Data (10-minute measurements) -----------------------------------------


def test_turbine_data_csvs_are_copied_from_a_local_directory_alongside_status(tmp_path):
    source = tmp_path / "source"
    _make_source_csvs(source)
    _make_source_turbine_data_csvs(source)
    raw = tmp_path / "raw"

    fetch_kelmarsh(raw, local_dirs=[source])

    for name in TURBINE_DATA_NAMES:
        assert (raw / name).exists()
        assert (raw / name).read_text(encoding="utf-8") == (source / name).read_text(encoding="utf-8")


def test_turbine_data_is_left_alone_when_the_source_only_has_status_csvs(tmp_path):
    source = tmp_path / "source"
    _make_source_csvs(source)
    raw = tmp_path / "raw"

    fetch_kelmarsh(raw, local_dirs=[source])

    assert list(raw.glob("Turbine_Data_Kelmarsh_*.csv")) == []


def test_incomplete_turbine_data_in_a_local_directory_is_not_copied(tmp_path):
    source = tmp_path / "source"
    _make_source_csvs(source)
    source.mkdir(exist_ok=True)
    for name in TURBINE_DATA_NAMES[:2]:
        (source / name).write_text("data\n", encoding="utf-8")
    raw = tmp_path / "raw"

    fetch_kelmarsh(raw, local_dirs=[source])

    # Falls through rather than copying a partial set (no zip to complete it from).
    assert list(raw.glob("Turbine_Data_Kelmarsh_*.csv")) == []


def test_turbine_data_csvs_are_extracted_from_a_local_zip_alongside_status(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    zip_path = source / fetch_module.ZENODO_FILENAME
    with zipfile.ZipFile(zip_path, "w") as zf:
        for name in STATUS_NAMES:
            zf.writestr(name, f"# Turbine: {name}\ndata\n")
        for n, name in zip(range(1, 7), TURBINE_DATA_NAMES):
            zf.writestr(name, _turbine_data_csv_text(n))
        zf.writestr("Metmast_Kelmarsh_2016.csv", "not a status or turbine data file\n")

    raw = tmp_path / "raw"
    fetch_kelmarsh(raw, local_dirs=[source])

    for n, name in zip(range(1, 7), TURBINE_DATA_NAMES):
        assert (raw / name).read_text(encoding="utf-8") == _turbine_data_csv_text(n)
    assert not (raw / "Metmast_Kelmarsh_2016.csv").exists()


def test_turbine_data_is_extracted_from_the_zip_already_downloaded_for_status(tmp_path, monkeypatch):
    raw = tmp_path / "raw"
    raw.mkdir()
    zip_path = raw / fetch_module.ZENODO_FILENAME
    with zipfile.ZipFile(zip_path, "w") as zf:
        for name in STATUS_NAMES:
            zf.writestr(name, f"# Turbine: {name}\ndata\n")
        for n, name in zip(range(1, 7), TURBINE_DATA_NAMES):
            zf.writestr(name, _turbine_data_csv_text(n))

    def boom(*args, **kwargs):
        raise AssertionError("should not touch the network: a zip is already in raw_dir")

    monkeypatch.setattr(fetch_module.urllib.request, "urlopen", boom)

    # Simulate a prior run that already extracted Status but left the zip behind, and
    # left Turbine_Data unfetched (as an older version of this module would have).
    fetch_module._extract_status_csvs(zip_path, raw)

    fetch_kelmarsh(raw, local_dirs=[tmp_path / "does-not-exist"])

    for name in TURBINE_DATA_NAMES:
        assert (raw / name).exists()


def test_turbine_data_fetch_is_a_noop_when_raw_dir_already_has_all_six(tmp_path, monkeypatch):
    raw = tmp_path / "raw"
    _make_source_csvs(raw)
    _make_source_turbine_data_csvs(raw)

    def boom(*args, **kwargs):
        raise AssertionError("should not touch the network when raw_dir is already populated")

    monkeypatch.setattr(fetch_module.urllib.request, "urlopen", boom)

    fetch_kelmarsh(raw, local_dirs=[tmp_path / "does-not-exist"])

    assert len(list(raw.glob("Turbine_Data_Kelmarsh_*.csv"))) == 6


# --- DuckDB build -------------------------------------------------------------------


def test_fetch_builds_the_duckdb_database_from_the_turbine_data_csvs(tmp_path):
    source = tmp_path / "source"
    _make_source_csvs(source)
    _make_source_turbine_data_csvs(source)
    raw = tmp_path / "raw"

    fetch_kelmarsh(raw, local_dirs=[source])

    db_path = raw / measurements.DB_FILENAME
    assert db_path.exists()
    con = measurements.connect(db_path)
    try:
        assert measurements.is_populated(con)
        [count] = con.execute("SELECT COUNT(*) FROM measurements").fetchone()
        assert count == 12  # 6 turbines x 2 rows each, from _turbine_data_csv_text
    finally:
        con.close()


def test_fetch_skips_rebuilding_the_duckdb_database_when_already_populated(tmp_path):
    source = tmp_path / "source"
    _make_source_csvs(source)
    _make_source_turbine_data_csvs(source)
    raw = tmp_path / "raw"
    fetch_kelmarsh(raw, local_dirs=[source])

    # Tamper with the database directly, without removing turbine 1 entirely (so
    # is_populated() still reports all six turbines present): a real rebuild
    # would restore the deleted row.
    con = measurements.connect(raw / measurements.DB_FILENAME)
    con.execute("DELETE FROM measurements WHERE turbine = 1 AND wind_ms = 5.0")
    con.close()

    fetch_kelmarsh(raw, local_dirs=[source])

    con = measurements.connect(raw / measurements.DB_FILENAME)
    try:
        assert measurements.is_populated(con)
        [count] = con.execute("SELECT COUNT(*) FROM measurements").fetchone()
        assert count == 11  # not rebuilt: still missing the row deleted above
    finally:
        con.close()


def test_fetch_does_not_build_a_database_when_no_turbine_data_is_available(tmp_path):
    source = tmp_path / "source"
    _make_source_csvs(source)
    raw = tmp_path / "raw"

    fetch_kelmarsh(raw, local_dirs=[source])

    assert not (raw / measurements.DB_FILENAME).exists()
