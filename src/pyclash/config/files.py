"""E/S de ficheros de configuración: escritura atómica, copia de los ilegibles y una sola copia del programa."""

from __future__ import annotations

import os
from pathlib import Path
from typing import IO


def atomic_write_text(path: Path, text: str) -> None:
    """Escribe en un temporal del mismo directorio y lo renombra encima: o queda el fichero entero nuevo o el viejo.

    Un corte a mitad deja, como mucho, un `.tmp` suelto (que `remove_stale_temps` limpia) y el original íntegro.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


def remove_stale_temps(directory: Path) -> None:
    """Quita temporales que dejó un cierre brusco (con una sola copia del programa, ninguno está en uso)."""
    if not directory.is_dir():
        return
    for tmp in directory.glob(".*.tmp"):
        try:
            tmp.unlink()
        except OSError:
            pass


def set_aside_copy(path: Path) -> Path | None:
    """Copia un fichero ilegible a `<nombre>.bad` (o `.bad1`, `.bad2`…) y devuelve la copia; el original no se toca.

    Si ya hay una copia idéntica, la reutiliza: leer el mismo fichero roto cien veces no llena la carpeta.
    """
    try:
        data = path.read_bytes()
    except OSError:
        return None
    for n in range(1000):
        candidate = path.with_name(f"{path.name}.bad{n or ''}")
        if not candidate.exists():
            try:
                candidate.write_bytes(data)
            except OSError:
                return None
            return candidate
        try:
            if candidate.read_bytes() == data:
                return candidate
        except OSError:
            continue
    return None


class AlreadyRunning(RuntimeError):
    """Hay otra copia de PyClash usando esta carpeta de datos."""


class InstanceLock:
    """Bloqueo de fichero para que solo haya una copia del programa por carpeta de datos.

    Dos bots sobre la misma ventana del juego se estorbarían, y dos copias guardando ajustes a la vez se
    pisarían. El sistema operativo libera el bloqueo si el proceso muere, así que no queda nunca «atascado».
    """

    def __init__(self, path: Path):
        self.path = path
        self._file: IO[bytes] | None = None

    def acquire(self) -> "InstanceLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        f = open(self.path, "a+b")
        try:
            _lock(f)
        except OSError:
            f.close()
            raise AlreadyRunning("PyClash ya está en marcha (otra copia usa esta carpeta de datos).") from None
        self._file = f
        return self

    def release(self) -> None:
        if self._file is not None:
            try:
                _unlock(self._file)
            finally:
                self._file.close()
                self._file = None

    def __enter__(self) -> "InstanceLock":
        return self.acquire()

    def __exit__(self, *exc: object) -> None:
        self.release()


if os.name == "nt":
    import msvcrt

    def _lock(f: IO[bytes]) -> None:
        f.seek(0)
        msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)

    def _unlock(f: IO[bytes]) -> None:
        f.seek(0)
        msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)

else:
    import fcntl

    def _lock(f: IO[bytes]) -> None:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock(f: IO[bytes]) -> None:
        fcntl.flock(f.fileno(), fcntl.LOCK_UN)
