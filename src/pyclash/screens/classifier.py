"""Clasificador de pantallas: cada fotograma es una del conjunto cerrado o «desconocida».

Una pantalla se reconoce cuando se cumple su firma (indicadores visuales con umbral). Nunca se
fuerza la más parecida: si ninguna firma se cumple, o dos de la misma prioridad se cumplen a la
vez, el resultado es `UNKNOWN`, y de ahí solo se sale por las vías seguras de `escapes.py`.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol, Sequence

import numpy as np

from ..vision.buttons import find_close_buttons, is_blank
from ..vision.color import RGB, find_green_buttons
from ..vision.result import Match
from ..vision.text import TEXT_COLORS, TextFinder, render_text
from .layout import RoiFraction, scaled_font_px


class Screen(str, Enum):
    MAIN_VILLAGE = "main_village"
    BUILDER_BASE = "builder_base"
    PLACING_BUILDING = "placing_building"  # colocando un edificio nuevo (✓ / ✗ sobre el terreno)
    UPGRADE_PANEL = "upgrade_panel"
    SHOP = "shop"
    LAB_MENU = "lab_menu"
    PET_MENU = "pet_menu"
    ATTACK_MENU = "attack_menu"
    ARMY_SELECTION = "army_selection"
    FINDING_OPPONENT = "finding_opponent"  # explorando bases (botón «Next»)
    PLACEMENT = "placement"
    BATTLE = "battle"
    CONFIRM_SURRENDER = "confirm_surrender"  # aviso conocido
    RESULT = "result"
    CHEST_REWARD = "chest_reward"
    LOGIN = "login"  # inicio de sesión de Supercell: el bot se detiene y no interactúa
    BLANK = "blank"  # captura negra o vacía
    UNKNOWN = "unknown"


class Indicator(Protocol):
    name: str
    threshold: float

    def score(self, image: np.ndarray, finder: TextFinder) -> float: ...


@dataclass(frozen=True)
class TextIndicator:
    """Un texto con la fuente del juego en una región. `base_px` es el tamaño a BASE_HEIGHT."""

    text: str
    region: RoiFraction
    base_px: int
    threshold: float = 0.5
    color: str | RGB = "white"

    @property
    def name(self) -> str:
        return f"texto «{self.text}»"

    def best(self, image: np.ndarray, finder: TextFinder) -> Match | None:
        """La mejor coincidencia del texto en la región, sea cual sea su confianza."""
        rgb = TEXT_COLORS[self.color] if isinstance(self.color, str) else self.color
        size = scaled_font_px(image, self.base_px)
        region = self.region.rect(image)
        best = None
        for s in {max(1, size - 1), size, size + 1}:  # el redondeo del tamaño cambia con la ventana
            m = finder.matcher.best(image, render_text(self.text, s, rgb), region, prefilter_color=rgb)
            if m is not None and (best is None or m.confidence > best.confidence):
                best = m
        return best

    def score(self, image: np.ndarray, finder: TextFinder) -> float:
        m = self.best(image, finder)
        return m.confidence if m is not None else 0.0


@dataclass(frozen=True)
class GreenButtonIndicator:
    """Un botón verde de confirmación en la región (confianza: relleno de la caja)."""

    region: RoiFraction
    threshold: float = 0.6
    name: str = "botón verde"

    def score(self, image: np.ndarray, finder: TextFinder) -> float:
        found = find_green_buttons(image, self.region.rect(image))
        return found[0].confidence if found else 0.0


@dataclass(frozen=True)
class CloseButtonIndicator:
    """Un botón de cerrar rojo con «X» en la región."""

    region: RoiFraction
    threshold: float = 0.4
    name: str = "botón de cerrar"

    def score(self, image: np.ndarray, finder: TextFinder) -> float:
        found = find_close_buttons(image, self.region.rect(image))
        return found[0].confidence if found else 0.0


@dataclass(frozen=True)
class ScreenSignature:
    """Indicadores de una pantalla. Se cumple con `min_matches` aciertos (por defecto, todos).

    `priority` resuelve las superposiciones: un diálogo sobre la aldea cumple también la firma de
    la aldea, pero el diálogo tiene mayor prioridad. `provisional` marca firmas sin calibrar
    contra una captura real de la ventana.
    """

    screen: Screen
    indicators: tuple[Indicator, ...]
    min_matches: int | None = None
    priority: int = 0
    provisional: bool = False

    @property
    def needed(self) -> int:
        return len(self.indicators) if self.min_matches is None else self.min_matches


@dataclass(frozen=True)
class SignatureReport:
    screen: Screen
    scores: tuple[tuple[str, float, float], ...]  # (indicador, puntuación, umbral)
    matched: bool
    confidence: float

    def describe(self) -> str:
        parts = [f"{name} {score:.2f}/{limit:.2f}" for name, score, limit in self.scores]
        return f"{self.screen.value}: " + ", ".join(parts)


@dataclass(frozen=True)
class Classification:
    screen: Screen
    confidence: float
    candidates: tuple[tuple[Screen, float], ...] = ()  # firmas que se cumplieron
    reason: str = ""

    @property
    def known(self) -> bool:
        return self.screen not in (Screen.UNKNOWN, Screen.BLANK)


class ScreenClassifier:
    def __init__(self, signatures: Sequence[ScreenSignature] | None = None, finder: TextFinder | None = None) -> None:
        from .signatures import DEFAULT_SIGNATURES

        self.signatures = tuple(DEFAULT_SIGNATURES if signatures is None else signatures)
        self.finder = finder or TextFinder()

    def diagnose(self, image: np.ndarray, only: Sequence[Screen] | None = None) -> dict[Screen, SignatureReport]:
        """Puntuaciones de cada indicador de cada firma (para calibrar y para la prueba canario)."""
        reports: dict[Screen, SignatureReport] = {}
        for sig in self.signatures:
            if only is not None and sig.screen not in only:
                continue
            scores = tuple((i.name, i.score(image, self.finder), i.threshold) for i in sig.indicators)
            hits = sorted((s for _, s, limit in scores if s >= limit), reverse=True)
            matched = len(hits) >= sig.needed
            confidence = hits[sig.needed - 1] if matched and sig.needed else 0.0
            reports[sig.screen] = SignatureReport(sig.screen, scores, matched, confidence)
        return reports

    def classify(self, image: np.ndarray) -> Classification:
        if is_blank(image):
            return Classification(Screen.BLANK, 1.0, reason="captura negra o vacía")
        reports = self.diagnose(image)
        priority = {sig.screen: sig.priority for sig in self.signatures}
        matched = [(priority[s], r) for s, r in reports.items() if r.matched]
        candidates = tuple((r.screen, r.confidence) for _, r in matched)
        if not matched:
            return Classification(Screen.UNKNOWN, 0.0, reason="ninguna firma se cumple")
        top = max(p for p, _ in matched)
        winners = [r for p, r in matched if p == top]
        if len(winners) > 1:
            names = ", ".join(r.screen.value for r in winners)
            return Classification(Screen.UNKNOWN, 0.0, candidates, f"firmas ambiguas: {names}")
        return Classification(winners[0].screen, winners[0].confidence, candidates)

    def explain(self, image: np.ndarray, expected: Screen) -> str | None:
        """None si `image` se clasifica como `expected`; si no, un mensaje legible de qué falló.

        Es la base de la prueba canario: cuando el juego cambia su interfaz, dice qué pantalla
        dejó de reconocerse y qué indicador concreto bajó del umbral.
        """
        got = self.classify(image)
        if got.screen is expected:
            return None
        report = self.diagnose(image, only=[expected]).get(expected)
        detail = report.describe() if report else f"{expected.value}: sin firma definida"
        return f"«{expected.value}» no se reconoce (se obtuvo «{got.screen.value}»: {got.reason or 'otra firma'}). {detail}"
