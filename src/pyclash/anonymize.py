"""Anonimización de capturas: tapa regiones con datos personales y marca la imagen."""

from __future__ import annotations

from pathlib import Path

from PIL import Image
from PIL.PngImagePlugin import PngInfo

MARKER_KEY = "pyclash-anonymized"
MARKER_VALUE = "1"

Region = tuple[int, int, int, int]  # x, y, ancho, alto


def anonymize(src: Path, dst: Path, regions: list[Region], fill: tuple[int, int, int] = (0, 0, 0)) -> None:
    """Escribe en `dst` una copia de `src` con `regions` rellenas y el marcador de anonimizado.

    Se vuelve a construir la imagen desde los píxeles, de modo que no se arrastran
    metadatos (EXIF, etc.) del original. `dst` no puede ser `src`.
    """
    if Path(src).resolve() == Path(dst).resolve():
        raise ValueError("el destino no puede ser el original: el original no debe quedar en el repositorio")
    with Image.open(src) as im:
        clean = Image.new("RGB", im.size)
        clean.paste(im.convert("RGB"))
    w, h = clean.size
    for x, y, rw, rh in regions:
        if rw <= 0 or rh <= 0 or x < 0 or y < 0 or x + rw > w or y + rh > h:
            raise ValueError(f"región fuera de la imagen o inválida: {(x, y, rw, rh)}")
        clean.paste(fill, (x, y, x + rw, y + rh))
    meta = PngInfo()
    meta.add_text(MARKER_KEY, MARKER_VALUE)
    Path(dst).parent.mkdir(parents=True, exist_ok=True)
    clean.save(dst, format="PNG", pnginfo=meta)


def is_anonymized(path: Path) -> bool:
    """True si la imagen PNG lleva el marcador de anonimizado."""
    try:
        with Image.open(path) as im:
            return im.format == "PNG" and getattr(im, "text", {}).get(MARKER_KEY) == MARKER_VALUE
    except OSError:
        return False
