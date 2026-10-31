"""Clasificador de pantallas y vías de escape (issue #6), sobre capturas reales anonimizadas.

Las capturas son de la ventana de Google Play Games reducida a 1280×720; las firmas se midieron
sobre el original (1763×992), así que además se comprueba que no dependen del tamaño exacto.
"""

import numpy as np
import pytest

from pyclash.device.base import Rect
from pyclash.screens.classifier import (
    Screen,
    ScreenClassifier,
    ScreenSignature,
    TextIndicator,
)
from pyclash.screens.escapes import find_escape
from pyclash.screens.signatures import DEFAULT_SIGNATURES, box
from pyclash.vision.buttons import find_close_buttons, find_empty_terrain, is_blank
from pyclash.vision.color import find_green_buttons
from pyclash.vision.text import render_text

from helpers import fixture_frame

# fixture -> pantalla esperada. El menú de filtros es un diálogo que el clasificador NO conoce.
EXPECTED = {
    "main_village": Screen.MAIN_VILLAGE,
    "placing_building": Screen.PLACING_BUILDING,
    "upgrade_panel": Screen.UPGRADE_PANEL,
    "upgrade_panel_filtered": Screen.UPGRADE_PANEL,
    "attack_menu": Screen.ATTACK_MENU,
    "army_selection": Screen.ARMY_SELECTION,
    "finding_opponent": Screen.FINDING_OPPONENT,
    "finding_opponent_red_zone": Screen.FINDING_OPPONENT,
    "battle": Screen.BATTLE,
    "confirm_surrender": Screen.CONFIRM_SURRENDER,
    "need_more_gold_search": Screen.NEED_MORE_GOLD,
    "scouting_barbarians": Screen.FINDING_OPPONENT,
    "scouting_base": Screen.FINDING_OPPONENT,
    "scouting_split_zone": Screen.FINDING_OPPONENT,
    "attacking_no_more_barbarians": Screen.BATTLE,
    "result": Screen.RESULT,
    "unknown_filter_menu": Screen.UNKNOWN,
}


@pytest.fixture(scope="module")
def classifier():
    return ScreenClassifier()


def image_of(name):
    return fixture_frame(name).image


@pytest.mark.parametrize("name,expected", EXPECTED.items())
def test_each_screen_is_classified(classifier, name, expected):
    got = classifier.classify(image_of(name))
    assert got.screen is expected, f"{name}: {got}"
    if expected is not Screen.UNKNOWN:
        assert got.confidence > 0.4


