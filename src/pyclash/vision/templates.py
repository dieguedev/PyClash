"""Buscador de plantillas: por región, con umbral, todas las coincidencias y búsqueda en lote."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Mapping

import cv2
import numpy as np

from ..device.base import Rect
from .color import RGB, isolate_color
from .preprocess import to_gray
from .result import Match, Provenance, crop

# Tope de candidatos que entran en la supresión de solapes (evita miles de picos de ruido).
_MAX_CANDIDATES = 2000


def score_map(image: np.ndarray, template: np.ndarray) -> np.ndarray | None:
    """Correlación normalizada de `template` sobre `image` (None si la plantilla no cabe)."""
    gray, tgray = to_gray(image), to_gray(template)
    if tgray.shape[0] > gray.shape[0] or tgray.shape[1] > gray.shape[1]:
        return None
    res = cv2.matchTemplate(gray, tgray, cv2.TM_CCOEFF_NORMED)
    # Una plantilla o una zona sin variación da NaN/inf: no es una coincidencia.
    return np.clip(np.nan_to_num(res, nan=0.0, posinf=0.0, neginf=0.0), -1.0, 1.0)


class TemplateMatcher:
    """Sin estado global: el umbral por defecto y el tamaño del pool se pasan al construirlo."""

    def __init__(self, threshold: float = 0.8, max_workers: int = 4) -> None:
        self.threshold = threshold
        self.max_workers = max_workers

    def find(
        self,
        image: np.ndarray,
        template: np.ndarray,
        region: Rect | None = None,
        threshold: float | None = None,
        prefilter_color: RGB | None = None,
        prefilter_tolerance: float = 30.0,
        label: str | None = None,
        method: str = "template",
    ) -> Match | None:
        """La mejor coincidencia por encima del umbral, o None (nunca una espuria por debajo)."""
        best = self.best(image, template, region, prefilter_color, prefilter_tolerance, label, method)
        limit = self.threshold if threshold is None else threshold
        return best if best is not None and best.confidence >= limit else None

    def best(
        self,
        image: np.ndarray,
        template: np.ndarray,
        region: Rect | None = None,
        prefilter_color: RGB | None = None,
        prefilter_tolerance: float = 30.0,
        label: str | None = None,
        method: str = "template",
    ) -> Match | None:
        """La mejor coincidencia sea cual sea su confianza (para calibrar); None si no cabe."""
        section, eff = crop(image, region)
        if section.size == 0:
            return None
        if prefilter_color is not None:
            section = isolate_color(section, prefilter_color, prefilter_tolerance)
        res = score_map(section, template)
        if res is None:
            return None
        _, score, _, (x, y) = cv2.minMaxLoc(res)
        th, tw = template.shape[:2]
        return Match(Rect(eff.x + x, eff.y + y, tw, th), float(score), Provenance(eff, method), label)

    def find_all(
        self,
        image: np.ndarray,
        template: np.ndarray,
        region: Rect | None = None,
        threshold: float | None = None,
        prefilter_color: RGB | None = None,
        prefilter_tolerance: float = 30.0,
        max_overlap: float = 0.5,
        label: str | None = None,
        method: str = "template",
    ) -> list[Match]:
        """Todas las coincidencias >= umbral, de mayor a menor confianza, sin solapes.

        Una candidata se descarta si cubre más de `max_overlap` del área de una ya aceptada.
        """
        section, eff = crop(image, region)
        if section.size == 0:
            return []
        if prefilter_color is not None:
            section = isolate_color(section, prefilter_color, prefilter_tolerance)
        res = score_map(section, template)
        if res is None:
            return []
        limit = self.threshold if threshold is None else threshold
        ys, xs = np.where(res >= limit)
        order = np.argsort(-res[ys, xs])[:_MAX_CANDIDATES]
        th, tw = template.shape[:2]
        prov = Provenance(eff, method)
        accepted: list[Match] = []
        for i in order:
            x, y = int(xs[i]), int(ys[i])
            if any(_overlap(x, y, tw, th, a.rect.x - eff.x, a.rect.y - eff.y) > max_overlap for a in accepted):
                continue
            accepted.append(Match(Rect(eff.x + x, eff.y + y, tw, th), float(res[y, x]), prov, label))
        return accepted

    def find_batch(
        self,
        image: np.ndarray,
        templates: Mapping[str, np.ndarray],
        region: Rect | None = None,
        threshold: float | None = None,
        check_stop: Callable[[], None] | None = None,
    ) -> dict[str, Match | None]:
        """Busca varias plantillas con un pool acotado.

        `check_stop` (p. ej. `ctx.check_stop`) se llama antes de lanzar cada tarea y mientras se
        recogen resultados; si lanza, se cancelan las pendientes y se propaga la excepción.
        """
        results: dict[str, Match | None] = {}
        pool = ThreadPoolExecutor(max_workers=self.max_workers)
        try:
            futures = {}
            for name, template in templates.items():
                if check_stop is not None:
                    check_stop()
                futures[name] = pool.submit(self.find, image, template, region, threshold, label=name)
            for name, future in futures.items():
                if check_stop is not None:
                    check_stop()
                results[name] = future.result()
        except BaseException:
            pool.shutdown(wait=True, cancel_futures=True)
            raise
        pool.shutdown(wait=True)
        return results


def _overlap(x: int, y: int, w: int, h: int, ax: int, ay: int) -> float:
    """Fracción del área de la caja (x, y, w, h) cubierta por otra caja del mismo tamaño en (ax, ay)."""
    ix = max(0, min(x + w, ax + w) - max(x, ax))
    iy = max(0, min(y + h, ay + h) - max(y, ay))
    return (ix * iy) / (w * h)
