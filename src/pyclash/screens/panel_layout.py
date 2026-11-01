"""Geometría del panel «Buildings & Heroes» (acoplado a la derecha), como fracciones del fotograma.

Medida sobre capturas reales de la ventana de Google Play Games de la aldea principal. Los demás
perfiles (laboratorio, mascotas, Builder Base) son copias PROVISIONALES: su estructura de fila se
supone igual hasta tener una captura de cada uno (la de mascotas parte más a la derecha).
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from .layout import RoiFraction


@dataclass(frozen=True)
class PanelLayout:
    # Zona de filas: bajo la cabecera del panel y sobre las pestañas de constructores.
    left: float = 0.735
    top: float = 0.065
    bottom: float = 0.895
    # Franja de la derecha donde se dibuja el icono de recurso del coste.
    icon_x: tuple[float, float] = (0.978, 1.0)
    icon_side: tuple[float, float] = (0.012, 0.040)  # lado, como fracción del alto
    icon_min_area: float = 0.00022  # fracción de alto², descarta relojes y brillos
    # Desplazamientos respecto al centro del icono del coste (fracción del alto).
    name_dy: float = -0.025
    band: float = 0.019  # mitad del alto de la banda de una línea de texto
    name_x: tuple[float, float] = (0.775, 0.905)
    time_x: tuple[float, float] = (0.895, 0.985)
    cost_x_min: float = 0.885  # el borde derecho del coste es el borde izquierdo del icono
    free_x: tuple[float, float] = (0.925, 0.996)
    new_x: tuple[float, float] = (0.775, 0.830)
    header_x: tuple[float, float] = (0.775, 0.985)
    # Tamaños de fuente a BASE_HEIGHT (1440).
    name_px: int = 23
    cost_px: int = 24
    time_px: int = 20
    free_px: int = 24
    new_px: int = 22
    header_px: int = 22
    # Cordura del coste. Decisión explícita (E3R3): cuentas de ayuntamiento bajo cuestan 150-750,
    # así que el mínimo es 100 (múltiplos de 50), no los 1000 que sirven para TH altos.
    min_cost: int = 100
    max_cost: int = 40_000_000
    provisional: bool = False

    def viewport(self, h: int, w: int) -> tuple[int, int, int, int]:
        """(x0, y0, x1, y1) en píxeles de la zona de filas."""
        return int(w * self.left), int(h * self.top), w, int(h * self.bottom)


VILLAGE_PANEL = PanelLayout()

PANEL_PROFILES: dict[str, PanelLayout] = {
    "village": VILLAGE_PANEL,
    "lab": replace(VILLAGE_PANEL, provisional=True),
    "pets": replace(VILLAGE_PANEL, left=0.80, name_x=(0.81, 0.905), provisional=True),
    "builder_base": replace(VILLAGE_PANEL, provisional=True),
}

# Botón de filtro/orden del panel (arriba a la izquierda de la cabecera) y su ventana emergente.
FILTER_BUTTON = RoiFraction(0.735, 0.0, 0.07, 0.065)
