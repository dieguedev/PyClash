"""Vocabulario del juego empaquetado, con resolución difusa y validación de listas del usuario.

La instantánea va dentro del paquete (`data/vocabulary.json`): el arranque nunca usa la red.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from importlib import resources
from typing import Iterable, Mapping, Sequence

from rapidfuzz import fuzz, process


def normalize(text: str) -> str:
    """Minúsculas, sin acentos ni puntuación («P.E.K.K.A» -> «pekka», «Builder's Hut» -> «builders hut»)."""
    folded = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", re.sub(r"[.'’]", "", folded.casefold())).strip()


@dataclass(frozen=True)
class Resolution:
    canonical: str | None
    score: float
    suggestions: tuple[str, ...] = ()


@dataclass(frozen=True)
class Validation:
    name: str
    valid: bool
    canonical: str | None = None
    suggestions: tuple[str, ...] = ()


class Vocabulary:
    def __init__(self, categories: Mapping[str, Sequence[str]]) -> None:
        self._categories = {c: tuple(names) for c, names in categories.items()}

    @classmethod
    def load_packaged(cls) -> "Vocabulary":
        raw = (resources.files("pyclash") / "data" / "vocabulary.json").read_text(encoding="utf-8")
        return cls(json.loads(raw)["categories"])

    @property
    def categories(self) -> tuple[str, ...]:
        return tuple(self._categories)

    def names(self, categories: Iterable[str] | None = None) -> list[str]:
        chosen = self._categories.keys() if categories is None else categories
        out: list[str] = []
        for c in chosen:
            if c not in self._categories:
                raise KeyError(f"categoría desconocida: {c}")
            out += self._categories[c]
        return out

    def _ranked(self, text: str, categories: Iterable[str] | None, limit: int, cutoff: float):
        names = self.names(categories)
        index = {normalize(n): n for n in names}
        return [
            (index[key], score)
            for key, score, _ in process.extract(
                normalize(text), list(index), scorer=fuzz.ratio, limit=limit, score_cutoff=cutoff
            )
        ]

    def resolve(
        self,
        text: str,
        categories: Iterable[str] | None = None,
        cutoff: float = 85.0,
        suggest_cutoff: float = 70.0,
        ambiguity_margin: float = 3.0,
    ) -> Resolution:
        """Nombre canónico de un texto leído por OCR.

        Un parecido >= `cutoff` se acepta; si el segundo candidato queda a menos de
        `ambiguity_margin` puntos se considera ambiguo y no se elige ninguno.
        """
        ranked = self._ranked(text, categories, limit=3, cutoff=suggest_cutoff)
        if not ranked:
            return Resolution(None, 0.0)
        (best, score), rest = ranked[0], ranked[1:]
        suggestions = tuple(n for n, _ in ranked)
        if score >= cutoff and not (rest and score - rest[0][1] < ambiguity_margin):
            return Resolution(best, score, suggestions)
        return Resolution(None, score, suggestions)

    def validate(
        self, names: Iterable[str], categories: Iterable[str] | None = None, suggest_cutoff: float = 80.0
    ) -> list[Validation]:
        """Valida una lista escrita por el usuario: solo se aceptan nombres exactos (sin distinguir
        mayúsculas ni puntuación); una errata se rechaza con sugerencias, un nombre inexistente sin ellas."""
        valid_names = {normalize(n): n for n in self.names(categories)}
        out = []
        for name in names:
            canonical = valid_names.get(normalize(name))
            if canonical is not None:
                out.append(Validation(name, True, canonical))
                continue
            close = self._ranked(name, categories, limit=3, cutoff=suggest_cutoff)
            out.append(Validation(name, False, None, tuple(n for n, _ in close)))
        return out
