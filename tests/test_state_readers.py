"""Lectores de estado (issue #5) sobre fotogramas sintéticos.

Los recortes y tamaños son provisionales (ver screens/layout.py): estas pruebas comprueban la
lógica de lectura, acuerdo y cordura, no la calibración contra la ventana real del juego.
"""

import numpy as np
import pytest

from pyclash.device.base import Frame, Rect
from pyclash.policy.loot import LootLimits, LootTracker
from pyclash.runtime.stop import StopRequested
from pyclash.screens.layout import DEFAULT_LAYOUT, scaled_font_px
from pyclash.screens.loot import read_enemy_loot
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


def put_chip(img, roi, text):
    r = roi.rect(img)
    paste(img, render_text(text, scaled_font_px(img, DEFAULT_LAYOUT.chip_font_px)), r.x + 4, r.y + 4)


def put_hud(img, values):
    hud = DEFAULT_LAYOUT.hud.rect(img)
    band = hud.h // 3
    size = scaled_font_px(img, DEFAULT_LAYOUT.hud_font_px)
    for i, v in enumerate(values):
        paste(img, render_text(str(v), size), hud.x + 20, hud.y + i * band + 8)


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
    put_chip(img, DEFAULT_LAYOUT.builder_chip, "0/5")
    reader = VillageReader()
    assert reader.read_builders(img) == (0, 5)
    assert reader.read_state(img).free_builders == 0


def test_e2r3_laboratorio_libre_y_ocupado():
    reader = VillageReader()
    libre, ocupado = screen(), screen()
    put_chip(libre, DEFAULT_LAYOUT.lab_chip, "1/1")
    put_chip(ocupado, DEFAULT_LAYOUT.lab_chip, "0/1")
    assert reader.read_state(libre).lab_idle
    assert not reader.read_state(ocupado).lab_idle


def test_sin_indicador_es_none_y_no_cuenta_como_laboratorio_libre():
    state = VillageReader().read_state(screen())
    assert state.builders is None and state.lab is None and state.resources is None
    assert not state.lab_idle and state.free_builders is None


def test_chip_con_total_imposible_se_rechaza():
    img = screen()
    put_chip(img, DEFAULT_LAYOUT.builder_chip, "8/8")  # un diálogo filtrando dígitos
    assert VillageReader().read_builders(img) is None


def test_constructores_del_builder_base_tienen_otro_maximo():
    img = screen()
    put_chip(img, DEFAULT_LAYOUT.builder_base_chip, "1/2")
    assert VillageReader().read_builders(img, base="builder") == (1, 2)
    img2 = screen()
    put_chip(img2, DEFAULT_LAYOUT.builder_base_chip, "3/5")
    assert VillageReader().read_builders(img2, base="builder") is None


def test_recursos_del_hud():
    img = screen()
    put_hud(img, [1234567, 89000, 4210])
    assert VillageReader().read_resources(img) == (1234567, 89000, 4210)


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
        put_chip(img, DEFAULT_LAYOUT.builder_chip, "2/5")
        imgs.append(img)
    ctx = make_ctx([blank_frame()])
    state = VillageReader().read_state_stable(ctx, sequence(imgs))
    assert state.builders == (2, 5)
    assert "builders" not in state.unreliable


def test_e2r1_una_lectura_contradictoria_se_descarta_y_se_vuelve_a_leer():
    good, bad = screen(), screen()
    put_chip(good, DEFAULT_LAYOUT.builder_chip, "2/5")
    put_chip(bad, DEFAULT_LAYOUT.builder_chip, "6/6")
    ctx = make_ctx([blank_frame()])
    state = VillageReader().read_state_stable(ctx, sequence([good, good, bad, good, good, good]))
    assert state.builders == (2, 5)


def test_e2r1_sin_acuerdo_el_estado_no_es_fiable():
    a, b = screen(), screen()
    put_chip(a, DEFAULT_LAYOUT.builder_chip, "2/5")
    put_chip(b, DEFAULT_LAYOUT.builder_chip, "3/5")
    ctx = make_ctx([blank_frame()])
    state = VillageReader().read_state_stable(ctx, sequence([a, b, a, b, a, b]))
    assert state.builders is None
    assert "builders" in state.unreliable and not state.reliable


