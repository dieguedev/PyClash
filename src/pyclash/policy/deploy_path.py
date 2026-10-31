"""Trazo de despliegue: un único gesto continuo de 3 o 4 vueltas alrededor de la aldea, sin patrón fijo.

Función pura (sin dispositivo ni reloj; el azar se inyecta): dado el contorno de la zona roja y el tamaño
del fotograma devuelve los puntos por los que pasa el dedo y cuánto espera en cada uno.

La figura sale de la envolvente convexa de la zona roja (sin entrantes), ensanchada hacia fuera. Para que no
parezca un bot:

- el margen sobre la zona cambia de una vuelta a otra y se suaviza dentro de cada vuelta, así las vueltas
  no son la misma curva;
- sobre el margen va un ruido de baja frecuencia distinto en cada vuelta (el trazo ondula como un dedo);
- el dedo acelera y frena, y los intervalos entre puntos no son iguales;
- un pequeño temblor por punto;
- empieza en un ángulo al azar, gira en un sentido al azar y acaba **después de un trozo extra de vuelta**,
  nunca donde empezó.

Los puntos se recortan al terreno visible (por encima de la barra de cartas y sin tocar la interfaz).
Soltar fuera del terreno jugable no hace nada en el juego; no se intenta detectar el rombo del mapa.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

Point = tuple[float, float]


@dataclass(frozen=True)
class PathSettings:
    laps: tuple[int, int] = (3, 4)  # vueltas completas, a elegir al azar entre estos valores
    extra_lap: tuple[float, float] = (0.1, 0.35)  # trozo de vuelta que se añade al final
    margin: tuple[float, float] = (0.04, 0.06)  # fracción del alto del fotograma sobre la zona roja
    margin_variation: float = 0.4  # ±, por vuelta
    wobble: tuple[float, float] = (0.1, 0.3)  # amplitud del ruido, como fracción del margen de la vuelta
    speed: float = 0.9  # alto del fotograma por segundo
    speed_variation: float = 0.3
    step_s: float = 0.02
    step_jitter: float = 0.15
    tremor: float = 0.0015  # desviación del temblor, fracción del alto
    top: float = 0.12  # límites del terreno visible, fracciones del fotograma
    bottom: float = 0.84
    side: float = 0.01


@dataclass(frozen=True)
class Trace:
    points: tuple[tuple[int, int], ...]
    waits: tuple[float, ...]  # segundos que se espera tras llegar a cada punto
    lap_ends: tuple[int, ...] = field(default_factory=tuple)  # índice (excluido) donde acaba cada vuelta; el último es el final

    @property
    def laps(self) -> int:
        """Vueltas completas (el último elemento de `lap_ends` es el final del trozo extra)."""
        return max(0, len(self.lap_ends) - 1)


def _centroid(hull: list[Point]) -> Point:
    area = cx = cy = 0.0
    for (x0, y0), (x1, y1) in zip(hull, hull[1:] + hull[:1]):
        cross = x0 * y1 - x1 * y0
        area += cross
        cx += (x0 + x1) * cross
        cy += (y0 + y1) * cross
    if abs(area) < 1e-9:
        return sum(p[0] for p in hull) / len(hull), sum(p[1] for p in hull) / len(hull)
    return cx / (3 * area), cy / (3 * area)


def _hull_radius(hull: list[Point], center: Point, theta: float) -> float:
    """Distancia del centro al contorno siguiendo el ángulo `theta` (el contorno es convexo)."""
    dx, dy = math.cos(theta), math.sin(theta)
    best = 0.0
    for (x0, y0), (x1, y1) in zip(hull, hull[1:] + hull[:1]):
        ex, ey = x1 - x0, y1 - y0
        det = dx * ey - dy * ex
        if abs(det) < 1e-12:
            continue
        px, py = x0 - center[0], y0 - center[1]
        t = (px * ey - py * ex) / det
        u = (px * dy - py * dx) / det
        if t >= 0 and -1e-9 <= u <= 1 + 1e-9:
            best = max(best, t)
    return best


def inside(hull: list[Point], x: float, y: float) -> bool:
    """¿(x, y) está dentro del polígono? (trazado de rayos)"""
    n, hit, j = len(hull), False, len(hull) - 1
    for i in range(n):
        (xi, yi), (xj, yj) = hull[i], hull[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            hit = not hit
        j = i
    return hit


def _smooth(t: float) -> float:
    return t * t * (3 - 2 * t)


def make_trace(
    hull_points: list[tuple[int, int]] | tuple[tuple[int, int], ...],
    size: tuple[int, int],
    rng: random.Random,
    settings: PathSettings | None = None,
) -> Trace:
    cfg = settings or PathSettings()
    hull: list[Point] = [(float(x), float(y)) for x, y in hull_points]
    if len(hull) < 3:
        raise ValueError("el contorno necesita al menos tres puntos")
    width, height = size
    center = _centroid(hull)

    laps = rng.randint(*cfg.laps)
    total = (laps + rng.uniform(*cfg.extra_lap)) * 2 * math.pi
    direction = rng.choice((1, -1))
    theta = theta0 = rng.uniform(0, 2 * math.pi)
    base = rng.uniform(*cfg.margin) * height
    margins = [base * (1 + rng.uniform(-cfg.margin_variation, cfg.margin_variation)) for _ in range(laps + 2)]
    ripples = [
        [(rng.uniform(*cfg.wobble) * m / k, k, rng.uniform(0, 2 * math.pi)) for k in (1, 2, 3)] for m in margins
    ]
    f1, f2 = rng.uniform(0.3, 0.6), rng.uniform(0.8, 1.4)
    p1, p2 = rng.uniform(0, 2 * math.pi), rng.uniform(0, 2 * math.pi)

    xs_min, xs_max = cfg.side * width, (1 - cfg.side) * width
    ys_min, ys_max = cfg.top * height, cfg.bottom * height

    points: list[tuple[int, int]] = []
    waits: list[float] = []
    lap_ends: list[int] = []
    t = 0.0
    progress = 0.0
    next_lap = 1
    while progress < total:
        lap_f = progress / (2 * math.pi)
        i = int(lap_f)
        w = _smooth(lap_f - i)
        margin = (1 - w) * margins[i] + w * margins[i + 1]
        wobble = sum(
            ((1 - w) * a0 * math.sin(k0 * theta + ph0) + w * a1 * math.sin(k1 * theta + ph1))
            for (a0, k0, ph0), (a1, k1, ph1) in zip(ripples[i], ripples[i + 1])
        )
        radius = _hull_radius(hull, center, theta) + max(0.5 * margins[i], margin + wobble)
        x = center[0] + radius * math.cos(theta) + rng.gauss(0, cfg.tremor * height)
        y = center[1] + radius * math.sin(theta) + rng.gauss(0, cfg.tremor * height)
        x, y = min(max(x, xs_min), xs_max), min(max(y, ys_min), ys_max)
        if not inside(hull, x, y):  # recortar al marco nunca debe meter el dedo en la zona roja
            points.append((round(x), round(y)))
            step = cfg.step_s * (1 + rng.uniform(-cfg.step_jitter, cfg.step_jitter))
            waits.append(step)
        else:
            step = cfg.step_s
        if progress >= next_lap * 2 * math.pi and points:
            lap_ends.append(len(points))
            next_lap += 1
        speed = cfg.speed * height * (
            1 + cfg.speed_variation * (0.6 * math.sin(2 * math.pi * f1 * t + p1) + 0.4 * math.sin(2 * math.pi * f2 * t + p2))
        )
        progress += max(1e-4, speed * step / radius)
        theta = theta0 + direction * progress
        t += step
    lap_ends.append(len(points))
    return Trace(tuple(points), tuple(waits), tuple(lap_ends))


__all__ = ["PathSettings", "Trace", "inside", "make_trace"]
