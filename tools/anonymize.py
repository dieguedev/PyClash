"""Uso: python tools/anonymize.py ORIGINAL.png DESTINO.png --region X,Y,W,H [--region ...]

El original NO debe estar dentro del repositorio (usa p. ej. captures_raw/, ignorado por git).
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from pyclash.anonymize import anonymize  # noqa: E402


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("src", type=Path)
    p.add_argument("dst", type=Path)
    p.add_argument("--region", action="append", default=[], help="X,Y,W,H a tapar")
    a = p.parse_args()
    regions = [tuple(int(v) for v in r.split(",")) for r in a.region]
    anonymize(a.src, a.dst, regions)
    print(f"Escrito {a.dst}")


if __name__ == "__main__":
    main()
