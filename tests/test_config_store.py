"""Almacén de configuración (#15): escritura atómica, ficheros rotos, versiones y una sola copia."""

import json
import os

import pytest

from pyclash.config.files import AlreadyRunning, InstanceLock, atomic_write_text, set_aside_copy
from pyclash.config.schema import AccountProfile, AppConfig, ConfigError, UpgradeProfile, WindowSelection
from pyclash.config.store import ConfigStore, profile_filename


@pytest.fixture
def store(tmp_path):
    return ConfigStore(tmp_path / "data")


# ---------------------------------------------------------------- R1: escritura segura


def test_roundtrip_of_app_and_profiles(store):
    store.save_app(AppConfig(WindowSelection("GPG", "Top", "Surface"), "uno"))
    store.save_profile(AccountProfile("uno", upgrade=UpgradeProfile(order="cheapest")))
    store.save_profile(AccountProfile("Dos", enabled=False))
    assert store.load_app().value == AppConfig(WindowSelection("GPG", "Top", "Surface"), "uno")
    assert [p.value.name for p in store.list_profiles()] == ["Dos", "uno"]
    assert store.load_profile("uno").value.upgrade.order == "cheapest"
    assert store.load_profile("nadie") is None
    assert all(not p.problems for p in store.list_profiles())


def test_saving_the_same_account_overwrites_its_file(store):
    a = store.save_profile(AccountProfile("uno"))
    b = store.save_profile(AccountProfile("uno", enabled=False))
    assert a == b and len(list(store.profiles_dir.glob("*.json"))) == 1
    assert store.load_profile("uno").value.enabled is False


def test_interrupted_write_leaves_previous_file_intact(store, monkeypatch):
    store.save_app(AppConfig(WindowSelection("antes"), None))
    before = store.app_path.read_text(encoding="utf-8")

    def crash(*_a, **_k):
        raise OSError("el proceso muere aquí")

    monkeypatch.setattr(os, "replace", crash)
    with pytest.raises(OSError):
        store.save_app(AppConfig(WindowSelection("después"), None))
    monkeypatch.undo()
    assert store.app_path.read_text(encoding="utf-8") == before
    assert not list(store.directory.glob(".*.tmp"))  # sin temporales sueltos
    assert store.load_app().value.window.title == "antes"


def test_death_while_writing_the_temp_keeps_the_original(tmp_path):
    target = tmp_path / "config.json"
    target.write_text('{"ok": 1}', encoding="utf-8")
    # un cierre brusco deja el temporal a medias, pero el original ni se ha tocado
    (tmp_path / f".config.json.{os.getpid()}.tmp").write_text('{"ok": ', encoding="utf-8")
    assert json.loads(target.read_text(encoding="utf-8")) == {"ok": 1}
    s = ConfigStore(tmp_path)
    s.app_path.write_text('{"version": 1, "window": {"title": "x"}}', encoding="utf-8")
    assert s.load_app().value.window.title == "x"
    assert not list(tmp_path.glob(".*.tmp"))  # al cargar se limpian


def test_atomic_write_creates_directories_and_cleans_up_on_failure(tmp_path, monkeypatch):
    path = tmp_path / "a" / "b.json"
    atomic_write_text(path, "{}")
    assert path.read_text(encoding="utf-8") == "{}"
    monkeypatch.setattr(os, "fsync", lambda _fd: (_ for _ in ()).throw(OSError("disco lleno")))
    with pytest.raises(OSError):
        atomic_write_text(path, "nuevo")
    assert path.read_text(encoding="utf-8") == "{}" and not list(path.parent.glob(".*.tmp"))


def test_out_of_range_value_is_rejected_with_the_range_and_nothing_is_written(store):
    with pytest.raises(ConfigError) as e:
        store.save_profile(AccountProfile("uno", upgrade=UpgradeProfile(reserve_builders=-3)))
    assert "entre 0 y 5" in str(e.value) and "upgrade.reserve_builders" in str(e.value)
    assert store.list_profiles() == []


# ---------------------------------------------------------------- ficheros rotos y versiones


