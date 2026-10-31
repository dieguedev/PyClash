"""Preprocesado compartido: binarización de texto blanco y filtrado de componentes por área."""

from __future__ import annotations

import cv2
import numpy as np


def to_gray(image: np.ndarray) -> np.ndarray:
    """Escala de grises de un fotograma RGB (los grises pasan sin cambios)."""
    if image.ndim == 2:
        return image
    return cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)


def binarize_white(image: np.ndarray, threshold: int = 200) -> np.ndarray:
    """Máscara 0/255 de los píxeles casi blancos (los tres canales por encima del umbral)."""
    if image.ndim == 2:
        return ((image >= threshold) * 255).astype(np.uint8)
    return ((image.min(axis=2) >= threshold) * 255).astype(np.uint8)


def filter_components(
    mask: np.ndarray, min_area: int = 1, max_area: int | None = None
) -> np.ndarray:
    """Conserva solo las componentes conexas cuya área está en [min_area, max_area]."""
    count, labels, stats, _ = cv2.connectedComponentsWithStats((mask > 0).astype(np.uint8), connectivity=8)
    keep = np.zeros(count, dtype=bool)
    for i in range(1, count):
        area = stats[i, cv2.CC_STAT_AREA]
        keep[i] = area >= min_area and (max_area is None or area <= max_area)
    return (keep[labels] * 255).astype(np.uint8)


def component_boxes(mask: np.ndarray, min_area: int = 1) -> list[tuple[int, int, int, int]]:
    """Cajas (x, y, w, h) de las componentes conexas, ordenadas de izquierda a derecha."""
    count, _, stats, _ = cv2.connectedComponentsWithStats((mask > 0).astype(np.uint8), connectivity=8)
    boxes = [
        (int(s[cv2.CC_STAT_LEFT]), int(s[cv2.CC_STAT_TOP]), int(s[cv2.CC_STAT_WIDTH]), int(s[cv2.CC_STAT_HEIGHT]))
        for s in stats[1:count]
        if s[cv2.CC_STAT_AREA] >= min_area
    ]
    return sorted(boxes)
