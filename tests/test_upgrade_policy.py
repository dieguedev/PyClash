"""Política de mejoras: tablas de casos con azar semillado (lógica pura, sin visión ni dispositivo)."""

import random

import pytest

from pyclash.policy.upgrades import (
    EXCLUDE_HEROES,
    Fallback,
    Idle,
    Mode,
    Order,
    Reason,
    UpgradeSettings,
    UpgradeState,
    decide_idle,
    decide_upgrade,
    validate_priorities,
)
from pyclash.screens.upgrade_panel import PanelRow
from pyclash.vision.vocabulary import Vocabulary

RICH = (10_000_000, 10_000_000, 1_000_000)


def row(name, cost=1000, resource="gold", kind="upgrade", affordable=True, free=False, y=0):
    if free:
        cost, resource, affordable = None, None, None
    return PanelRow(
        name=name, quantity=1, cost=cost, resource=resource, free=free, new=False, duration=None,
        affordable=affordable, kind=kind, section="other", center=(0, y), y=y,
    )


def state(*rows, builders=3, resources=RICH, **kw):
    return UpgradeState(rows=rows, resources=resources, free_builders=builders, **kw)


def decide(st, seed=1, **settings):
    return decide_upgrade(st, UpgradeSettings(**settings), random.Random(seed))


def name_of(decision):
    return decision.chosen.name if decision.chosen else None


# --------------------------------------------------------------------------- R1 prioridades

LEVELS = (("Laboratory", "Blacksmith"), ("Wall",))


def test_first_level_with_anything_available_wins():  # E1R1
    d = decide(state(row("Blacksmith", y=1), row("Wall", y=2)), priorities=LEVELS)
    assert name_of(d) == "Blacksmith" and d.tier == 0


def test_next_level_is_used_when_the_first_has_nothing_affordable():  # E2R1
    d = decide(state(row("Blacksmith", affordable=False, y=1), row("Wall", y=2)), priorities=LEVELS)
    assert name_of(d) == "Wall" and d.tier == 1
    assert d.why_not("Blacksmith") == [Reason.NOT_AFFORDABLE]


def test_fallback_any_applies_when_no_list_matches():  # E3R1
    d = decide(state(row("Cannon", 500), row("Mortar", 900)), priorities=LEVELS, fallback=Fallback.ANY)
    assert name_of(d) == "Mortar" and d.tier is None  # más caro primero


def test_fallback_none_does_nothing_when_no_list_matches():  # E3R1
    d = decide(state(row("Cannon")), priorities=LEVELS, fallback=Fallback.NONE)
    assert d.chosen is None and "alternativa" in d.reason


def test_choice_within_a_level_is_random_but_reproducible():
    st = state(*(row(n, y=i) for i, n in enumerate(("Laboratory", "Blacksmith"))))
    picks = {name_of(decide(st, seed=s, priorities=LEVELS)) for s in range(30)}
    assert picks == {"Laboratory", "Blacksmith"}  # el azar mueve la elección
    assert name_of(decide(st, seed=7, priorities=LEVELS)) == name_of(decide(st, seed=7, priorities=LEVELS))


def test_priority_names_ignore_case_and_punctuation():
    assert name_of(decide(state(row("Builder's Hut")), priorities=(("builders hut",),))) == "Builder's Hut"


# --------------------------------------------------------------------------- R2 modos y gasto

def test_maxer_never_picks_the_town_hall():  # E1R2
    d = decide(state(row("Town Hall", 9_000_000), row("Cannon", 100)), mode=Mode.MAXER)
    assert name_of(d) == "Cannon"
    assert d.why_not("Town Hall") == [Reason.MAXER_TOWN_HALL]


def test_maxer_with_only_the_town_hall_does_nothing():
    assert decide(state(row("Town Hall"))).chosen is None


def test_rusher_takes_the_town_hall_before_anything_else():
    d = decide(state(row("Town Hall", 9_000_000), row("Blacksmith")), mode=Mode.RUSHER, priorities=LEVELS)
    assert name_of(d) == "Town Hall"


def test_rusher_without_an_affordable_town_hall_follows_the_normal_rules():
    d = decide(state(row("Town Hall", affordable=False), row("Cannon")), mode=Mode.RUSHER)
    assert name_of(d) == "Cannon"


def test_dry_run_decides_like_maxer_but_marks_the_decision():
    d = decide(state(row("Town Hall"), row("Cannon", 100)), mode=Mode.DRY_RUN)
    assert name_of(d) == "Cannon" and d.dry_run
    assert not decide(state(row("Cannon"))).dry_run


@pytest.mark.parametrize(
    "builders, reserve, allowed",
    [(2, 1, True), (1, 1, False), (0, 1, False), (3, 0, True), (None, 0, False)],
)
def test_builder_reserve(builders, reserve, allowed):  # E2R2
    d = decide(state(row("Cannon"), builders=builders), reserve_builders=reserve)
    assert (d.chosen is not None) is allowed
    if not allowed:
        assert d.reason


def test_a_red_cost_is_never_eligible_even_in_the_first_priority():  # E3R2
    d = decide(state(row("Blacksmith", affordable=False)), priorities=LEVELS, fallback=Fallback.ANY)
    assert d.chosen is None
    assert d.why_not("Blacksmith") == [Reason.NOT_AFFORDABLE]


