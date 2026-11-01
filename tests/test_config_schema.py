"""Esquema de la configuración (#15): validación con mensajes, versiones y en vivo / al reiniciar."""

import pytest

from pyclash.config.schema import (
    LIVE,
    RESTART,
    SCHEMA_VERSION,
    AccountProfile,
    AppConfig,
    UpgradeProfile,
    WindowSelection,
    applies_when,
    changes,
    migrate,
    parse_app,
    parse_profile,
    profile_to_dict,
    restart_needed,
    validate_profile,
)
from pyclash.policy.upgrades import Fallback, Mode, Order


def only(problems):
    assert len(problems) == 1, [str(p) for p in problems]
    return problems[0]


# ---------------------------------------------------------------- R1: valores fuera de rango


@pytest.mark.parametrize(
    "path,raw,fragment",
    [
        ("upgrade.reserve_builders", {"upgrade": {"reserve_builders": -3}}, "entre 0 y 5"),
        ("upgrade.reserve_builders", {"upgrade": {"reserve_builders": 9}}, "entre 0 y 5"),
        ("upgrade.reserve_builders", {"upgrade": {"reserve_builders": "2"}}, "número entero"),
        ("upgrade.reserve_builders", {"upgrade": {"reserve_builders": True}}, "número entero"),
        ("upgrade.cap_per_cycle", {"upgrade": {"cap_per_cycle": 0}}, "entre 1 y 99"),
        ("upgrade.order", {"upgrade": {"order": "random"}}, "priciest"),
        ("upgrade.mode", {"upgrade": {"mode": "dry_run"}}, "maxer, rusher"),
        ("upgrade.fallback", {"upgrade": {"fallback": "x"}}, "any, none"),
        ("upgrade.excluded[0]", {"upgrade": {"excluded": ["walls"]}}, "heroes"),
        ("upgrade.priorities", {"upgrade": {"priorities": "Cannon"}}, "lista"),
        ("upgrade.priorities[0]", {"upgrade": {"priorities": ["Cannon"]}}, "lista de nombres"),
        ("enabled", {"enabled": "si"}, "verdadero o falso"),
        ("from_v1.wall_upgrade_threshold_m", {"from_v1": {"wall_upgrade_threshold_m": 21}}, "entre 0 y 20"),
        ("from_v1.min_loot_gold", {"from_v1": {"min_loot_gold": -1}}, "entre 0 y 9999999"),
        ("from_v1.earthquake_method", {"from_v1": {"earthquake_method": "Boom"}}, "Curve Placement"),
        ("from_v1.random_minutes_max", {"from_v1": {"random_minutes_min": 10, "random_minutes_max": 5}}, "menor"),
        ("upgrade", {"upgrade": 7}, "objeto"),
    ],
)
def test_invalid_value_is_reported_with_path_and_valid_range(path, raw, fragment):
    profile, problems = parse_profile({"name": "a", **raw})
    problem = only(problems)
    assert problem.path == path
    assert fragment in problem.message
    assert profile.upgrade == UpgradeProfile() or path.startswith("upgrade")  # el resto sigue en defecto


def test_a_bad_field_does_not_spoil_the_others():
    raw = {"name": "a", "upgrade": {"reserve_builders": -3, "order": "cheapest", "excluded": ["heroes"]}}
    profile, problems = parse_profile(raw)
    assert len(problems) == 1
    assert profile.upgrade.reserve_builders == 1  # defecto
    assert profile.upgrade.order == "cheapest" and profile.upgrade.excluded == ("heroes",)


def test_missing_name_is_a_problem_unless_a_hint_exists():
    assert only(parse_profile({})[1]).path == "name"
    assert parse_profile({}, "pepe")[0].name == "pepe" and not parse_profile({}, "pepe")[1]


