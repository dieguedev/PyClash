"""Imagen de depuración de un trazo de despliegue: qué dibujó el bot y qué pasó.

Izquierda: la captura justo antes de pulsar, con la zona roja leída (rojo), el trazo previsto (fino), la parte
que se llegó a recorrer (gruesa, de azul a amarillo según avanza), el punto de pulsación (verde), el último punto
(blanco) y los límites del terreno por los que se recorta el trazo. Derecha: la captura al soltar.
Pueden mostrar el nombre del jugador: la carpeta `debug/` está ignorada por git.
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
from PIL import Image


def draw_trace(
    before: np.ndarray,
    after: np.ndarray,
    hull: tuple[tuple[int, int], ...],
    points: tuple[tuple[int, int], ...],
    done: int,
    bounds: tuple[float, float, float, float],
    lines: list[str],
) -> np.ndarray:
    """`done` = cuántos puntos del trazo se llegaron a enviar (incluido el de pulsación). `bounds` = (top, bottom,
    left, right) en píxeles."""
    left = np.ascontiguousarray(before.copy())
    h, w = left.shape[:2]
    top, bottom, x0, x1 = (int(v) for v in bounds)
    cv2.rectangle(left, (x0, top), (x1, bottom), (255, 255, 255), 1)  # recorte del trazo
    if len(hull) >= 3:
        cv2.polylines(left, [np.array(hull, dtype=np.int32)], True, (255, 0, 0), 2)
    if len(points) >= 2:
        cv2.polylines(left, [np.array(points, dtype=np.int32)], False, (0, 255, 255), 1)  # lo previsto
    sent = points[: max(0, done)]
    for i in range(1, len(sent)):
        t = i / max(1, len(sent) - 1)
        color = (int(255 * (1 - t)), int(255 * t), int(255 * t))  # azul -> amarillo
        cv2.line(left, sent[i - 1], sent[i], color, 3)
    if points:
        cv2.circle(left, points[0], 9, (0, 255, 0), -1)
    if sent:
        cv2.circle(left, sent[-1], 7, (255, 255, 255), -1)
        cv2.circle(left, sent[-1], 7, (0, 0, 0), 1)
    for i, text in enumerate(lines):
        y = 22 + 20 * i
        cv2.putText(left, text, (10, y), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 0), 3, cv2.LINE_AA)
        cv2.putText(left, text, (10, y), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1, cv2.LINE_AA)
    right = after if after.shape == before.shape else cv2.resize(after, (w, h))
    return np.concatenate([left, right], axis=1)


def save_debug(directory: Path, name: str, image: np.ndarray) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}.png"
    Image.fromarray(image).save(path)
    return path


__all__ = ["draw_trace", "save_debug"]
