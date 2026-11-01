"""Secretos (token de Telegram, URL de webhook) fuera de los ficheros legibles (issue #15).

Tres almacenes detrás de la misma interfaz:
  - `KeyringSecretStore`: el del sistema (en Windows, el Administrador de credenciales). Extra opcional `.[secrets]`.
  - `FileSecretStore`: un fichero propio que solo puede abrir tu usuario. Falla cerrado: si no se puede
    restringir el acceso, no guarda nada.
  - `MemorySecretStore`: para las pruebas.

Los ajustes legibles nunca llevan un secreto, solo su nombre. `Redactor` y `RedactingFilter` los quitan de
cualquier texto o registro antes de que salgan (logs, paquetes de diagnóstico).
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from pathlib import Path
from typing import Any, Iterable, Protocol

SERVICE = "PyClash"
TELEGRAM_TOKEN = "telegram_token"
TELEGRAM_CHAT = "telegram_chat"
DISCORD_WEBHOOK = "discord_webhook"
KNOWN_SECRETS = (TELEGRAM_TOKEN, TELEGRAM_CHAT, DISCORD_WEBHOOK)
SECRETS_FILE = "secrets.json"
MASK = "«secreto»"


class SecretStoreError(RuntimeError):
    pass


class SecretStore(Protocol):
    backend: str

    def get(self, name: str) -> str | None: ...
    def set(self, name: str, value: str) -> None: ...
    def delete(self, name: str) -> None: ...


class MemorySecretStore:
    backend = "memoria"

    def __init__(self) -> None:
        self._values: dict[str, str] = {}

    def get(self, name: str) -> str | None:
        return self._values.get(name)

    def set(self, name: str, value: str) -> None:
        self._values[name] = value

    def delete(self, name: str) -> None:
        self._values.pop(name, None)


class KeyringSecretStore:
    backend = "sistema"

    def __init__(self, keyring_module: Any):
        self._kr = keyring_module

    def get(self, name: str) -> str | None:
        return self._kr.get_password(SERVICE, name)

    def set(self, name: str, value: str) -> None:
        self._kr.set_password(SERVICE, name, value)

    def delete(self, name: str) -> None:
        try:
            self._kr.delete_password(SERVICE, name)
        except Exception:  # noqa: BLE001 - borrar lo que no existe no es un error
            pass


def restrict_to_current_user(path: Path) -> None:
    """Deja el fichero accesible solo para el usuario actual. Lanza `SecretStoreError` si no puede."""
    if os.name == "nt":
        user = os.environ.get("USERNAME")
        if not user:
            raise SecretStoreError("no se sabe el usuario de Windows para restringir el fichero de secretos")
        try:
            done = subprocess.run(
                ["icacls", str(path), "/inheritance:r", "/grant:r", f"{user}:F"],
                capture_output=True,
                stdin=subprocess.DEVNULL,
                timeout=15,
            )
        except (OSError, subprocess.SubprocessError) as e:
            raise SecretStoreError(f"no se pudo restringir el fichero de secretos: {e}") from e
        if done.returncode != 0:
            raise SecretStoreError("icacls no pudo restringir el fichero de secretos")
    else:
        os.chmod(path, 0o600)


class FileSecretStore:
    backend = "fichero"

    def __init__(self, path: Path, restrict=restrict_to_current_user):
        self.path = path
        self._restrict = restrict

    def _read(self) -> dict[str, str]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return {k: v for k, v in data.items() if isinstance(k, str) and isinstance(v, str)} if isinstance(data, dict) else {}

    def _write(self, values: dict[str, str]) -> None:
        # Atómico como el resto, pero el temporal se restringe ANTES de recibir el secreto (el renombrado deja al
        # fichero final con los permisos del temporal): el secreto nunca existe con permisos abiertos.
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(f".{self.path.name}.{os.getpid()}.tmp")
        try:
            tmp.touch()
            self._restrict(tmp)
            with open(tmp, "w", encoding="utf-8", newline="\n") as f:
                f.write(json.dumps(values, indent=2))
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.path)
        except BaseException:
            try:
                tmp.unlink()
            except OSError:
                pass
            raise

    def get(self, name: str) -> str | None:
        return self._read().get(name)

    def set(self, name: str, value: str) -> None:
        values = self._read()
        values[name] = value
        self._write(values)

    def delete(self, name: str) -> None:
        values = self._read()
        if values.pop(name, None) is not None:
            self._write(values)


def default_secret_store(data_dir: Path, keyring_module: Any = None) -> SecretStore:
    """El almacén del sistema si existe y funciona; si no, el fichero restringido."""
    kr = keyring_module
    if kr is None:
        try:
            import keyring as kr  # type: ignore[no-redef]
        except ImportError:
            kr = None
    if kr is not None:
        try:
            backend = kr.get_keyring()
            module = type(backend).__module__
            if not module.startswith(("keyring.backends.fail", "keyring.backends.null")):
                return KeyringSecretStore(kr)
        except Exception:  # noqa: BLE001 - cualquier fallo de keyring significa «no disponible»
            pass
    return FileSecretStore(data_dir / SECRETS_FILE)


# ---------------------------------------------------------------- que no salgan


class Redactor:
    """Sustituye los secretos conocidos por una máscara en cualquier texto."""

    def __init__(self, store: SecretStore, names: Iterable[str] = KNOWN_SECRETS):
        self._store = store
        self._names = tuple(names)

    def values(self) -> list[str]:
        found = (self._store.get(n) for n in self._names)
        return sorted({v for v in found if v}, key=len, reverse=True)  # los largos primero

    def __call__(self, text: str) -> str:
        for value in self.values():
            text = text.replace(value, MASK)
        return text


class RedactingFilter(logging.Filter):
    """Filtro de `logging` que enmascara los secretos en el mensaje ya formateado."""

    def __init__(self, redactor: Redactor):
        super().__init__()
        self._redactor = redactor

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = self._redactor(record.getMessage())
        record.args = None
        return True


def install_log_redaction(store: SecretStore, logger: logging.Logger | None = None) -> RedactingFilter:
    """Añade el filtro a los manejadores del registro raíz (o del indicado)."""
    flt = RedactingFilter(Redactor(store))
    target = logger or logging.getLogger()
    for handler in target.handlers:
        handler.addFilter(flt)
    return flt
