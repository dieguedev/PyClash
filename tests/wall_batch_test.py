"""Barra de lote de muros (issue #13): botones activos o apagados, contador «x N» y «Upgrade More»."""

import numpy as np
import pytest

from pyclash.device.base import Rect
from pyclash.screens.wall_batch import BatchBarReader, cost_affordable

from helpers import fixture_frame


@pytest.fixture(scope="module")
def reader():
    return BatchBarReader()


def img(name):
    return fixture_frame(name).image.copy()


# --- R2: los botones activos son los correctos ---------------------------------------------------


def test_e2r2_botones_activos_con_el_lote_en_uno(reader):
    bar = reader.read(img("wall_batch_one"))
    assert bar is not None
    assert bar.add10.active and bar.add1.active  # plantilla y color coinciden: se pueden pulsar
    assert bar.remove is not None and not bar.remove.active  # lote en el mínimo: «−1» apagado
    assert bar.count == 1
    assert [u.resource for u in bar.upgrades] == ["gold"]
    assert bar.upgrade_for("gold").affordable is True and bar.upgrade_for("elixir") is None


def test_con_menos_de_diez_muros_posibles_solo_hay_un_add_wall_y_es_el_de_uno(reader):
    """Con `Wall ×8` el juego no ofrece «+10»: la barra tiene Remove, un único «Add Wall» y los dos «Upgrade» (oro y elixir)."""
    bar = reader.read(img("wall_batch_only_add_one"))
    assert bar is not None
    assert bar.add10 is None and bar.add1 is not None and bar.add1.active
    assert bar.remove is not None and bar.remove.center[0] < bar.add1.center[0]
    assert bar.count == 1
    assert [u.resource for u in bar.upgrades] == ["gold", "elixir"]


def test_e1r2_botones_de_anadir_apagados_no_se_pulsan(reader):
    bar = reader.read(img("wall_batch_grey"))
    assert bar is not None
    assert not bar.add10.active and not bar.add1.active  # no quedan muros de ese nivel
    assert bar.remove.active
    assert bar.count == 10


def test_los_botones_van_de_izquierda_a_derecha(reader):
    bar = reader.read(img("wall_batch_grey"))
    xs = [b.center[0] for b in (bar.remove, bar.add10, bar.add1)] + [u.center[0] for u in bar.upgrades]
    assert xs == sorted(xs)


@pytest.mark.parametrize("name", ["main_village", "wall_select_bar", "wall_selected", "upgrade_panel", "full_storage"])
def test_otras_pantallas_no_son_la_barra_de_lote(reader, name):
    assert reader.read(img(name)) is None


# --- el contador «x N» -----------------------------------------------------------------------------


def test_el_contador_se_lee_con_la_fuente_del_juego(reader):
    assert reader.count(img("wall_batch_one")) == 1
    assert reader.count(img("wall_batch_grey")) == 10


def test_sin_titulo_el_contador_es_none(reader):
    assert reader.count(img("main_village")) is None


# --- R3: el coste en rojo ---------------------------------------------------------------------------


def _paint_cost_red(image, upgrade_center):
    """Imagen sintética: el coste (texto blanco sobre «Upgrade») pasa a rojo."""
    out = image.copy()
    cx, cy = upgrade_center
    h = image.shape[0]
    y0, y1 = max(0, cy - int(0.085 * h)), cy - int(0.02 * h)
    patch = out[y0:y1, cx - 50 : cx + 50]
    white = patch.min(axis=2) > 228
    patch[white] = (250, 90, 80)
    return out


def test_e1r3_coste_en_rojo_no_es_asequible(reader):
    base = img("wall_batch_grey")
    center = reader.read(base).upgrades[0].center
    red = _paint_cost_red(base, center)
    bar = reader.read(red)
    assert bar is not None and bar.upgrades[0].affordable is False


def test_un_fondo_salmon_tras_el_boton_translucido_no_es_un_coste_en_rojo(reader):
    """Con tejados o barriles detrás, el botón deja ver tonos salmón (212,134,105): el coste blanco sigue siendo asequible."""
    base = img("wall_batch_grey")
    cx, cy = reader.read(base).upgrades[0].center
    h = base.shape[0]
    out = base.copy()
    patch = out[max(0, cy - int(0.085 * h)) : cy - int(0.02 * h), cx - 50 : cx + 50]
    patch[patch.min(axis=2) <= 228] = (212, 134, 105)  # todo lo que no es texto blanco: fondo salmón
    bar = reader.read(out)
    assert bar is not None and bar.upgrades[0].affordable is True


