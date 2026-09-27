"""Gets the Kelmarsh 2016 Status CSVs, and the six 10-minute Turbine_Data CSVs, into
data/raw/.

Status CSVs: prefers a local copy over downloading: files already in `raw_dir`,
then a local mirror directory (see `_resolve_local_dirs`) that already holds the
extracted CSVs, then a local zip copy in that directory, and only then Zenodo
record 16807551 (`Kelmarsh_SCADA_2016_3082.zip`). Only the `Status_*.csv` members
of the zip are kept; the rest of the archive (met mast and power curve files) is
discarded.

Turbine_Data CSVs (about 545 MB for all six, git-ignored): populated on a
best-effort basis from whatever the Status fetch's own local sources happen to
also carry, since the same Zenodo zip holds both file sets. Unlike the Status
CSVs, this step never raises and never triggers its own download; a source that
offers only Status CSVs (common in tests, and for a partial local mirror) is left
alone, and a real run picks the Turbine_Data files up from the same zip once it
has been downloaded for the Status CSVs.

Once at least one Turbine_Data CSV is present in `raw_dir`, this also (re)builds
`data/raw/kelmarsh.duckdb`'s `measurements` table from whatever Turbine_Data CSVs
are there (see measurements.py), skipping the rebuild if the database already has
all six turbines. Unlike the CSV steps, this one is not best-effort: once a CSV is
confirmed present, loading it is expected to succeed, and a malformed file raises
measurements.LoadError rather than being silently skipped.
"""

from __future__ import annotations

import json
import os
import shutil
import urllib.request
import zipfile
from pathlib import Path

from . import measurements

TURBINE_COUNT = 6
ZENODO_RECORD_ID = "16807551"
ZENODO_FILENAME = "Kelmarsh_SCADA_2016_3082.zip"
ZENODO_API_URL = f"https://zenodo.org/api/records/{ZENODO_RECORD_ID}"
LOCAL_DIR_ENV_VAR = "KELMARSH_LOCAL_DIR"


class FetchError(RuntimeError):
    """No source (local or Zenodo) produced the expected Status CSVs."""


def fetch_kelmarsh(raw_dir: Path, local_dirs: list[Path] | None = None) -> list[Path]:
    """Populate `raw_dir` with the Kelmarsh 2016 Status CSVs and return their paths;
    also populate it with the six Turbine_Data CSVs on a best-effort basis (see
    module docstring). The return value is the Status CSVs only, unchanged from
    before this module also fetched Turbine_Data.

    Idempotent: if `raw_dir` already holds all six Status files, nothing else is
    touched for them (Turbine_Data is still checked separately). `local_dirs`
    defaults to the directory named by the `KELMARSH_LOCAL_DIR` environment
    variable, if set, else no local directory is tried and this downloads from
    Zenodo.
    """
    raw_dir = Path(raw_dir)
    raw_dir.mkdir(parents=True, exist_ok=True)
    sources = _resolve_local_dirs() if local_dirs is None else local_dirs

    status_paths = _fetch_status_csvs(raw_dir, sources)
    turbine_data_paths = _ensure_turbine_data_csvs(raw_dir, sources)
    _ensure_duckdb(raw_dir, turbine_data_paths)
    return status_paths


def _fetch_status_csvs(raw_dir: Path, sources: list[Path]) -> list[Path]:
    existing = _status_csvs(raw_dir)
    if len(existing) >= TURBINE_COUNT:
        return existing

    for source in sources:
        found = _status_csvs(source)
        if found and len(found) < TURBINE_COUNT:
            raise FetchError(
                f"{source} has {len(found)} Status CSV(s), expected {TURBINE_COUNT}. "
                "Point --from (or KELMARSH_LOCAL_DIR) at a complete local copy, or "
                "remove the incomplete one so this falls through to downloading."
            )
        if found:
            return _copy_csvs(found, raw_dir)

    for source in sources:
        zips = sorted(source.glob("*.zip")) if source.exists() else []
        if zips:
            return _extract_status_csvs(zips[0], raw_dir)

    zip_path = raw_dir / ZENODO_FILENAME
    _download(zip_path)
    return _extract_status_csvs(zip_path, raw_dir)


