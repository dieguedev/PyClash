"""Nivel de la insignia de las filas del panel, sobre una captura real (a la escala de la ventana de origen).

La captura trae filas con nivel 2 (Gold Mine, Elixir Collector ×2, Bomb), 3 (Wall) y 4 (Elixir Collector).
"""

import numpy as np
import pytest
from PIL import Image

from pyclash.policy.walls import ELIXIR_MIN_LEVEL, gold_only_level
from pyclash.screens.level_badge import read_any_level, read_level
from pyclash.screens.town_hall import offers_town_hall_10, read_town_hall_level
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


def _hud_con(level: int, size=(1765, 993)):
    """Fotograma sintético con el icono del Ayuntamiento `level` pegado donde lo dibuja el perfil del HUD."""
    import pyclash.screens.town_hall as th

    image = np.full((size[1], size[0], 3), (60, 110, 40), np.uint8)
    icon = th._level_templates()[level]
    image[40 : 40 + icon.shape[0], 25 : 25 + icon.shape[1]] = icon
    return image


def test_lee_el_nivel_del_ayuntamiento_en_el_perfil_de_la_aldea_real():
    assert read_town_hall_level(capture("village_th9.png")) == 9


@pytest.mark.parametrize("level", [2, 3, 4, 5, 6, 9])
def test_lee_cada_nivel_con_plantilla(level):
    assert read_town_hall_level(_hud_con(level)) == level


@pytest.mark.parametrize("size", [(1766, 993), (1765, 993), (1763, 990), (1762, 989)])
def test_tolera_las_pequenas_diferencias_de_tamano_de_la_ventana(size):
    assert read_town_hall_level(_hud_con(9, size)) == 9


def test_sin_hud_o_sin_plantilla_es_duda_nunca_otro_nivel():
    # panel abierto: el HUD no se dibuja
    assert read_town_hall_level(capture("panel_levels_2_to_8.png")) is None
    # fondo liso: ninguna plantilla encaja
    assert read_town_hall_level(np.full((993, 1766, 3), (60, 110, 40), np.uint8)) is None


def test_a_otra_escala_el_lector_de_nivel_dice_no_se():
    assert read_town_hall_level(capture("main_village.png")) is None
    assert read_town_hall_level(np.zeros((720, 1280, 3), np.uint8)) is None


def test_dos_niveles_parecidos_no_se_adivinan(monkeypatch):
    import pyclash.screens.town_hall as th

    image = _hud_con(4)
    monkeypatch.setattr(th.cv2, "matchTemplate", lambda *a, **k: np.full((2, 2), 0.95, np.float32))  # todos igual
    assert th.read_town_hall_level(image) is None


def test_el_detector_del_th10_tolera_el_tamano_de_la_ventana():
    image = np.zeros((989, 1762, 3), np.uint8)
    assert offers_town_hall_10(image) is False  # fondo liso: seguro que no hay tarjeta


def test_el_detector_del_th10_dice_duda_en_la_zona_gris(monkeypatch):
    import pyclash.screens.town_hall as th

    image = np.zeros((993, 1766, 3), np.uint8)
    for score, expected in [(0.95, True), (0.85, True), (0.75, None), (0.66, None), (0.65, False), (0.3, False)]:
        monkeypatch.setattr(th.cv2, "matchTemplate", lambda *a, _s=score, **k: np.full((2, 2), _s, np.float32))
        assert th.offers_town_hall_10(image) is expected, score


def _town_hall_row(name):
    from pyclash.screens.upgrade_panel import PanelReader

    rows = [r for r in PanelReader().read(capture(name)).rows if r.name == "Town Hall"]
    assert len(rows) == 1, name
    return rows[0]


@pytest.mark.parametrize("name", ["panel_levels_2_to_8.png", "panel_levels_2_to_8_b.png"])
def test_el_panel_lee_el_numero_de_la_fila_del_ayuntamiento(name):
    assert _town_hall_row(name).level == 5  # se sube a TH5: la cuenta está en TH4


def test_la_fila_del_th10_no_tiene_numero_legible():
    assert _town_hall_row("panel_levels_2_to_9_th10.png").level is None


def test_la_politica_solo_sube_el_ayuntamiento_con_numero_legible():
    import random

    from pyclash.policy.upgrades import Mode, UpgradeSettings, UpgradeState, decide_upgrade

    def pick(name, th_level, offers):
        row = _town_hall_row(name)
        state = UpgradeState((row,), (10**9,) * 3, 3, th_level=th_level, offers_th10=offers)
        return decide_upgrade(state, UpgradeSettings(mode=Mode.RUSHER, reserve_builders=0), random.Random(1)).chosen

    assert pick("panel_levels_2_to_8.png", None, False) is not None
    assert pick("panel_levels_2_to_9_th10.png", 9, True) is None
    assert pick("panel_levels_2_to_9_th10.png", None, False) is None  # sin número legible no se sube aunque nada diga TH9
