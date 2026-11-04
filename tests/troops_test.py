from pyclash.policy.armies import ARMIES, Army, troop_name
from pyclash.screens.battle_bar import BarReader
from pyclash.screens.troops import TroopPortraits, check_army

from helpers import fixture_frame

BAR = BarReader()
PORTRAITS = TroopPortraits.load_packaged()

BARBARIANS = ARMIES["barbarians"]
BARBARIANS_AND_ARCHERS = Army("barbarians_archers", "Bárbaros y Arqueras", ("barbarian", "archer"))


def test_packaged_portraits_are_available():
    assert {"barbarian", "archer"} <= set(PORTRAITS.portraits)


def identify(name, index=0):
    image = fixture_frame(name).image
    cards = BAR.detect(image)
    return PORTRAITS.identify(image, cards[index].rect)


def test_barbarian_card_is_identified():
    ident = identify("scouting_barbarians")
    assert ident.troop == "barbarian"
    assert ident.score >= 0.9 and ident.score - ident.runner_up >= 0.3  # medido: 0.95-0.98 frente a ~0.45


def test_both_cards_of_a_two_troop_bar_are_told_apart():
    assert identify("two_troops_bar", 0).troop == "barbarian"
    assert identify("two_troops_bar", 1).troop == "archer"


def test_a_card_that_matches_no_portrait_is_not_identified():
    # La carta agotada es gris: no casa con ningún retrato.
    assert identify("attacking_no_more_barbarians").troop is None


def check(name, army):
    image = fixture_frame(name).image
    return check_army(image, BAR.detect(image), army, PORTRAITS)


def test_barbarians_army_is_found_in_the_bar():
    result = check("scouting_barbarians", BARBARIANS)
    assert result.ok and set(result.cards) == {"barbarian"}


def test_two_troop_army_is_found_in_order():
    result = check("two_troops_bar", BARBARIANS_AND_ARCHERS)
    assert result.ok
    assert result.cards["barbarian"].rect.x < result.cards["archer"].rect.x


def test_missing_troop_fails_with_a_clear_message():
    result = check("scouting_barbarians", BARBARIANS_AND_ARCHERS)
    assert not result.ok
    assert result.reason == "Troop Archer not found"


def test_extra_known_troops_are_reported_but_do_not_fail():
    result = check("two_troops_bar", BARBARIANS)
    assert result.ok and result.others == ("archer",)


def test_empty_bar_counts_as_missing_troops():
    result = check("main_village", BARBARIANS)
    assert not result.ok and result.reason == "Troop Barbarian not found"


def test_troop_without_a_portrait_gets_a_readable_name():
    assert troop_name("barbarian") == "Barbarian"
    assert troop_name("wall_breaker") == "Wall Breaker"
