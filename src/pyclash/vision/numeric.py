"""Interpretación de números leídos. Solo para campos numéricos: nunca se aplica a nombres."""

from __future__ import annotations

import re

# Letras que el OCR confunde con dígitos. Es una tabla corta y deliberada.
_LOOKALIKES = str.maketrans({"O": "0", "o": "0", "I": "1", "l": "1", "|": "1", "Z": "2", "S": "5", "B": "8"})
_SEPARATORS = re.compile(r"[\s,.']")
_DURATION = re.compile(r"(\d+)\s*([dhms])")
_UNITS = {"d": 86400, "h": 3600, "m": 60, "s": 1}


def parse_numeric_field(text: str) -> int | None:
    """«2O0O» -> 2000. None si tras corregir quedan caracteres que no son dígitos.

    Exige al menos un dígito real, así una palabra como «SOS» no se convierte en 505.
    """
    cleaned = _SEPARATORS.sub("", text)
    if not any(c.isdigit() for c in cleaned):
        return None
    fixed = cleaned.translate(_LOOKALIKES)
    return int(fixed) if fixed.isdigit() else None


def parse_duration(text: str) -> int | None:
    """«1d 4h 20m» -> segundos. Sin corrección de letras: las unidades son letras de verdad."""
    parts = _DURATION.findall(text.lower())
    if not parts or _DURATION.sub("", text.lower()).strip():
        return None
    return sum(int(n) * _UNITS[u] for n, u in parts)
