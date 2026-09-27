#!/usr/bin/env python3
"""Populate data/raw/ with the Kelmarsh 2016 Status CSVs.

Copies from a local source when one is available (see jev_turbine.fetch), otherwise
downloads Kelmarsh_SCADA_2016_3082.zip from Zenodo record 16807551. Run this once;
fetch_kelmarsh() is a no-op if data/raw/ is already populated.
"""

from pathlib import Path

from jev_turbine.fetch import fetch_kelmarsh

if __name__ == "__main__":
    root = Path(__file__).resolve().parent.parent
    paths = fetch_kelmarsh(root / "data" / "raw")
    print(f"data/raw/ has {len(paths)} Status CSVs:")
    for path in paths:
        print(f"  {path.name}")
