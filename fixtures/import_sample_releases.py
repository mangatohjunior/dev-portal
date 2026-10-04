"""Load fixtures/sample-releases.json into the release ledger.

Re-running replaces only the sample pipeline ids (900001 and up) and
recomputes their timestamps, so the chart stays current.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from releases_store import import_sample_releases

FIXTURE = Path(__file__).resolve().parent / "sample-releases.json"


def main() -> None:
    db_path = sys.argv[1] if len(sys.argv) > 1 else None
    count = import_sample_releases(str(FIXTURE), db_path)
    print(f"Imported {count} sample releases.")


if __name__ == "__main__":
    main()
