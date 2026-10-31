"""Zona roja de la aldea rival: el contorno que rodea sus edificios y donde no se puede soltar tropas.

Solo se ve en la fase de exploración («Battle starts in»); desaparece al empezar la batalla. Se lee
una vez justo antes de desplegar y de ella sale el trazo del ataque (`policy/deploy_path.py`).

Cómo se encuentra: el contorno es una línea de 1-2 px más roja que su entorno, con una saturación y un
brillo que cambian de una aldea a otra, así que no se usa un umbral de color absoluto sino un
**top-hat** (blanco sobre fondo) del canal «a» de LAB: resalta lo estrecho y rojizo e ignora las
superficies rojas grandes (tejados) y el verde. Se tapa la interfaz, se dilata para unir los trozos del
trazo y se toma la masa mayor: **la aldea**. Los trozos sueltos (un edificio aislado, otro contorno
pequeño) se ignoran. La envolvente convexa de esa masa es la figura «sin entrantes» sobre la que se
traza.

Sin una masa clara (poca línea roja, o dos masas parecidas) no se devuelve zona: el ataque no empieza.
Medido sobre capturas reales: la aldea da una masa de 2000–4000 px (a 720 de alto) y la siguiente, menos
de 500; sin zona roja (batalla, aldea, resultado) la mayor no pasa de ~1250 y la proporción es < 3.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

TOPHAT_KERNEL = 7  # px a 720 de alto
TOPHAT_THRESHOLD = 12.0  # diferencia del canal «a» (0..255)
JOIN_FRACTION = 0.014  # dilatación (fracción del alto) para unir los trozos del contorno
MIN_PIXELS = 1500  # píxeles de la masa mayor a 720 de alto; se escala con el alto
MIN_RATIO = 3.5  # la masa mayor debe ser al menos esto veces la segunda


@dataclass(frozen=True)
class RedZone:
    hull: tuple[tuple[int, int], ...]  # envolvente convexa, en píxeles del fotograma
    center: tuple[float, float]
    pixels: int  # píxeles rojos de la masa mayor
    ratio: float  # masa mayor / segunda (inf si no hay segunda)


@dataclass(frozen=True)
class RedZoneReading:
    zone: RedZone | None
    reason: str = ""


def _hide_interface(mask: np.ndarray) -> None:
    """Pone a cero lo que no es terreno: marcadores, botones y barra de cartas."""
    h, w = mask.shape
    mask[: int(0.10 * h)] = 0  # cuenta atrás y recursos
    mask[int(0.845 * h) :] = 0  # barra de cartas
    mask[int(0.74 * h) :, : int(0.13 * w)] = 0  # End Battle / Surrender
    mask[int(0.68 * h) :, int(0.80 * w) :] = 0  # Next / Overall Damage
    mask[: int(0.22 * h), : int(0.16 * w)] = 0  # botín disponible
    mask[: int(0.17 * h), int(0.80 * w) :] = 0  # oro y elixir propios


def read_red_zone(image: np.ndarray) -> RedZoneReading:
    h, w = image.shape[:2]
    scale = h / 720.0
    kernel = max(3, round(TOPHAT_KERNEL * scale)) | 1
    a = cv2.cvtColor(image, cv2.COLOR_RGB2LAB)[..., 1].astype(np.float32)
    a = cv2.GaussianBlur(a, (0, 0), 0.6 * scale)
    tophat = cv2.morphologyEx(a, cv2.MORPH_TOPHAT, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (kernel, kernel)))
    mask = (tophat >= TOPHAT_THRESHOLD).astype(np.uint8)
    _hide_interface(mask)

    join = max(3, round(JOIN_FRACTION * h)) | 1
    count, labels, _, _ = cv2.connectedComponentsWithStats(cv2.dilate(mask, np.ones((join, join), np.uint8)), connectivity=8)
    sizes = sorted(((int(((labels == i) & (mask > 0)).sum()), i) for i in range(1, count)), reverse=True)
    if not sizes:
        return RedZoneReading(None, "no se ve ninguna línea roja")
    main, index = sizes[0]
    second = sizes[1][0] if len(sizes) > 1 else 0
    ratio = main / second if second else float("inf")
    if main < MIN_PIXELS * scale:
        return RedZoneReading(None, f"la zona roja se ve poco ({main} px)")
    if ratio < MIN_RATIO:
        return RedZoneReading(None, f"no hay una masa roja clara (la mayor es solo {ratio:.1f} veces la segunda)")
    ys, xs = np.nonzero((labels == index) & (mask > 0))
    hull = cv2.convexHull(np.stack([xs, ys], axis=1).astype(np.int32))[:, 0, :]
    center = (float(xs.mean()), float(ys.mean()))
    return RedZoneReading(RedZone(tuple((int(x), int(y)) for x, y in hull), center, main, ratio))


__all__ = ["RedZone", "RedZoneReading", "read_red_zone"]