def test_cada_campo_se_asienta_por_separado():
    img = screen()
    put_chip(img, DEFAULT_LAYOUT.builder_chip, "2/5")  # sin chip de laboratorio
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


# --- R2: el botín leído es verosímil ------------------------------------------------------

def test_e1r2_ganancia_normal():
    t = LootTracker()
    assert t.update((120_000, 100_000, 5_000)).status == "baseline"
    u = t.update((185_000, 100_000, 5_000))
    assert u.status == "ok" and u.gain == (65_000, 0, 0) and t.total == (65_000, 0, 0)


def test_e2r2_lectura_inflada_se_descarta_con_aviso_y_no_envenena_la_base():
    t = LootTracker()
    t.update((120_000, 100_000, 5_000))
    u = t.update((91_000_000, 100_000, 5_000))
    assert u.status == "discarded" and u.discarded == ("oro",) and u.gain == (0, 0, 0)
    assert "descartada" in u.warnings[0]
    assert t.baseline == (120_000, 100_000, 5_000)
    u = t.update((185_000, 100_000, 5_000))  # la siguiente lectura buena sigue comparándose bien
    assert u.gain == (65_000, 0, 0)


def test_lectura_inverosimil_que_se_repite_es_real_y_se_adopta_sin_contarla():
    t = LootTracker()
    t.update((120_000, 0, 0))
    t.update((9_000_000, 0, 0))
    u = t.update((9_000_000, 0, 0))
    assert u.status == "rebased" and u.gain == (0, 0, 0) and t.baseline[0] == 9_000_000
    assert t.total == (0, 0, 0)


def test_e3r2_almacen_lleno_no_da_botin():
    t = LootTracker()
    t.update((20_000_000, 20_000_000, 0))
    u = t.update((20_000_000, 20_000_000, 0))
    assert u.status == "ok" and u.gain == (0, 0, 0) and t.total == (0, 0, 0)


def test_gastar_en_mejoras_no_resta_ni_cuenta():
    t = LootTracker()
    t.update((500_000, 300_000, 0))
    u = t.update((200_000, 450_000, 0))  # gasto de oro y ganancia de elixir
    assert u.gain == (0, 150_000, 0) and t.baseline == (200_000, 450_000, 0)


def test_limites_configurables_y_el_oscuro_tiene_su_propio_tope():
    t = LootTracker(LootLimits(max_gain_main=1000, max_gain_dark=10))
    t.update((0, 0, 0))
    u = t.update((500, 500, 50))
    assert u.gain == (500, 500, 0) and u.discarded == ("elixir oscuro",)


# --- botín del rival ----------------------------------------------------------------------

def put_enemy(img, roi, values):
    band = roi.h // 3
    size = scaled_font_px(img, 30)
    for i, v in enumerate(values):
        if v is not None:
            paste(img, render_text(str(v), size), roi.x + 10, roi.y + i * band + 6)


ROI = Rect(100, 300, 400, 210)


def test_botin_del_rival():
    img = screen()
    put_enemy(img, ROI, [812345, 790000, 4200])
    loot = read_enemy_loot(img, ROI)
    assert loot is not None and loot.amounts == (812345, 790000, 4200) and loot.confidence > 0.75


def test_fila_de_oscuro_vacia_vale_cero():
    img = screen()
    put_enemy(img, ROI, [300000, 250000, None])
    assert read_enemy_loot(img, ROI).amounts == (300000, 250000, 0)


def test_sin_oro_o_elixir_no_hay_lectura():
    img = screen()
    put_enemy(img, ROI, [None, 250000, 100])
    assert read_enemy_loot(img, ROI) is None


def test_botin_del_rival_imposible_se_rechaza():
    img = screen()
    put_enemy(img, ROI, [91000000, 250000, 100])
    assert read_enemy_loot(img, ROI) is None
