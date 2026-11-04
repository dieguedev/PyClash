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

El contorno de una aldea puede salir partido en varios trozos grandes (un árbol o una roca lo interrumpen
un momento): los trozos de tamaño parecido y cercanos al mayor se tratan como la misma aldea; los pequeños
o lejanos, no.

Sin una masa clara (poca línea roja, o la aldea no destaca sobre lo demás) no se devuelve zona: el ataque
no empieza. Medido sobre capturas reales de exploración a 720-990 de alto: la aldea da 2000-9000 px y los
trozos sueltos, menos de ~1000.

El contorno PARPADEA: un solo fotograma puede coger el instante tenue y rechazar una zona válida. Para
decidir se leen varios fotogramas y se combinan con el máximo (`read_red_zone_frames`).

SOLO ES FIABLE EN LA EXPLORACIÓN. En otras pantallas (la aldea propia, una batalla ya empezada) hay muchas
cosas rojas (tejados, tropas) y a mayor resolución el tamaño de una masa ya no distingue una aldea de un
falso positivo. Quien la llama debe haber comprobado antes que la pantalla es la exploración.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

TOPHAT_KERNEL = 7  # px a 720 de alto
TOPHAT_THRESHOLD = 12.0  # diferencia del canal «a» (0..255)
JOIN_FRACTION = 0.014  # dilatación (fracción del alto) para unir los trozos del contorno
MIN_PIXELS = 1500  # píxeles de la masa mayor a 720 de alto; se escala con el alto
MIN_RATIO = 4.5  # la aldea debe ser al menos esto veces el mayor trozo que no es suyo
MERGE_MIN_FRACTION = 0.4  # un trozo de al menos esta fracción del mayor puede ser parte de la misma aldea…
MERGE_DISTANCE = 0.08  # …si está a menos de esto (fracción del alto) de él


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


def _response(image: np.ndarray) -> np.ndarray:
    """Cuánto resalta cada píxel como línea estrecha y rojiza (top-hat del canal «a»), sin umbral todavía."""
    h = image.shape[0]
    scale = h / 720.0
    kernel = max(3, round(TOPHAT_KERNEL * scale)) | 1
    a = cv2.cvtColor(image, cv2.COLOR_RGB2LAB)[..., 1].astype(np.float32)
    a = cv2.GaussianBlur(a, (0, 0), 0.6 * scale)
    return cv2.morphologyEx(a, cv2.MORPH_TOPHAT, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (kernel, kernel)))


def _analyse(response: np.ndarray, h: int) -> RedZoneReading:
    w = response.shape[1]
    scale = h / 720.0
    mask = (response >= TOPHAT_THRESHOLD).astype(np.uint8)
    _hide_interface(mask)

    join = max(3, round(JOIN_FRACTION * h)) | 1
    count, labels, _, _ = cv2.connectedComponentsWithStats(cv2.dilate(mask, np.ones((join, join), np.uint8)), connectivity=8)
    sizes = sorted(((int(((labels == i) & (mask > 0)).sum()), i) for i in range(1, count)), reverse=True)
    if not sizes:
        return RedZoneReading(None, "no se ve ninguna línea roja")
    main, index = sizes[0]

    # El contorno de una aldea puede salir partido en trozos grandes (un árbol o una roca lo interrumpen):
    # los trozos de tamaño parecido y cercanos al mayor son la misma aldea. Los pequeños o lejanos, no.
    village = (labels == index) & (mask > 0)
    parts = [i for s, i in sizes if s >= MERGE_MIN_FRACTION * main]
    if len(parts) > 1:
        big = (np.isin(labels, parts) & (mask > 0)).astype(np.uint8)
        reach = max(3, round(MERGE_DISTANCE * h)) | 1
        _, linked = cv2.connectedComponents(cv2.dilate(big, np.ones((reach, reach), np.uint8)), connectivity=8)
        anchor = np.argwhere(village)[0]
        village = (big > 0) & (linked == linked[anchor[0], anchor[1]])
    merged = {int(i) for i in np.unique(labels[village])}
    pixels = int(village.sum())
    rest = max((s for s, i in sizes if i not in merged), default=0)
    ratio = pixels / rest if rest else float("inf")
    if pixels < MIN_PIXELS * scale:
        return RedZoneReading(None, f"la zona roja se ve poco ({pixels} px)")
    if ratio < MIN_RATIO:
        return RedZoneReading(None, f"no hay una masa roja clara (la aldea es solo {ratio:.1f} veces el siguiente trozo)")
    ys, xs = np.nonzero(village)
    hull = cv2.convexHull(np.stack([xs, ys], axis=1).astype(np.int32))[:, 0, :]
    center = (float(xs.mean()), float(ys.mean()))
    return RedZoneReading(RedZone(tuple((int(x), int(y)) for x, y in hull), center, pixels, ratio))


def read_red_zone(image: np.ndarray) -> RedZoneReading:
    """Zona roja de UN fotograma. El contorno parpadea: para decidir, usa `read_red_zone_frames`."""
    return _analyse(_response(image), image.shape[0])


def read_red_zone_frames(images: list[np.ndarray]) -> RedZoneReading:
    """Zona roja de VARIOS fotogramas de la misma exploración: se queda con la lectura más clara.

    El contorno rojo parpadea (se desvanece y vuelve): en un fotograma tenue casi no se ve (medido en capturas
    reales: la pieza mayor baja a ~800 px cuando con el contorno pleno son 2000-9000 y se rechaza una zona
    válida). Se analiza CADA fotograma por separado y además la combinación de todos con el máximo de cada píxel
    (el contorno en su momento más intenso). Entre las lecturas válidas gana la de mayor proporción aldea /
    siguiente trozo, o sea la más segura. La combinación sola no basta: al acumular fotogramas también
    acumula manchas sueltas, que suben «el siguiente trozo» y pueden rechazar una aldea que en un solo
    fotograma se ve limpia. Los fotogramas deben ser del mismo tamaño y sin mover la cámara.
    """
    if not images:
        return RedZoneReading(None, "no hay fotogramas")
    if len({i.shape for i in images}) != 1:
        return RedZoneReading(None, "los fotogramas no tienen el mismo tamaño")
    h = images[0].shape[0]
    responses = [_response(i) for i in images]
    candidates = list(responses)
    if len(responses) > 1:
        candidates.append(np.maximum.reduce(responses))
    readings = [_analyse(r, h) for r in candidates]
    valid = [r for r in readings if r.zone is not None]
    if valid:
        return max(valid, key=lambda r: (min(r.zone.ratio, 1e6), r.zone.pixels))
    return readings[-1]  # ninguna sirve: el motivo de la combinación es el más representativo


__all__ = ["RedZone", "RedZoneReading", "read_red_zone", "read_red_zone_frames"]
