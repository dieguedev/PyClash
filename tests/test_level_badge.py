"""Nivel de la insignia de las filas del panel, sobre una captura real (a la escala de la ventana de origen).

La captura trae filas con nivel 2 (Gold Mine, Elixir Collector ×2, Bomb), 3 (Wall) y 4 (Elixir Collector).
"""

import numpy as np
from PIL import Image

from pyclash.policy.walls import ELIXIR_MIN_LEVEL, gold_only_level
from pyclash.screens.level_badge import read_level
from pyclash.screens.panel_layout import PANEL_PROFILES
from pyclash.screens.upgrade_panel import PanelReader

from helpers import FIXTURES

# y del nombre de cada fila y nivel de su insignia (a 1766×993)
ROWS = {"Gold Mine": (260, 2), "Elixir Collector x2": (339, 2), "Wall": (449, 3), "Elixir Collector": (528, 4), "Bomb": (607, 2)}


def panel():
    with Image.open(FIXTURES / "panel_levels_2_3_4.png") as im:
        return np.array(im.convert("RGB"))


def test_lee_el_nivel_2_3_y_4_de_las_insignias():
    image = panel()
    assert {name: read_level(image, y) for name, (y, _) in ROWS.items()} == {n: lvl for n, (_, lvl) in ROWS.items()}


def test_sin_insignia_o_sin_ventana_no_inventa_nada():
    image = panel()
    assert read_level(image, 181) is None  # Builder's Hut: no tiene insignia
    assert read_level(image, 96) is None  # Town Hall fijado arriba: tampoco
    assert read_level(np.zeros((100, 100, 3), np.uint8), 10) is None


def test_a_otra_escala_de_ventana_no_afirma_nada():
    small = np.array(Image.fromarray(panel()).resize((1280, 720), Image.LANCZOS))
    assert read_level(small, int(449 * 720 / 993)) is None  # mejor «no sé» que un nivel equivocado


def test_la_fila_del_muro_trae_su_nivel_y_las_demas_no():
    rows = PanelReader(PANEL_PROFILES["village"]).read(panel()).rows
    wall = next(r for r in rows if r.name == "Wall")
    assert wall.level == 3 and wall.cost == 5000
    assert all(r.level is None for r in rows if r.name != "Wall")


def test_el_elixir_empieza_en_el_nivel_5():
    assert ELIXIR_MIN_LEVEL == 5
    assert [gold_only_level(n) for n in (2, 3, 4, 5, 6)] == [True, True, True, False, False]
    assert gold_only_level(None) is False