def test_region_sin_texto_no_se_sabe():
    blank = np.full((720, 1280, 3), 90, np.uint8)
    assert cost_affordable(blank, Rect(100, 100, 80, 40)) is None


# --- «Upgrade More» en la barra del muro seleccionado ------------------------------------------------


@pytest.mark.parametrize(
    "name, x_range",
    [("wall_selected", (500, 540)), ("wall_select_bar", (670, 715)), ("wall_select_bar_elixir", (620, 665))],
)
def test_upgrade_more_se_localiza_en_la_barra_de_seleccion(reader, name, x_range):
    point = reader.upgrade_more(img(name))
    assert point is not None
    assert x_range[0] <= point[0] <= x_range[1]
    assert 540 <= point[1] <= 600  # dentro del botón, no sobre la etiqueta de abajo


@pytest.mark.parametrize("name", ["wall_batch_one", "wall_batch_grey", "main_village", "upgrade_panel"])
def test_upgrade_more_no_aparece_en_otras_pantallas(reader, name):
    assert reader.upgrade_more(img(name)) is None


# --- el modal «Upgrade Walls» --------------------------------------------------------------------------


def test_el_modal_de_lote_se_reconoce_con_okay_a_la_derecha(reader):
    modal = reader.modal(img("wall_batch_modal"))
    assert modal is not None
    assert modal.cancel[0] < modal.okay[0]
    assert 640 <= modal.okay[0] <= 870 and 400 <= modal.okay[1] <= 500
    assert 500 <= modal.cancel[0] <= 540 or modal.cancel[0] < 640


def test_el_modal_no_aparece_en_ninguna_otra_pantalla(reader):
    from pathlib import Path

    from helpers import FIXTURES

    for path in sorted(Path(FIXTURES).glob("*.png")):
        if path.stem in ("wall_batch_modal", "wall_batch_modal_elixir"):
            continue
        assert reader.modal(img(path.stem)) is None, path.stem


def test_el_modal_con_elixir_es_el_mismo_que_con_oro(reader):
    # «Walls for 400000 Elixir?»: mismos botones; el recurso no se lee (va en otra fuente).
    gold, elixir = reader.modal(img("wall_batch_modal")), reader.modal(img("wall_batch_modal_elixir"))
    assert gold is not None and elixir is not None
    assert gold.okay == elixir.okay and gold.cancel == elixir.cancel


def test_el_dialogo_de_un_muro_con_elixir_no_es_el_modal_de_lote(reader):
    assert reader.modal(img("wall_dialog_elixir")) is None


# --- oro y elixir: dos botones «Upgrade» con el icono del recurso ------------------------------------------


def test_con_muros_de_nivel_alto_hay_un_boton_de_oro_y_otro_de_elixir(reader):
    bar = reader.read(img("wall_batch_bar_elixir"))
    assert bar is not None and bar.count == 1
    assert [u.resource for u in bar.upgrades] == ["gold", "elixir"]  # moneda a la izquierda, gota a la derecha
    gold, elixir = bar.upgrade_for("gold"), bar.upgrade_for("elixir")
    assert gold.center[0] < elixir.center[0]
    assert gold.affordable is True and elixir.affordable is True
    assert bar.add10.active and bar.add1.active and not bar.remove.active  # lote en el mínimo


def test_el_icono_decide_el_recurso_aunque_el_numero_cambie(reader):
    # Las plantillas son solo la moneda y la gota: no dependen de la cifra (100 000, 10 000, 1 000...).
    casos = [("wall_batch_one", ["gold"]), ("wall_batch_grey", ["gold"]), ("wall_batch_bar_elixir", ["gold", "elixir"])]
    for name, expected in casos:
        assert [u.resource for u in reader.read(img(name)).upgrades] == expected, name


def test_sin_icono_reconocible_no_se_adivina_el_recurso(reader):
    image = img("wall_batch_bar_elixir")
    u = reader.read(image).upgrades[0]
    blank = image.copy()
    blank[500:560, u.center[0] - 40 : u.center[0] + 60] = (150, 140, 120)  # borra el coste y su icono
    found = reader.read(blank)
    assert found is None or found.upgrade_for("gold") is None


def test_el_boton_del_otro_recurso_no_se_toma_por_el_pedido(reader):
    assert reader.read(img("wall_batch_one")).upgrade_for("elixir") is None  # solo hay moneda
