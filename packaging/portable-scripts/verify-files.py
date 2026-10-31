from __future__ import annotations

import hashlib
import sys
from pathlib import Path


def main() -> int:
    root = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent
    manifest = root / "checksums.sha256"
    if not manifest.is_file():
        print(f"missing manifest: {manifest}", file=sys.stderr)
        return 1
    failed = 0
    checked = 0
    for line in manifest.read_text(encoding="ascii").splitlines():
        line = line.strip()
        if not line:
            continue
        expected, relative = line.split("  ", 1)
        path = root / relative
        if not path.is_file():
            print(f"MISSING {relative}")
            failed += 1
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        checked += 1
        if digest != expected.lower():
            print(f"MISMATCH {relative}")
            failed += 1
    if failed:
        print(f"{failed} file(s) failed; checked {checked}")
        return 1
    print(f"All {checked} files verified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
