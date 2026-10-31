import pytest

from pyclash.policy.deploy_path import inside
from pyclash.screens.red_zone import read_red_zone

from helpers import fixture_frame

# Centro del Ayuntamiento en cada captura (1280x720): debe quedar dentro de la zona roja.
VILLAGES = {
    "scouting_barbarians": (640, 360),
    "scouting_base": (635, 342),
    "finding_opponent_red_zone": (652, 318),
    "finding_opponent": (672, 330),
    "scouting_split_zone": (645, 240),
}
# Pantallas sin zona roja: batalla ya empezada, aldea propia, menús, resultado y la exploración del diálogo de oro.
NO_ZONE = ["battle", "attacking_no_more_barbarians", "main_village", "result", "attack_menu", "need_more_gold_search", "army_selection"]


@pytest.mark.parametrize("name", VILLAGES)
def test_village_zone_is_found_and_contains_the_town_hall(name):
    reading = read_red_zone(fixture_frame(name).image)
    assert reading.zone is not None, reading.reason
    hull = [tuple(p) for p in reading.zone.hull]
    assert inside(hull, *VILLAGES[name])
    assert reading.zone.ratio >= 3.5


@pytest.mark.parametrize("name", NO_ZONE)
def test_screens_without_a_red_zone_give_no_zone_and_a_reason(name):
    reading = read_red_zone(fixture_frame(name).image)
    assert reading.zone is None
    assert reading.reason


def test_the_zone_is_the_village_not_the_small_detached_outline():
    # scouting_barbarians tiene otro contorno pequeño abajo a la izquierda (un edificio suelto, ~(550, 490)).
    zone = read_red_zone(fixture_frame("scouting_barbarians").image).zone
    assert not inside([tuple(p) for p in zone.hull], 550, 490)


def test_a_village_whose_outline_comes_out_in_two_big_pieces_is_still_one_village():
    # En esta captura el contorno se interrumpe tras unos árboles: salen dos trozos casi iguales (arriba y
    # abajo a la izquierda) que antes se tomaban por una lectura ambigua.
    zone = read_red_zone(fixture_frame("scouting_split_zone").image).zone
    hull = [tuple(map(float, p)) for p in zone.hull]
    assert inside(hull, 700, 200) and inside(hull, 500, 440)  # los dos trozos caen dentro de la envolvente


def test_the_zone_stays_clear_of_the_interface():
    zone = read_red_zone(fixture_frame("scouting_barbarians").image).zone
    xs = [x for x, _ in zone.hull]
    ys = [y for _, y in zone.hull]
    assert min(ys) > 0.10 * 720 and max(ys) < 0.845 * 720  # ni cuenta atrás ni barra de cartas
    assert 0 < min(xs) and max(xs) < 1280
