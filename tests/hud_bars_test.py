"""Barras del HUD (issue #13): cuánto de la barra de oro y elixir está relleno, y cuándo cuenta como llena."""

import pytest

from pyclash.policy.walls import bar_is_full
from pyclash.screens.hud_bars import read_fill

from helpers import fixture_frame


def img(name):
    return fixture_frame(name).image.copy()


def test_oro_lleno_y_elixir_a_medias():
    gold, elixir = read_fill(img("full_storage"), "gold"), read_fill(img("full_storage"), "elixir")
    assert gold >= 0.95 and bar_is_full(gold) is True  # barra amarilla de punta a punta
    assert 0.6 <= elixir <= 0.8 and bar_is_full(elixir) is False


def test_barras_a_medias_no_cuentan_como_llenas():
    gold, elixir = read_fill(img("wall_batch_grey"), "gold"), read_fill(img("wall_batch_grey"), "elixir")
    assert 0.75 <= gold <= 0.9 and bar_is_full(gold) is False
    assert elixir <= 0.1 and bar_is_full(elixir) is False


@pytest.mark.parametrize("name", ["wall_batch_one", "wall_selected", "upgrade_panel"])
def test_con_un_panel_abierto_no_se_sabe(name):
    # El panel «Buildings & Heroes» tapa el HUD: nunca cuenta como lleno ni como vacío.
    assert read_fill(img(name), "gold") is None and read_fill(img(name), "elixir") is None
    assert bar_is_full(read_fill(img(name), "gold")) is None


def test_aldea_normal_se_lee_sin_estar_llena():
    gold = read_fill(img("main_village"), "gold")
    assert gold is not None and not bar_is_full(gold)
