"""Lectores de estado (issue #5) sobre fotogramas sintéticos.

Los recortes y tamaños son provisionales (ver screens/layout.py): estas pruebas comprueban la
lógica de lectura, acuerdo y cordura, no la calibración contra la ventana real del juego.
"""

import numpy as np
import pytest

from pyclash.device.base import Frame
from pyclash.runtime.stop import StopRequested
from pyclash.screens.layout import DEFAULT_LAYOUT, scaled_font_px
from pyclash.screens.stable import stable_fields, stable_read
from pyclash.screens.village import StorageIcon, VillageReader, parse_fraction
from pyclash.vision.text import render_text

from helpers import frame as blank_frame, make_ctx

W, H = 1920, 1080
BG = (40, 60, 110)


def screen():
    img = np.empty((H, W, 3), np.uint8)
    img[:] = BG
    return img


def paste(img, patch, x, y):
    h, w = patch.shape[:2]
    ink = patch.max(axis=2) > 0
    img[y:y + h, x:x + w][ink] = patch[ink]


def put_chip(img, strip, text, slot=0):
    """Un chip en la franja: los chips se colocan de izquierda a derecha, con un hueco entre ellos."""
    r = strip.rect(img)
    size = scaled_font_px(img, DEFAULT_LAYOUT.chip_font_px)
    paste(img, render_text(text, size), r.x + 4 + slot * size * 4, r.y + 4)


def put_hud(img, gold, elixir):
    size = scaled_font_px(img, DEFAULT_LAYOUT.hud_font_px)
    for roi, value in ((DEFAULT_LAYOUT.gold_bar, gold), (DEFAULT_LAYOUT.elixir_bar, elixir)):
        r = roi.rect(img)
        paste(img, render_text(str(value), size), r.x + 20, r.y + 8)


def as_frame(img):
    return Frame(img, (W, H), 0.0)


def sequence(images):
    """capture() que recorre los fotogramas en orden y repite el último."""
    it = iter(images)
    last = [None]

    def capture():
        last[0] = next(it, last[0])
        return as_frame(last[0])

    return capture


# --- parse_fraction -----------------------------------------------------------------------

@pytest.mark.parametrize(
    "text, max_total, expected",
    [("2/5", 6, (2, 5)), ("0/5", 6, (0, 5)), ("25", 6, (2, 5)), ("8/8", 6, None), ("3/2", 6, None),
     ("0/0", 6, None), ("2/", 6, None), ("123", 6, None), ("/5", 6, None)],
)
def test_parse_fraction(text, max_total, expected):
    assert parse_fraction(text, max_total) == expected


# --- R3: el estado refleja los indicadores del juego --------------------------------------

def test_e1r3_todos_los_constructores_ocupados():
    img = screen()
    put_chip(img, DEFAULT_LAYOUT.chip_strip, "0/5")
    reader = VillageReader()
    assert reader.read_builders(img) == (0, 5)
    assert reader.read_state(img).free_builders == 0


def test_e2r3_laboratorio_libre_y_ocupado():
    reader = VillageReader()
    libre, ocupado = screen(), screen()
    for img, lab in ((libre, "1/1"), (ocupado, "0/1")):
        put_chip(img, DEFAULT_LAYOUT.chip_strip, "2/5")
        put_chip(img, DEFAULT_LAYOUT.chip_strip, lab, slot=1)
    assert reader.read_state(libre).lab_idle
    assert not reader.read_state(ocupado).lab_idle


def test_sin_indicador_es_none_y_no_cuenta_como_laboratorio_libre():
    state = VillageReader().read_state(screen())
    assert state.builders is None and state.lab is None and state.resources is None
    assert not state.lab_idle and state.free_builders is None


def test_chip_con_total_imposible_se_rechaza():
    img = screen()
    put_chip(img, DEFAULT_LAYOUT.chip_strip, "8/8")  # un diálogo filtrando dígitos
    assert VillageReader().read_builders(img) is None


def test_constructores_del_builder_base_tienen_otro_maximo():
    img = screen()
    put_chip(img, DEFAULT_LAYOUT.builder_base_strip, "1/2")
    assert VillageReader().read_builders(img, base="builder") == (1, 2)
    img2 = screen()
    put_chip(img2, DEFAULT_LAYOUT.builder_base_strip, "3/5")
    assert VillageReader().read_builders(img2, base="builder") is None


def test_recursos_del_hud():
    img = screen()
    put_hud(img, 1234567, 89000)
    assert VillageReader().read_resources(img) == (1234567, 89000, None)  # el oscuro no se lee por posición


