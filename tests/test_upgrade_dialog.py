"""Lector de diálogos de mejora sobre capturas reales anonimizadas (issue #9)."""

import pytest

from pyclash.screens.upgrade_dialog import DialogReader, usable_confirm

from helpers import fixture_frame
from test_upgrade_flow import CachingDialogs


@pytest.fixture(scope="module")
def reader():
    return CachingDialogs()


def image(name):
    return fixture_frame(name).image


@pytest.mark.parametrize(
    "fixture, name, kind",
    [("upgrade_dialog", "Archer Tower", "upgrade"), ("wall_upgrade_dialog", "Wall", "upgrade"),
     ("rebuild_clan_castle", "Clan Castle", "rebuild")],
)
def test_title_names_the_chosen_row(reader, fixture, name, kind):
    title = reader.title(image(fixture), name)
    assert title is not None and title.kind == kind


@pytest.mark.parametrize(
    "fixture, wrong",
    [("upgrade_dialog", "Cannon"), ("upgrade_dialog", "Archer Queen"), ("upgrade_dialog", "Mortar"),
     ("wall_upgrade_dialog", "Cannon"), ("rebuild_clan_castle", "Barracks"), ("missing_gems", "Wall")],
)
def test_title_of_another_name_is_rejected(reader, fixture, wrong):  # E3R1
    assert reader.title(image(fixture), wrong) is None


@pytest.mark.parametrize(
    "fixture, resource, cost",
    [("upgrade_dialog", "gold", 2000), ("wall_upgrade_dialog", "gold", 1000), ("rebuild_clan_castle", "elixir", 10000)],
)
def test_confirm_button_has_a_known_resource_and_its_cost(reader, fixture, resource, cost):
    button = usable_confirm(reader.confirm_buttons(image(fixture)))
    assert button is not None and (button.resource, button.cost) == (resource, cost)


def test_the_enter_shop_button_is_not_a_usable_confirm(reader):  # E2R1
    assert usable_confirm(reader.confirm_buttons(image("missing_gems"))) is None
    assert reader.missing(image("missing_gems")) == "gems"


def test_other_screens_are_not_a_missing_dialog(reader):
    assert all(reader.missing(image(n)) is None for n in ("upgrade_dialog", "main_village", "upgrade_panel"))


def test_wall_menu_finds_upgrade_and_not_upgrade_more(reader):
    found = reader.wall_upgrade(image("wall_selected"))
    assert found is not None and 600 <= found.center[0] <= 650  # «Upgrade More» queda a su izquierda
    assert reader.wall_upgrade(image("main_village")) is None


def test_two_usable_buttons_are_never_guessed():
    from pyclash.device.base import Rect
    from pyclash.screens.upgrade_dialog import ConfirmButton

    a = ConfirmButton(Rect(0, 0, 10, 5), "gold", 100, 0.9)
    assert usable_confirm([a, ConfirmButton(Rect(20, 0, 10, 5), "elixir", 100, 0.9)]) is None
    assert usable_confirm([a, ConfirmButton(Rect(20, 0, 10, 5), None, None, 0.0)]) is a


def test_a_discounted_price_is_read_in_green_not_the_struck_original(reader):
    """«Upgrade Barracks» con Hammer of Building: 60 000 en verde y 120 000 tachado debajo. El precio es el
    verde; el botón gris del Hammer (un objeto) nunca es candidato."""
    img = image("upgrade_dialog_discount")
    assert reader.title(img, "Barracks") is not None
    buttons = reader.confirm_buttons(img)
    assert len(buttons) == 1  # solo el verde: el del Hammer no lo es
    button = usable_confirm(buttons)
    assert button is not None and (button.resource, button.cost) == ("elixir", 60000)
    h, w = img.shape[:2]
    assert button.center[0] > 0.6 * w  # el de la derecha, no el del Hammer
