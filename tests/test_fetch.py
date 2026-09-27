import json
import zipfile
from io import BytesIO
from pathlib import Path

import pytest

from jev_turbine import fetch as fetch_module
from jev_turbine.fetch import fetch_kelmarsh

STATUS_NAMES = [f"Status_Kelmarsh_{n}_2016.csv" for n in range(1, 7)]


def _make_source_csvs(dir_path: Path) -> None:
    dir_path.mkdir(parents=True, exist_ok=True)
    for name in STATUS_NAMES:
        (dir_path / name).write_text(f"# Turbine: {name}\ndata\n", encoding="utf-8")


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
