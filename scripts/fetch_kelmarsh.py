#!/usr/bin/env python3
"""Populate data/raw/ with the Kelmarsh 2016 Status CSVs.

Copies from a local source when one is available: pass --from DIR, or set the
KELMARSH_LOCAL_DIR environment variable, pointing at a directory that already holds
the six Status CSVs or a Kelmarsh_SCADA_2016_3082.zip. With neither set, this
downloads the zip from Zenodo record 16807551. Run this once; fetch_kelmarsh() is a
no-op if data/raw/ is already populated.
"""

import argparse
from pathlib import Path

from jev_turbine.fetch import fetch_kelmarsh


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--from",
        dest="from_dir",
        metavar="DIR",
        help="a local directory that already holds the Status CSVs, or a Kelmarsh zip",
    )
    args = parser.parse_args()

    root = Path(__file__).resolve().parent.parent
    local_dirs = [Path(args.from_dir)] if args.from_dir else None
    paths = fetch_kelmarsh(root / "data" / "raw", local_dirs=local_dirs)
    print(f"data/raw/ has {len(paths)} Status CSVs:")
    for path in paths:
        print(f"  {path.name}")


if __name__ == "__main__":
    main()