def test_unreadable_app_file_gives_defaults_and_keeps_a_bad_copy(store):
    store.directory.mkdir(parents=True)
    store.app_path.write_text("{no es json", encoding="utf-8")
    loaded = store.load_app()
    assert loaded.value == AppConfig() and loaded.set_aside is not None
    assert "se usan los valores por defecto" in str(loaded.problems[0])
    assert loaded.set_aside.read_text(encoding="utf-8") == "{no es json"
    assert store.app_path.read_text(encoding="utf-8") == "{no es json"  # no se reescribe hasta que se guarde
    store.load_app()
    assert len(list(store.directory.glob("config.json.bad*"))) == 1  # no se acumulan copias iguales


def test_bad_field_in_a_file_keeps_the_rest_and_does_not_rewrite(store):
    store.profiles_dir.mkdir(parents=True)
    path = store.profiles_dir / "uno.json"
    text = json.dumps({"version": 1, "name": "uno", "upgrade": {"reserve_builders": -3, "order": "cheapest"}})
    path.write_text(text, encoding="utf-8")
    loaded = store.load_profile("uno")
    assert loaded.value.upgrade.order == "cheapest" and loaded.value.upgrade.reserve_builders == 1
    assert [p.path for p in loaded.problems] == ["upgrade.reserve_builders"]
    assert path.read_text(encoding="utf-8") == text


def test_corrupt_profile_is_still_listed_by_its_file_name(store):
    store.profiles_dir.mkdir(parents=True)
    (store.profiles_dir / "roto.json").write_text("[1,2]", encoding="utf-8")
    (p,) = store.list_profiles()
    assert p.value.name == "roto" and p.set_aside is not None
    store.save_profile(AccountProfile("roto"))  # guardar lo arregla
    assert store.load_profile("roto").problems == []


def test_newer_file_is_read_but_never_overwritten(store):
    store.profiles_dir.mkdir(parents=True)
    path = store.profiles_dir / "uno.json"
    path.write_text(json.dumps({"version": 9, "name": "uno", "futuro": True}), encoding="utf-8")
    loaded = store.load_profile("uno")
    assert loaded.newer_version and "más nueva" in str(loaded.problems[0])
    with pytest.raises(ConfigError, match="más nueva"):
        store.save_profile(AccountProfile("uno"))
    assert json.loads(path.read_text(encoding="utf-8"))["futuro"] is True


def test_set_aside_copy_numbers_different_contents(tmp_path):
    f = tmp_path / "x.json"
    f.write_text("uno")
    first = set_aside_copy(f)
    f.write_text("dos")
    second = set_aside_copy(f)
    assert first.name == "x.json.bad" and second.name == "x.json.bad1"
    assert set_aside_copy(tmp_path / "no_existe") is None


@pytest.mark.parametrize("name", ["Mi cuenta", "a/b", "CON", "ñandú", "", "x" * 80, "con.punto"])
def test_profile_filenames_are_safe_and_distinct(name):
    f = profile_filename(name)
    assert f.endswith(".json") and not set(f) & set('/\\:*?"<>|')
    assert len(f) < 60
    assert profile_filename("a/b") != profile_filename("a:b")


def test_awkward_names_roundtrip(store):
    for name in ("a/b", "a:b", "CON", "Ñu ñu"):
        store.save_profile(AccountProfile(name))
    assert sorted(p.value.name for p in store.list_profiles()) == sorted(["a/b", "a:b", "CON", "Ñu ñu"])


def test_delete_profile(store):
    store.save_profile(AccountProfile("uno"))
    assert store.delete_profile("uno") and not store.delete_profile("uno")


# ---------------------------------------------------------------- una sola copia


def test_second_instance_is_refused_and_lock_is_released(tmp_path):
    path = tmp_path / "pyclash.lock"
    with InstanceLock(path):
        with pytest.raises(AlreadyRunning, match="ya está en marcha"):
            InstanceLock(path).acquire()
    with InstanceLock(path):  # liberado: se puede volver a tomar
        pass
