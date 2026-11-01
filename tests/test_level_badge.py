"""Nivel de la insignia de las filas del panel, sobre una captura real (a la escala de la ventana de origen).

La captura trae filas con nivel 2 (Gold Mine, Elixir Collector ×2, Bomb), 3 (Wall) y 4 (Elixir Collector).
"""

import numpy as np
from PIL import Image

from pyclash.policy.walls import ELIXIR_MIN_LEVEL, gold_only_level
from pyclash.screens.level_badge import read_any_level, read_level
from pyclash.screens.town_hall import offers_town_hall_10
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


# --- niveles 2 a 9, de tres capturas reales del panel (y del nombre de cada fila, nivel de su insignia) ------------
# La insignia es el nivel AL QUE SE MEJORA. La tarjeta del Town Hall de la 3.ª captura pone «10» (dos dígitos) y no se lee aquí.
CAPTURAS = {
    "panel_levels_2_to_8.png": {
        "Gold Mine": (153, 3), "Army Camp x2": (231, 4), "Town Hall": (308, 5), "Elixir Storage x2": (435, 8),
        "Cannon x2": (514, 5), "Mortar": (593, 2), "Archer Tower": (672, 4), "Wall x50": (750, 4),
        "Elixir Collector": (829, 6),
    },
    "panel_levels_2_to_8_b.png": {
        "Gold Storage x2": (139, 8), "Gold Mine x2": (218, 7), "Gold Mine": (297, 3), "Army Camp x2": (376, 4),
        "Town Hall": (453, 5), "Elixir Storage": (580, 8), "Cannon x2": (659, 5), "Mortar": (738, 2),
        "Archer Tower": (817, 4),
    },
    "panel_levels_2_to_9_th10.png": {
        "X-Bow": (266, 3), "Bomb Tower": (344, 2), "Air Sweeper": (423, 4), "Hidden Tesla x2": (502, 4),
        "Seeking Air Mine x4": (581, 2), "Wizard Tower x2": (660, 5), "Mortar x2": (739, 6), "Archer Tower x3": (818, 9),
    },
}
# El 8 y el 9 se parecen: esta fila puede dar «no sé» (None) pero nunca otro número.
AMBIGUAS = {("panel_levels_2_to_8_b.png", "Gold Storage x2")}


def capture(name: str):
    with Image.open(FIXTURES / name) as im:
        return np.array(im.convert("RGB"))


def test_lee_los_niveles_2_a_9_sin_equivocarse_nunca():
    for name, rows in CAPTURAS.items():
        image = capture(name)
        for row, (y, level) in rows.items():
            got = read_any_level(image, y)
            assert got == level or (got is None and (name, row) in AMBIGUAS), f"{name} · {row}: {got} en vez de {level}"


def test_el_lector_de_muros_sigue_siendo_estricto_con_2_3_y_4():
    # Con las plantillas 5-9 presentes, un 3 sigue leyéndose como 3 y un 5, 6 u 8 NO se toma por 2, 3 o 4.
    image = capture("panel_levels_2_to_8.png")
    assert read_level(image, 153) == 3 and read_level(image, 593) == 2 and read_level(image, 672) == 4
    assert [read_level(image, y) for y in (308, 435, 829)] == [None, None, None]


def test_el_lector_amplio_no_inventa_donde_no_hay_insignia():
    image = capture("panel_levels_2_to_9_th10.png")
    assert read_any_level(image, 139) is None  # tarjeta del TH10: «10» tiene dos dígitos
    assert read_any_level(image, 11) is None  # cabecera del panel
    assert read_any_level(np.zeros((100, 100, 3), np.uint8), 10) is None


def test_detecta_la_tarjeta_del_ayuntamiento_10_y_solo_esa():
    assert offers_town_hall_10(capture("panel_levels_2_to_9_th10.png")) is True
    for name in ("panel_levels_2_to_8.png", "panel_levels_2_to_8_b.png", "panel_levels_2_3_4.png"):
        assert offers_town_hall_10(capture(name)) is False


def test_a_otra_escala_el_detector_del_th10_dice_no_se():
    assert offers_town_hall_10(np.zeros((720, 1280, 3), np.uint8)) is None