def test_valid_profile_roundtrips_without_problems():
    original = AccountProfile(
        "Mi cuenta",
        enabled=False,
        upgrade=UpgradeProfile("rusher", "cheapest", 0, 5, (("Cannon", "Archer Tower"), ("Wall",)), "none", ("heroes",)),
    )
    again, problems = parse_profile(profile_to_dict(original))
    assert problems == [] and again == original
    assert validate_profile(original) == []


def test_validate_profile_catches_an_invalid_object():
    bad = AccountProfile("x", upgrade=UpgradeProfile(reserve_builders=-3))
    assert only(validate_profile(bad)).path == "upgrade.reserve_builders"


def test_profile_maps_to_upgrade_settings():
    s = UpgradeProfile("rusher", "cheapest", 2, 4, (("Cannon",),), "none", ("heroes",)).settings()
    assert (s.mode, s.order, s.fallback) == (Mode.RUSHER, Order.CHEAPEST, Fallback.NONE)
    assert s.reserve_builders == 2 and s.cap_per_cycle == 4
    assert s.priorities == (("Cannon",),) and s.excluded == frozenset({"heroes"})


def test_app_config_parse():
    app, problems = parse_app({"window": {"title": " GPG ", "child_class": "Surface"}, "active_profile": "a"})
    assert problems == []
    assert app == AppConfig(WindowSelection("GPG", "", "Surface"), "a")
    assert parse_app({"window": 3})[1][0].path == "window"


# ---------------------------------------------------------------- E3R1: versiones


def add_field(data):
    return {**data, "upgrade": {**data.get("upgrade", {}), "nuevo": 1}}


def test_old_version_is_migrated_forward_keeping_values():
    old = {"version": 1, "name": "a", "upgrade": {"order": "cheapest"}}
    data, found, problems = migrate(old, "profile", current=3, migrations={1: add_field, 2: lambda d: {**d, "x": 2}})
    assert found == 1 and problems == []
    assert data["version"] == 3 and data["x"] == 2 and data["upgrade"] == {"order": "cheapest", "nuevo": 1}


def test_missing_migration_step_is_reported():
    _, _, problems = migrate({"version": 1}, "profile", current=2, migrations={})
    assert "no hay migración" in only(problems).message


def test_newer_file_is_flagged_not_migrated():
    data, found, problems = migrate({"version": 9, "name": "a"}, "profile")
    assert found == 9 and "más nueva" in only(problems).message and data["version"] == 9


def test_missing_or_invalid_version():
    assert "se asume la versión 1" in only(migrate({}, "app")[2]).message
    assert "no es una versión válida" in only(migrate({"version": "x"}, "app")[2]).message


def test_current_files_carry_the_schema_version():
    assert profile_to_dict(AccountProfile("a"))["version"] == SCHEMA_VERSION


# ---------------------------------------------------------------- en vivo o al reiniciar


@pytest.mark.parametrize(
    "path,expected",
    [
        ("upgrade.order", LIVE),
        ("upgrade.reserve_builders", LIVE),
        ("upgrade.cap_per_cycle", LIVE),
        ("upgrade.priorities", LIVE),
        ("upgrade.excluded", LIVE),
        ("upgrade.mode", RESTART),
        ("window.title", RESTART),
        ("from_v1.min_loot_gold", RESTART),
        ("campo.desconocido", RESTART),  # lo no registrado espera: es lo seguro
    ],
)
def test_applies_when(path, expected):
    assert applies_when(path) == expected


def test_changes_tell_what_is_live_and_what_needs_restart():
    old = AccountProfile("a")
    new = AccountProfile("a", upgrade=UpgradeProfile(mode="rusher", order="cheapest", excluded=("heroes",)))
    got = {c.path: c.applies for c in changes(old, new)}
    assert got == {"upgrade.mode": RESTART, "upgrade.order": LIVE, "upgrade.excluded": LIVE}
    assert [c.path for c in restart_needed(old, new)] == ["upgrade.mode"]
    assert changes(old, old) == []
    assert [c.applies for c in changes(AppConfig(), AppConfig(WindowSelection("x"), None))] == [RESTART]