def _ensure_turbine_data_csvs(raw_dir: Path, sources: list[Path]) -> list[Path]:
    """Best-effort counterpart to `_fetch_status_csvs` for the six Turbine_Data
    CSVs: copies them from a local source directory that already has all six, else
    extracts them from a local zip, else from a zip already downloaded into
    `raw_dir` for the Status CSVs. Never downloads on its own and never raises; a
    source offering only Status CSVs, or an incomplete set of Turbine_Data CSVs
    with no zip to fall back on, is left alone rather than copying a partial set."""
    existing = _turbine_data_csvs(raw_dir)
    if len(existing) >= TURBINE_COUNT:
        return existing

    for source in sources:
        found = _turbine_data_csvs(source)
        if len(found) >= TURBINE_COUNT:
            return _copy_csvs(found, raw_dir)

    for source in sources:
        zips = sorted(source.glob("*.zip")) if source.exists() else []
        if zips:
            extracted = _extract_turbine_data_csvs(zips[0], raw_dir)
            if extracted:
                return extracted

    downloaded_zip = raw_dir / ZENODO_FILENAME
    if downloaded_zip.exists():
        return _extract_turbine_data_csvs(downloaded_zip, raw_dir)

    return existing


def _ensure_duckdb(raw_dir: Path, turbine_data_paths: list[Path]) -> None:
    """(Re)build data/raw/kelmarsh.duckdb's `measurements` table from
    `turbine_data_paths`, unless it already has all six turbines. Does nothing if
    `turbine_data_paths` is empty (the best-effort Turbine_Data step above found
    nothing to load from)."""
    if not turbine_data_paths:
        return
    con = measurements.connect(raw_dir / measurements.DB_FILENAME)
    try:
        if not measurements.is_populated(con):
            measurements.build_database(con, turbine_data_paths)
    finally:
        con.close()


def _resolve_local_dirs() -> list[Path]:
    value = os.environ.get(LOCAL_DIR_ENV_VAR)
    return [Path(value)] if value else []


def _status_csvs(folder: Path) -> list[Path]:
    return sorted(folder.glob("Status_*.csv")) if folder.exists() else []


def _turbine_data_csvs(folder: Path) -> list[Path]:
    return sorted(folder.glob("Turbine_Data_Kelmarsh_*.csv")) if folder.exists() else []


def _copy_csvs(paths: list[Path], raw_dir: Path) -> list[Path]:
    copied = []
    for path in paths:
        dest = raw_dir / path.name
        shutil.copyfile(path, dest)
        copied.append(dest)
    return sorted(copied)


def _extract_status_csvs(zip_path: Path, raw_dir: Path) -> list[Path]:
    extracted = []
    with zipfile.ZipFile(zip_path) as zf:
        for member in zf.namelist():
            name = Path(member).name
            if name.startswith("Status_") and name.endswith(".csv"):
                dest = raw_dir / name
                with zf.open(member) as src, open(dest, "wb") as out:
                    shutil.copyfileobj(src, out)
                extracted.append(dest)
    if not extracted:
        raise FetchError(f"no Status_*.csv files found in {zip_path}")
    return sorted(extracted)


def _extract_turbine_data_csvs(zip_path: Path, raw_dir: Path) -> list[Path]:
    extracted = []
    with zipfile.ZipFile(zip_path) as zf:
        for member in zf.namelist():
            name = Path(member).name
            if name.startswith("Turbine_Data_Kelmarsh_") and name.endswith(".csv"):
                dest = raw_dir / name
                with zf.open(member) as src, open(dest, "wb") as out:
                    shutil.copyfileobj(src, out)
                extracted.append(dest)
    return sorted(extracted)


def _zenodo_file_url() -> str:
    with urllib.request.urlopen(ZENODO_API_URL, timeout=30) as resp:
        record = json.loads(resp.read())
    for entry in record.get("files", []):
        if entry.get("key") == ZENODO_FILENAME:
            return entry["links"]["self"]
    raise FetchError(f"{ZENODO_FILENAME} not found in Zenodo record {ZENODO_RECORD_ID}")


def _download(dest: Path) -> None:
    url = _zenodo_file_url()
    with urllib.request.urlopen(url, timeout=180) as resp, open(dest, "wb") as out:
        shutil.copyfileobj(resp, out)