def test_almacen_lleno_solo_con_icono_del_color_correcto():
    yellow = (240, 200, 40)
    icon_img = np.zeros((24, 24, 3), np.uint8)
    icon_img[4:20, 4:20] = yellow
    icon_img[8:12, 8:16] = (255, 255, 255)  # un detalle para que la plantilla tenga variación
    reader = VillageReader(storage_icons={"gold": StorageIcon(icon_img, yellow, min_color_fraction=0.3)})
    hud = DEFAULT_LAYOUT.hud.rect(screen())

    llena = screen()
    llena[hud.y + 10:hud.y + 34, hud.x + 10:hud.x + 34] = icon_img
    assert reader.storage_full(llena, "gold") is True
    assert reader.storage_full(screen(), "gold") is False
    assert reader.storage_full(llena, "elixir") is None  # sin icono configurado: no se adivina

    gris = screen()
    gris[hud.y + 10:hud.y + 34, hud.x + 10:hud.x + 34] = np.where(icon_img > 0, 150, 0).astype(np.uint8)
    assert reader.storage_full(gris, "gold") is False  # misma forma, color equivocado


# --- R1: una lectura dudosa no se da por buena --------------------------------------------

def test_e1r1_lecturas_coherentes_entregan_2_de_5():
    imgs = []
    for _ in range(3):
        img = screen()
        put_chip(img, DEFAULT_LAYOUT.chip_strip, "2/5")
        imgs.append(img)
    ctx = make_ctx([blank_frame()])
    state = VillageReader().read_state_stable(ctx, sequence(imgs))
    assert state.builders == (2, 5)
    assert "builders" not in state.unreliable


def test_e2r1_una_lectura_contradictoria_se_descarta_y_se_vuelve_a_leer():
    good, bad = screen(), screen()
    put_chip(good, DEFAULT_LAYOUT.chip_strip, "2/5")
    put_chip(bad, DEFAULT_LAYOUT.chip_strip, "6/6")
    ctx = make_ctx([blank_frame()])
    state = VillageReader().read_state_stable(ctx, sequence([good, good, bad, good, good, good]))
    assert state.builders == (2, 5)


def test_e2r1_sin_acuerdo_el_estado_no_es_fiable():
    a, b = screen(), screen()
    put_chip(a, DEFAULT_LAYOUT.chip_strip, "2/5")
    put_chip(b, DEFAULT_LAYOUT.chip_strip, "3/5")
    ctx = make_ctx([blank_frame()])
    state = VillageReader().read_state_stable(ctx, sequence([a, b, a, b, a, b]))
    assert state.builders is None
    assert "builders" in state.unreliable and not state.reliable


def test_cada_campo_se_asienta_por_separado():
    img = screen()
    put_chip(img, DEFAULT_LAYOUT.chip_strip, "2/5")  # sin chip de laboratorio
    state = VillageReader().read_state_stable(make_ctx([blank_frame()]), sequence([img] * 6))
    assert state.builders == (2, 5)
    assert state.unreliable == {"lab", "resources"}  # los almacenes sin icono no se esperan


def test_stable_fields_para_tras_asentar_todo():
    calls = []
    ctx = make_ctx([blank_frame()])

    def read(f):
        calls.append(1)
        return 7

    out = stable_fields(ctx, {"x": read}, lambda: blank_frame(), required=3, attempts=10)
    assert out["x"].reliable and out["x"].captures == 3 and len(calls) == 3


def test_stable_read_una_lectura_ilegible_reinicia_la_racha():
    values = iter([2, None, 2, 2, 2])
    ctx = make_ctx([blank_frame()])
    r = stable_read(ctx, lambda f: next(values), lambda: blank_frame(), required=3, attempts=5)
    assert r.reliable and r.value == 2 and r.captures == 5


def test_stable_respeta_la_parada():
    ctx = make_ctx([blank_frame()])
    ctx.stop.request()
    with pytest.raises(StopRequested):
        stable_read(ctx, lambda f: 1, lambda: blank_frame())


def test_stable_valida_parametros():
    with pytest.raises(ValueError):
        stable_read(make_ctx([blank_frame()]), lambda f: 1, lambda: blank_frame(), required=3, attempts=2)


# --- sobre una captura real (anonimizada), no sintética -------------------------------------

def test_aldea_real_1280x720_se_lee_con_el_layout_calibrado():
    from helpers import fixture_frame

    image = fixture_frame("main_village").image
    reader = VillageReader()
    assert reader.read_builders(image) == (2, 2)
    assert reader.read_lab(image) is None  # esa cuenta solo tiene el chip de constructores
    assert reader.read_resources(image) == (3354, 10740, None)
