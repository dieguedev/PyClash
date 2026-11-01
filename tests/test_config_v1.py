"""Importación de PixelChief v1 (#15) y traslado de la carpeta de datos antigua."""

import json

import pytest

from pyclash import paths
from pyclash.config.schema import AccountProfile, UpgradeProfile, WindowSelection
from pyclash.config.store import ConfigStore
from pyclash.config.v1 import DEFAULT_PROFILE_NAME, detect_v1, import_v1, v1_data_dir

V1_SETTINGS = {
    "earthquake_method": "Random Placement",
    "wall_upgrade_threshold_m": 4,
    "reserve_builders": 0,
    "upgrade_order": "cheapest",
    "random_minutes_min": 5,
    "random_minutes_max": 9,
    "repeat_pause_min": 0,
    "repeat_pause_max": 0,
    "min_loot_gold": 3000,
    "min_loot_elixir": 2000,
    "min_loot_dark": 100,
}


@pytest.fixture
def v1(tmp_path):
    d = tmp_path / "PixelChief"
    d.mkdir()
    return d


def write(d, name, data):
    (d / name).write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")


# ---------------------------------------------------------------- R2: ajustes, jugadores y ventana


def test_no_v1_data_means_nothing_to_offer(v1):
    report = detect_v1(v1)
    assert not report.has_data and "No hay datos" in report.describe()[0]


def test_user_with_v1_data_sees_what_would_be_imported_then_gets_it(v1, tmp_path):
    write(v1, "settings.json", V1_SETTINGS)
    write(v1, "player_list.json", [{"name": "Ana", "enabled": True}, {"name": "Beto", "enabled": False}])
    write(v1, "window.json", {"title": "Google Play Games", "top_class": "Top", "child_class": "Surface"})
    report = detect_v1(v1)
    text = "\n".join(report.describe())
    assert "Ana" in text and "Beto (desactivado)" in text and "Google Play Games" in text

    store = ConfigStore(tmp_path / "nuevo")
    result = import_v1(report, store)
    assert result.created == ["Ana", "Beto"] and result.window_imported
    ana, beto = (store.load_profile(n).value for n in ("Ana", "Beto"))
    # cada jugador recibe su copia de los ajustes de la v1
    assert ana.upgrade == beto.upgrade == UpgradeProfile(order="cheapest", reserve_builders=0)
    assert ana.from_v1 == beto.from_v1 and ana.from_v1.min_loot_gold == 3000
    assert ana.from_v1.earthquake_method == "Random Placement" and ana.from_v1.random_minutes_max == 9
    assert ana.enabled and not beto.enabled
    assert store.load_app().value.window == WindowSelection("Google Play Games", "Top", "Surface")
    assert all(not p.problems for p in store.list_profiles())


def test_import_does_not_touch_the_v1_folder(v1, tmp_path):
    write(v1, "settings.json", V1_SETTINGS)
    write(v1, "player_list.json", [{"name": "Ana"}])
    before = {p.name: p.read_bytes() for p in v1.iterdir()}
    import_v1(detect_v1(v1), ConfigStore(tmp_path / "nuevo"))
    assert {p.name: p.read_bytes() for p in v1.iterdir()} == before


def test_settings_without_player_list_make_one_default_account(v1, tmp_path):
    write(v1, "settings.json", V1_SETTINGS)
    store = ConfigStore(tmp_path / "nuevo")
    assert import_v1(detect_v1(v1), store).created == [DEFAULT_PROFILE_NAME]


def test_import_never_overwrites_existing_accounts_or_window(v1, tmp_path):
    write(v1, "settings.json", V1_SETTINGS)
    write(v1, "player_list.json", [{"name": "Ana"}, {"name": "Nueva"}])
    write(v1, "window.json", {"title": "v1"})
    store = ConfigStore(tmp_path / "nuevo")
    store.save_profile(AccountProfile("Ana", upgrade=UpgradeProfile(order="priciest", reserve_builders=3)))
    from pyclash.config.schema import AppConfig

    store.save_app(AppConfig(WindowSelection("la mía")))
    result = import_v1(detect_v1(v1), store)
    assert result.created == ["Nueva"] and result.skipped == ["Ana"] and not result.window_imported
    assert store.load_profile("Ana").value.upgrade.reserve_builders == 3
    assert store.load_app().value.window.title == "la mía"
    again = import_v1(detect_v1(v1), store)  # repetir no duplica
    assert again.created == [] and sorted(again.skipped) == ["Ana", "Nueva"]


