"""Falla si algún PNG/imagen versionada no lleva el marcador de anonimizado."""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from pyclash.anonymize import is_anonymized  # noqa: E402

EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".webp", ".gif"}


def tracked_or_present() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=ROOT, capture_output=True, text=True, check=True,
    ).stdout.splitlines()
    return [ROOT / f for f in out if Path(f).suffix.lower() in EXTS and (ROOT / f).exists()]


def main() -> int:
    bad = [p for p in tracked_or_present() if not is_anonymized(p)]
    for p in bad:
        print(f"ERROR: {p.relative_to(ROOT)} no está anonimizada (falta el marcador de anonimizado)")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