@pytest.mark.parametrize("name,expected", [(n, s) for n, s in EXPECTED.items() if s is not Screen.UNKNOWN])
@pytest.mark.parametrize("scale", [0.8, 1.5])
def test_classification_survives_window_resize(classifier, name, expected, scale):
    import cv2

    image = image_of(name)
    h, w = image.shape[:2]
    resized = cv2.resize(image, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
    assert classifier.classify(resized).screen is expected


# --- R1 -----------------------------------------------------------------------------------------


def test_unknown_dialog_is_not_forced_to_the_most_similar(classifier):
    """E2R1: el menú de filtros tiene el panel de mejoras debajo, pero no es ninguna pantalla conocida."""
    got = classifier.classify(image_of("unknown_filter_menu"))
    assert got.screen is Screen.UNKNOWN
    assert got.candidates == ()


def test_ambiguous_signatures_of_equal_priority_give_unknown():
    word = TextIndicator("Battle", box(0.0, 0.89, 0.11, 0.99), 28)
    a, b = ScreenSignature(Screen.SHOP, (word,)), ScreenSignature(Screen.LAB_MENU, (word,))
    got = ScreenClassifier(signatures=(a, b)).classify(image_of("main_village"))
    assert got.screen is Screen.UNKNOWN
    assert "ambiguas" in got.reason


def test_higher_priority_signature_wins_over_the_village_it_covers(classifier):
    got = classifier.classify(image_of("placing_building"))
    assert {s for s, _ in got.candidates} == {Screen.MAIN_VILLAGE, Screen.PLACING_BUILDING}
    assert got.screen is Screen.PLACING_BUILDING


def test_blank_capture_is_detected(classifier):
    assert classifier.classify(np.zeros((720, 1280, 3), np.uint8)).screen is Screen.BLANK
    assert is_blank(np.full((10, 10, 3), 3, np.uint8))
    assert not is_blank(image_of("main_village"))


def test_canary_names_the_screen_and_the_indicator_that_stopped_matching(classifier):
    """E3R1: una interfaz distinta falla con un mensaje que dice qué dejó de reconocerse."""
    image = image_of("main_village").copy()
    h, w = image.shape[:2]
    image[int(h * 0.89):, : int(w * 0.11)] = 0  # sin el botón «Battle», como en otra generación de UI
    message = classifier.explain(image, Screen.MAIN_VILLAGE)
    assert message is not None
    assert "main_village" in message and "Battle" in message
    assert classifier.explain(image_of("main_village"), Screen.MAIN_VILLAGE) is None


def test_every_default_signature_is_exercised_by_a_fixture():
    covered = {s for s in EXPECTED.values()}
    assert {sig.screen for sig in DEFAULT_SIGNATURES} <= covered


# --- detectores de salida segura ---------------------------------------------------------------


def test_close_button_found_where_the_game_draws_it():
    image = image_of("upgrade_panel")
    h, w = image.shape[:2]
    found = find_close_buttons(image)
    assert len(found) == 1
    cx, cy = found[0].center
    assert cx > 0.95 * w and cy < 0.1 * h


def test_close_button_found_even_when_dimmed_by_the_filter_popup():
    assert len(find_close_buttons(image_of("unknown_filter_menu"))) == 1


@pytest.mark.parametrize("name", ["main_village", "battle", "finding_opponent", "result", "confirm_surrender", "attack_menu"])
def test_no_false_close_button_on_screens_without_one(name):
    """El tejado rojo del cuartel, las insignias de aviso y los botones rojos anchos no valen."""
    assert find_close_buttons(image_of(name)) == []


def test_empty_terrain_is_a_flat_grass_square():
    image = image_of("main_village")
    found = find_empty_terrain(image)
    assert found is not None
    patch = image[found.rect.y:found.rect.y + found.rect.h, found.rect.x:found.rect.x + found.rect.w]
    r, g, b = (patch[..., i].astype(float).mean() for i in range(3))
    assert g > r + 10 and g > b + 60  # verde amarillento, no un botón ni un edificio


@pytest.mark.parametrize("name", ["army_selection", "confirm_surrender", "result"])
def test_no_terrain_under_a_dialog(name):
    """Terreno oscurecido por un diálogo, o cubierto por él, no cuenta como terreno vacío."""
    assert find_empty_terrain(image_of(name), box(0.15, 0.15, 0.85, 0.80).rect(image_of(name))) is None


def test_a_green_button_is_not_terrain():
    img = np.full((720, 1280, 3), (70, 50, 40), np.uint8)
    img[300:420, 400:800] = (110, 200, 30)  # el verde de los botones
    assert find_empty_terrain(img) is None


# --- vías de escape ----------------------------------------------------------------------------


def contains(rect: Rect, tap) -> bool:
    return rect.contains(tap.x, tap.y)


def test_escape_of_a_panel_is_its_close_button(classifier):
    image = image_of("upgrade_panel")
    tap = find_escape(image, Screen.UPGRADE_PANEL, classifier.finder)
    assert tap is not None and contains(find_close_buttons(image)[0].rect, tap)


def test_escape_of_the_result_is_the_green_return_button(classifier):
    image = image_of("result")
    tap = find_escape(image, Screen.RESULT, classifier.finder)
    assert tap is not None and contains(find_green_buttons(image)[0].rect, tap)


def test_confirm_dialog_cancels_unless_surrender_is_allowed(classifier):
    image = image_of("confirm_surrender")
    h, w = image.shape[:2]
    cancel = find_escape(image, Screen.CONFIRM_SURRENDER, classifier.finder)
    okay = find_escape(image, Screen.CONFIRM_SURRENDER, classifier.finder, allow_surrender=True)
    assert cancel.x < w / 2 < okay.x  # «Cancel» a la izquierda, «Okay» a la derecha
    assert abs(cancel.y - okay.y) < 0.05 * h


def test_battle_has_no_escape_without_permission_to_surrender(classifier):
    image = image_of("battle")
    assert find_escape(image, Screen.BATTLE, classifier.finder) is None
    assert find_escape(image, Screen.BATTLE, classifier.finder, allow_surrender=True) is not None


def test_login_and_blank_have_no_escape(classifier):
    for screen in (Screen.LOGIN, Screen.BLANK, Screen.MAIN_VILLAGE):
        assert find_escape(image_of("main_village"), screen, classifier.finder) is None


def test_text_font_size_matches_at_the_scaled_height():
    """Las firmas usan la fuente del juego: «Battle» renderizado debe casar en la aldea real."""
    from pyclash.screens.layout import scaled_font_px

    image = image_of("main_village")
    assert scaled_font_px(image, 28) == 14
    assert render_text("Battle", 14).shape[0] > 5