def test_duplicate_and_blank_player_names_are_ignored(v1):
    write(v1, "player_list.json", [{"name": " Ana "}, {"name": "Ana"}, {"name": ""}, {"name": 5}, "x", {"name": "Z", "enabled": 0}])
    assert detect_v1(v1).players == [("Ana", True), ("Z", False)]


# ---------------------------------------------------------------- E2R2: v1 corrupto


@pytest.mark.parametrize("name", ["settings.json", "player_list.json", "window.json"])
def test_unreadable_v1_file_is_reported_and_the_rest_continues(v1, tmp_path, name):
    write(v1, "settings.json", V1_SETTINGS)
    write(v1, "player_list.json", [{"name": "Ana"}])
    write(v1, "window.json", {"title": "w"})
    write(v1, name, "{{ esto no es json")
    report = detect_v1(v1)
    assert any(name in p for p in report.problems)
    store = ConfigStore(tmp_path / "nuevo")
    import_v1(report, store)  # no lanza
    if name != "player_list.json":
        assert store.load_profile("Ana") is not None
    if name == "settings.json":
        assert store.load_profile("Ana").value == AccountProfile("Ana")  # valores por defecto
    assert (store.load_app().value.window.title == "w") == (name != "window.json")


def test_wrong_json_shapes_do_not_crash(v1):
    write(v1, "settings.json", "[1]")
    write(v1, "player_list.json", {"a": 1})
    write(v1, "window.json", "[]")
    report = detect_v1(v1)
    assert len(report.problems) == 3 and report.settings is None


def test_out_of_range_v1_values_fall_back_and_are_reported(v1):
    write(v1, "settings.json", {**V1_SETTINGS, "reserve_builders": 99})
    report = detect_v1(v1)
    assert report.settings.upgrade.reserve_builders == 1
    assert any("reserve_builders" in p for p in report.problems)


def test_v1_dir_is_next_to_the_new_one():
    assert v1_data_dir({"LOCALAPPDATA": "C:/L"}).as_posix() == "C:/L/PixelChief"


# ---------------------------------------------------------------- carpeta de datos


def test_data_dir_is_local_not_roaming(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "roaming"))
    assert paths.user_data_dir() == tmp_path / "local" / "PyClash"


def test_files_left_in_the_old_roaming_folder_move_on_startup(tmp_path, monkeypatch):
    local, roaming = tmp_path / "local", tmp_path / "roaming"
    old = roaming / "PyClash"
    old.mkdir(parents=True)
    (old / "display_restore.devmode").write_bytes(b"pendiente")
    (old / "hidden_window.json").write_text("{}")
    (old / "sessions.jsonl").write_text("{}\n")
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setenv("APPDATA", str(roaming))
    new = paths.user_data_dir()
    assert (new / "display_restore.devmode").read_bytes() == b"pendiente"  # el marcador de seguridad llega
    assert (new / "hidden_window.json").is_file() and (new / "sessions.jsonl").is_file()
    assert not old.exists()  # vacía: se quita


def test_moving_never_overwrites_and_leaves_unknown_files(tmp_path, monkeypatch):
    local, roaming = tmp_path / "local", tmp_path / "roaming"
    (roaming / "PyClash").mkdir(parents=True)
    (local / "PyClash").mkdir(parents=True)
    (roaming / "PyClash" / "sessions.jsonl").write_text("viejo")
    (roaming / "PyClash" / "otra_cosa.txt").write_text("mía")
    (local / "PyClash" / "sessions.jsonl").write_text("nuevo")
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setenv("APPDATA", str(roaming))
    paths.user_data_dir()
    assert (local / "PyClash" / "sessions.jsonl").read_text() == "nuevo"
    assert (roaming / "PyClash" / "sessions.jsonl").read_text() == "viejo"  # no se pierde
    assert (roaming / "PyClash" / "otra_cosa.txt").exists()
    paths.user_data_dir()  # repetir no cambia nada