@pytest.mark.parametrize(
    "bad, reason",
    [
        (row("Cannon", resource=None), Reason.UNKNOWN_RESOURCE),
        (row("Cannon", cost=None), Reason.UNKNOWN_RESOURCE),
        (row("Cannon", resource="gems"), Reason.GEMS),
        (row(None), Reason.UNNAMED),
        (row("Cannon", kind="new_building"), Reason.NEW_BUILDING),
        (row("Cannon", cost=50_000_000), Reason.INSUFFICIENT),
    ],
)
def test_rows_that_cannot_be_trusted_are_discarded_with_their_reason(bad, reason):
    d = decide(state(bad))
    assert d.chosen is None and d.rejected[0].reason is reason


def test_unread_resource_leaves_only_the_red_check():
    d = decide(state(row("Cannon", 5_000_000), resources=(None, None, None)))
    assert name_of(d) == "Cannon"


def test_free_row_is_eligible():
    assert name_of(decide(state(row("Wall", free=True)), priorities=(("Wall",),))) == "Wall"


def test_cap_per_cycle():
    st = lambda n: state(row("Cannon"), started_this_cycle=n)
    assert decide(st(1), cap_per_cycle=2).chosen is not None
    assert decide(st(2), cap_per_cycle=2).chosen is None
    assert decide(st(99)).chosen is not None  # sin tope


def test_walls_are_chosen_only_if_listed_never_by_the_any_fallback():
    only_wall = state(row("Wall", 50_000))
    assert decide(only_wall).chosen is None
    assert decide(only_wall).why_not("Wall") == [Reason.WALL_BATCH]
    assert name_of(decide(only_wall, priorities=(("Wall",),))) == "Wall"


# --------------------------------------------------------------------------- R3 explicación

def test_cooldown_rows_are_rejected_with_the_reason():  # E1R3
    d = decide(state(row("Mortar"), row("Cannon", 100), on_cooldown=frozenset({"Mortar"})))
    assert name_of(d) == "Cannon"
    assert d.why_not("Mortar") == [Reason.COOLDOWN]


def test_hero_rows_are_dropped_when_heroes_are_excluded():  # E2R3
    d = decide(
        state(row("Archer Queen", 200_000, "dark_elixir", kind="hero"), row("Cannon", 100)),
        excluded=frozenset({EXCLUDE_HEROES}),
    )
    assert name_of(d) == "Cannon"
    assert d.why_not("Archer Queen") == [Reason.HEROES_EXCLUDED]


def test_dark_elixir_rows_go_first_unless_heroes_are_excluded():
    rows = (row("Archer Queen", 100_000, "dark_elixir", kind="hero"), row("Mortar", 3_000_000))
    assert name_of(decide(state(*rows))) == "Archer Queen"
    assert name_of(decide(state(*rows), excluded=frozenset({EXCLUDE_HEROES}))) == "Mortar"


def test_order_cheapest_vs_priciest():
    rows = (row("Cannon", 100), row("Mortar", 900), row("Archer Tower", 500))
    assert name_of(decide(state(*rows), order=Order.PRICIEST)) == "Mortar"
    assert name_of(decide(state(*rows), order=Order.CHEAPEST)) == "Cannon"


def test_no_eligible_row_still_explains_every_discard():
    d = decide(state(row("Cannon", affordable=False), row("Mortar", resource="gems")))
    assert d.chosen is None and {r.reason for r in d.rejected} == {Reason.NOT_AFFORDABLE, Reason.GEMS}


# --------------------------------------------------------------------------- validación

def test_priority_lists_are_validated_against_the_vocabulary_with_suggestions():
    vocab = Vocabulary.load_packaged()
    assert validate_priorities([["Laboratory", "Blacksmith"], ["Wall"]], vocab) == []
    problems = validate_priorities([["Laboratory"], ["Blacksmit", "Dragon Cannon"]], vocab)
    assert [(p.tier, p.name) for p in problems] == [(1, "Blacksmit"), (1, "Dragon Cannon")]
    assert "Blacksmith" in problems[0].suggestions and problems[1].suggestions == ()


# --------------------------------------------------------------------------- R4 idle

def test_everything_full_and_busy_waits_with_reason_and_next_check():  # E1R4
    d = decide_idle(free_builders=0, reserve=1, storages_full=True, next_builder_free_s=600)
    assert d.action is Idle.WAIT and d.next_check_s == 600
    assert "almacenes llenos" in d.reason and "0 libres" in d.reason


def test_a_freed_builder_means_work():  # E2R4
    d = decide_idle(free_builders=2, reserve=1, storages_full=True)
    assert d.action is Idle.WORK and d.next_check_s is None


def test_wait_time_is_bounded_and_defaults_when_unknown():
    assert decide_idle(0, 1, False).next_check_s == 300
    assert decide_idle(0, 1, False, next_builder_free_s=2).next_check_s == 30
    assert decide_idle(0, 1, False, next_builder_free_s=10**7).next_check_s == 1800


def test_unknown_builders_wait_instead_of_working():
    d = decide_idle(None, 1, False)
    assert d.action is Idle.WAIT and "desconocidos" in d.reason


def test_nothing_left_to_upgrade_finishes():
    assert decide_idle(3, 1, False, nothing_left=True).action is Idle.FINISH
