"""Gets the Kelmarsh 2016 Status CSVs into data/raw/.

Prefers a local copy over downloading: files already in `raw_dir`, then a local
mirror directory (see `_resolve_local_dirs`) that already holds the extracted CSVs,
then a local zip copy in that directory, and only then Zenodo record 16807551
(`Kelmarsh_SCADA_2016_3082.zip`). Only the `Status_*.csv` members of the zip are
kept; the rest of the archive (turbine, met mast and power curve files) is
discarded.
"""

from __future__ import annotations

import json
import os
import shutil
import urllib.request
import zipfile
from pathlib import Path

TURBINE_COUNT = 6
ZENODO_RECORD_ID = "16807551"
ZENODO_FILENAME = "Kelmarsh_SCADA_2016_3082.zip"
ZENODO_API_URL = f"https://zenodo.org/api/records/{ZENODO_RECORD_ID}"
LOCAL_DIR_ENV_VAR = "KELMARSH_LOCAL_DIR"


class FetchError(RuntimeError):
    """No source (local or Zenodo) produced the expected Status CSVs."""


def fetch_kelmarsh(raw_dir: Path, local_dirs: list[Path] | None = None) -> list[Path]:
    """Populate `raw_dir` with the Kelmarsh 2016 Status CSVs and return their paths.

    Idempotent: if `raw_dir` already holds all six files, nothing else is touched.
    `local_dirs` defaults to the directory named by the `KELMARSH_LOCAL_DIR`
    environment variable, if set, else no local directory is tried and this
    downloads from Zenodo.
    """
    raw_dir = Path(raw_dir)
    raw_dir.mkdir(parents=True, exist_ok=True)
    sources = _resolve_local_dirs() if local_dirs is None else local_dirs

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


def _resolve_local_dirs() -> list[Path]:
    value = os.environ.get(LOCAL_DIR_ENV_VAR)
    return [Path(value)] if value else []


def _status_csvs(folder: Path) -> list[Path]:
    return sorted(folder.glob("Status_*.csv")) if folder.exists() else []


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
