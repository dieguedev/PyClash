"""Dibuja el trazo de despliegue sobre una captura de la exploración, para pulirlo a ojo.

Uso:
    python tools/preview_path.py                                   # fixture de ejemplo, semilla al azar
    python tools/preview_path.py captures_raw/scouting-base.png --seed 7
    python tools/preview_path.py CAPTURA.png --count 6             # 6 trazos distintos, uno por imagen
    python tools/preview_path.py CAPTURA.png --count 6 --overlay   # los 6 sobre la misma imagen
    python tools/preview_path.py CAPTURA.png --margin 0.03,0.05 --wobble 0.2,0.4

Se guarda en `captures_raw/path_debug/` (ignorada por git: las capturas pueden llevar datos personales).

Leyenda: amarillo = contorno de la zona roja (envolvente convexa); cada vuelta de un color; círculo verde
«S» = donde empieza el dedo; círculo rojo «E» = donde acaba; flechas = sentido del movimiento. El texto
resume vueltas, puntos y duración.
"""

from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pyclash.policy.deploy_path import PathSettings, Trace, make_trace  # noqa: E402
from pyclash.screens.red_zone import read_red_zone  # noqa: E402

DEFAULT_IMAGE = ROOT / "tests" / "fixtures" / "screens" / "scouting_barbarians.png"
OUT_DIR = ROOT / "captures_raw" / "path_debug"
LAP_COLORS = [(0, 255, 255), (255, 0, 255), (255, 160, 0), (80, 255, 80), (255, 80, 80)]  # BGR/RGB da igual: se elige en RGB


def parse_pair(text: str) -> tuple[float, float]:
    low, high = (float(v) for v in text.split(","))
    return low, high


def draw_trace(canvas: np.ndarray, trace: Trace, color_by_lap: bool = True, color=(255, 255, 255), thickness: int = 2) -> None:
    start = 0
    for lap, end in enumerate(trace.lap_ends):
        pts = np.array(trace.points[max(0, start - 1) : end], dtype=np.int32).reshape(-1, 1, 2)
        paint = LAP_COLORS[lap % len(LAP_COLORS)] if color_by_lap else color
        if len(pts) > 1:
            cv2.polylines(canvas, [pts], False, paint, thickness, cv2.LINE_AA)
        start = end
    for i in range(0, len(trace.points) - 4, 30):  # flechas de sentido, pequeñas
        (x0, y0), (x1, y1) = trace.points[i], trace.points[i + 3]
        if (x0, y0) != (x1, y1):
            cv2.arrowedLine(canvas, (x0, y0), (x1, y1), (255, 255, 255), 1, cv2.LINE_AA, tipLength=0.6)


def mark_ends(canvas: np.ndarray, trace: Trace, label: str = "") -> None:
    sx, sy = trace.points[0]
    ex, ey = trace.points[-1]
    cv2.circle(canvas, (sx, sy), 9, (0, 220, 0), -1, cv2.LINE_AA)
    cv2.putText(canvas, "S" + label, (sx - 5, sy + 4), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 0), 1, cv2.LINE_AA)
    cv2.circle(canvas, (ex, ey), 9, (230, 0, 0), -1, cv2.LINE_AA)
    cv2.putText(canvas, "E" + label, (ex - 5, ey + 4), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1, cv2.LINE_AA)


def caption(canvas: np.ndarray, lines: list[str]) -> None:
    box_w = max(len(line) for line in lines) * 9 + 20
    shade = canvas.copy()
    cv2.rectangle(shade, (0, 0), (box_w, 18 * len(lines) + 12), (0, 0, 0), -1)
    cv2.addWeighted(shade, 0.65, canvas, 0.35, 0, canvas)  # fondo oscuro para que el texto se lea
    for i, line in enumerate(lines):
        y = 18 + 18 * i
        cv2.putText(canvas, line, (11, y + 1), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 2, cv2.LINE_AA)
        cv2.putText(canvas, line, (10, y), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1, cv2.LINE_AA)


def describe(trace: Trace, seed: int) -> str:
    return f"semilla {seed} | {trace.laps} vueltas + trozo extra | {len(trace.points)} puntos | {sum(trace.waits):.1f} s"


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("image", nargs="?", type=Path, default=DEFAULT_IMAGE)
    p.add_argument("--seed", type=int, help="semilla del primer trazo (por defecto, al azar)")
    p.add_argument("--count", type=int, default=1, help="cuántos trazos distintos generar")
    p.add_argument("--overlay", action="store_true", help="dibujar todos los trazos sobre la misma imagen")
    p.add_argument("--out", type=Path, default=OUT_DIR)
    p.add_argument("--margin", type=parse_pair, help="margen sobre la zona roja, fracción del alto: MIN,MAX")
    p.add_argument("--variation", type=float, help="variación del margen por vuelta (±)")
    p.add_argument("--wobble", type=parse_pair, help="ondulación como fracción del margen: MIN,MAX")
    p.add_argument("--speed", type=float, help="alto del fotograma por segundo")
    a = p.parse_args()

    image = np.array(Image.open(a.image).convert("RGB"))
    height, width = image.shape[:2]
    reading = read_red_zone(image)
    if reading.zone is None:
        print(f"No se pudo leer la zona roja de {a.image}: {reading.reason}", file=sys.stderr)
        return 1
    zone = reading.zone
    defaults = PathSettings()
    settings = PathSettings(
        margin=a.margin or defaults.margin,
        margin_variation=defaults.margin_variation if a.variation is None else a.variation,
        wobble=a.wobble or defaults.wobble,
        speed=defaults.speed if a.speed is None else a.speed,
    )

    first = a.seed if a.seed is not None else random.SystemRandom().randrange(1_000_000)
    seeds = [first + i for i in range(a.count)]
    traces = [make_trace(list(zone.hull), (width, height), random.Random(s), settings) for s in seeds]

    a.out.mkdir(parents=True, exist_ok=True)
    hull = np.array(zone.hull, dtype=np.int32).reshape(-1, 1, 2)
    zone_note = f"zona roja: {zone.pixels} px, masa {zone.ratio:.1f}x la siguiente"

    def base() -> np.ndarray:
        canvas = image.copy()
        cv2.polylines(canvas, [hull], True, (255, 255, 0), 2, cv2.LINE_AA)
        cx, cy = (int(v) for v in zone.center)
        cv2.drawMarker(canvas, (cx, cy), (255, 255, 0), cv2.MARKER_CROSS, 14, 2)
        return canvas

    written: list[Path] = []
    if a.overlay:
        canvas = base()
        for i, (seed, trace) in enumerate(zip(seeds, traces)):
            draw_trace(canvas, trace, color_by_lap=False, color=LAP_COLORS[i % len(LAP_COLORS)], thickness=1)
            mark_ends(canvas, trace, str(i + 1))
        caption(canvas, [f"{len(traces)} trazos superpuestos (semillas {seeds[0]}..{seeds[-1]})", zone_note])
        path = a.out / f"overlay-{seeds[0]}.png"
        Image.fromarray(canvas).save(path)
        written.append(path)
    else:
        for seed, trace in zip(seeds, traces):
            canvas = base()
            draw_trace(canvas, trace)
            mark_ends(canvas, trace)
            caption(canvas, [describe(trace, seed), zone_note])
            path = a.out / f"path-{seed}.png"
            Image.fromarray(canvas).save(path)
            written.append(path)

    for path in written:
        print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
