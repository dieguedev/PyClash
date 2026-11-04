"""Secretos (#15): fuera de los ficheros legibles, de los logs y de los diagnósticos."""

import io
import logging
import os
import subprocess

import pytest

from pyclash.config.schema import AccountProfile, AppConfig, WindowSelection
from pyclash.config.credentials import (
    DISCORD_WEBHOOK,
    MASK,
    TELEGRAM_TOKEN,
    FileSecretStore,
    KeyringSecretStore,
    MemorySecretStore,
    Redactor,
    RedactingFilter,
    SecretStoreError,
    default_secret_store,
    restrict_to_current_user,
)
from pyclash.config.store import ConfigStore

TOKEN = "123456:ABC-token-de-prueba-NO-FILTRAR"
HOOK = "https://discord.com/api/webhooks/999/hook-de-prueba-NO-FILTRAR"


class FakeKeyring:
    def __init__(self):
        self.items = {}

    def get_keyring(self):
        return type("Backend", (), {})()

    def get_password(self, service, name):
        return self.items.get((service, name))

    def set_password(self, service, name, value):
        self.items[(service, name)] = value

    def delete_password(self, service, name):
        del self.items[(service, name)]


def no_restrict(_path):
    pass


# ---------------------------------------------------------------- R3: no se filtran


def test_secret_never_reaches_readable_config_files(tmp_path):
    secrets = FileSecretStore(tmp_path / "secrets.json", restrict=no_restrict)
    secrets.set(TELEGRAM_TOKEN, TOKEN)
    secrets.set(DISCORD_WEBHOOK, HOOK)
    store = ConfigStore(tmp_path)
    store.save_app(AppConfig(WindowSelection("GPG"), "uno"))
    store.save_profile(AccountProfile("uno"))
    for path in tmp_path.rglob("*"):
        if path.is_file() and path.name != "secrets.json":
            text = path.read_text(encoding="utf-8")
            assert TOKEN not in text and HOOK not in text, path


def test_keyring_store_keeps_secrets_in_the_system_and_not_on_disk(tmp_path):
    kr = FakeKeyring()
    store = default_secret_store(tmp_path, keyring_module=kr)
    assert isinstance(store, KeyringSecretStore)
    store.set(TELEGRAM_TOKEN, TOKEN)
    assert store.get(TELEGRAM_TOKEN) == TOKEN
    store.delete(TELEGRAM_TOKEN)
    store.delete(TELEGRAM_TOKEN)  # borrar lo que no existe no falla
    assert store.get(TELEGRAM_TOKEN) is None
    assert not list(tmp_path.iterdir())


def test_falls_back_to_the_restricted_file_without_a_usable_keyring(tmp_path):
    class Fail:
        def get_keyring(self):
            return type("Backend", (), {"__module__": "keyring.backends.fail"})()

    assert isinstance(default_secret_store(tmp_path, keyring_module=Fail()), FileSecretStore)

    class Boom:
        def get_keyring(self):
            raise RuntimeError("sin D-Bus")

    assert isinstance(default_secret_store(tmp_path, keyring_module=Boom()), FileSecretStore)


def test_diagnostic_text_and_logs_are_redacted():
    store = MemorySecretStore()
    store.set(TELEGRAM_TOKEN, TOKEN)
    store.set(DISCORD_WEBHOOK, HOOK)
    redact = Redactor(store)
    text = f"fallo enviando a {HOOK} con bot{TOKEN}/sendMessage"
    out = redact(text)
    assert TOKEN not in out and HOOK not in out and MASK in out

    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.addFilter(RedactingFilter(redact))
    log = logging.getLogger("test.secretos")
    log.setLevel(logging.INFO)
    log.addHandler(handler)
    try:
        log.info("token %s", TOKEN)
        log.info(f"webhook {HOOK}")
    finally:
        log.removeHandler(handler)
    assert TOKEN not in stream.getvalue() and HOOK not in stream.getvalue()
    assert stream.getvalue().count(MASK) == 2


def test_redactor_handles_no_secrets_and_overlapping_values():
    store = MemorySecretStore()
    assert Redactor(store)("nada que ocultar") == "nada que ocultar"
    store.set(TELEGRAM_TOKEN, "abc")
    store.set(DISCORD_WEBHOOK, "abcdef")
    assert Redactor(store)("x abcdef y") == f"x {MASK} y"  # el largo primero, sin dejar «def» suelto


# ---------------------------------------------------------------- el fichero de secretos


def test_file_store_roundtrip_and_delete(tmp_path):
    s = FileSecretStore(tmp_path / "sub" / "secrets.json", restrict=no_restrict)
    assert s.get("x") is None
    s.set("x", "uno")
    s.set("y", "dos")
    s.delete("x")
    assert (s.get("x"), s.get("y")) == (None, "dos")
    assert not list((tmp_path / "sub").glob(".*.tmp"))


def test_file_store_restricts_the_temp_before_writing_the_secret(tmp_path):
    seen = []

    def spy(path):
        seen.append((path.name, path.read_text(encoding="utf-8") if path.exists() else None))

    FileSecretStore(tmp_path / "secrets.json", restrict=spy).set("x", TOKEN)
    assert seen and seen[0][0].endswith(".tmp") and seen[0][1] == ""  # vacío cuando se restringe


def test_file_store_fails_closed_when_it_cannot_restrict(tmp_path):
    def refuse(_path):
        raise SecretStoreError("no se pudo")

    path = tmp_path / "secrets.json"
    with pytest.raises(SecretStoreError):
        FileSecretStore(path, restrict=refuse).set("x", TOKEN)
    assert not path.exists() and not list(tmp_path.glob(".*.tmp"))


@pytest.mark.skipif(os.name != "nt", reason="icacls solo existe en Windows")
def test_icacls_leaves_only_the_current_user(tmp_path):
    f = tmp_path / "s.json"
    f.write_text("{}")
    restrict_to_current_user(f)
    shown = subprocess.run(["icacls", str(f)], capture_output=True, text=True, stdin=subprocess.DEVNULL).stdout
    assert os.environ["USERNAME"].lower() in shown.lower()
    for broad in ("Everyone", "BUILTIN\\Users", "Authenticated Users"):
        assert broad not in shown
