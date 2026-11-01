"""Esquema tipado y versionado de la configuración (issue #15).

Sin E/S: se parte de un `dict` leído de JSON y se devuelve un objeto con valores válidos más la lista de
problemas encontrados. Un valor malo nunca se corrige en silencio: se sustituye por el de defecto **y se
cuenta** (`ConfigProblem`). Quien guarda (`ConfigStore`) rechaza cualquier problema; quien lee los
muestra y sigue con el resto.

Aquí no viven secretos: los tokens y URLs de webhook están en `secrets.py` y estos ficheros son legibles.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from ..policy.upgrades import EXCLUDE_HEROES, Fallback, Mode, Order, UpgradeSettings

SCHEMA_VERSION = 1

# Los modos que se guardan: el simulacro (DRY_RUN) es una forma de ejecutar, no un perfil.
UPGRADE_MODES = (Mode.MAXER.value, Mode.RUSHER.value)
UPGRADE_ORDERS = tuple(o.value for o in Order)
UPGRADE_FALLBACKS = tuple(f.value for f in Fallback)
EXCLUSIONS = (EXCLUDE_HEROES,)
RESERVE_RANGE = (0, 5)
CAP_RANGE = (1, 99)

EARTHQUAKE_METHODS = ("Curve Placement", "Random Placement")
WALL_THRESHOLD_RANGE = (0, 20)
MINUTES_RANGE = (0, 999)
LOOT_RANGE = (0, 9_999_999)


# ---------------------------------------------------------------- problemas


@dataclass(frozen=True)
class ConfigProblem:
    path: str  # p. ej. «upgrade.reserve_builders»
    message: str

    def __str__(self) -> str:
        return f"{self.path}: {self.message}"


class ConfigError(ValueError):
    """Un ajuste se rechaza. `problems` dice qué campo y qué rango o valores valen."""

    def __init__(self, problems: list[ConfigProblem] | str):
        if isinstance(problems, str):
            problems = [ConfigProblem("", problems)]
        self.problems = problems
        super().__init__("; ".join(str(p) for p in problems))


class _Reader:
    """Lee campos de un `dict` con tipo y rango, apuntando los problemas con su ruta."""

    def __init__(self, raw: Any, prefix: str, problems: list[ConfigProblem]):
        self.prefix = prefix
        self.problems = problems
        if raw is None:
            raw = {}
        if not isinstance(raw, Mapping):
            self.problem("", f"debe ser un objeto, no {type(raw).__name__}")
            raw = {}
        self.raw: Mapping[str, Any] = raw

    def problem(self, key: str, message: str) -> None:
        path = ".".join(p for p in (self.prefix, key) if p)
        self.problems.append(ConfigProblem(path, message))

    def int(self, key: str, default: int, lo: int, hi: int) -> int:
        if key not in self.raw:
            return default
        value = self.raw[key]
        if isinstance(value, bool) or not isinstance(value, int):
            self.problem(key, f"debe ser un número entero entre {lo} y {hi} (era {value!r}); se usa {default}")
            return default
        if not lo <= value <= hi:
            self.problem(key, f"debe estar entre {lo} y {hi} (era {value}); se usa {default}")
            return default
        return value

    def optional_int(self, key: str, lo: int, hi: int) -> int | None:
        value = self.raw.get(key)
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, int) or not lo <= value <= hi:
            self.problem(key, f"debe ser un entero entre {lo} y {hi}, o vacío para «sin tope» (era {value!r}); se usa «sin tope»")
            return None
        return value

    def choice(self, key: str, default: str, options: tuple[str, ...]) -> str:
        if key not in self.raw:
            return default
        value = self.raw[key]
        if value not in options:
            self.problem(key, f"debe ser uno de {', '.join(options)} (era {value!r}); se usa {default}")
            return default
        return value

    def boolean(self, key: str, default: bool) -> bool:
        if key not in self.raw:
            return default
        value = self.raw[key]
        if not isinstance(value, bool):
            self.problem(key, f"debe ser verdadero o falso (era {value!r}); se usa {default}")
            return default
        return value

    def text(self, key: str, default: str = "") -> str:
        if key not in self.raw:
            return default
        value = self.raw[key]
        if value is None:
            return default
        if not isinstance(value, str):
            self.problem(key, f"debe ser texto (era {value!r})")
            return default
        return value.strip()

    def names(self, key: str, allowed: tuple[str, ...] | None = None) -> tuple[str, ...]:
        value = self.raw.get(key, [])
        if not isinstance(value, (list, tuple)):
            self.problem(key, f"debe ser una lista (era {value!r})")
            return ()
        out: list[str] = []
        for i, item in enumerate(value):
            if not isinstance(item, str) or not item.strip():
                self.problem(f"{key}[{i}]", f"debe ser un texto no vacío (era {item!r}); se descarta")
            elif allowed is not None and item not in allowed:
                self.problem(f"{key}[{i}]", f"debe ser uno de {', '.join(allowed)} (era {item!r}); se descarta")
            else:
                out.append(item.strip())
        return tuple(out)

    def tiers(self, key: str) -> tuple[tuple[str, ...], ...]:
        value = self.raw.get(key, [])
        if not isinstance(value, (list, tuple)):
            self.problem(key, f"debe ser una lista de niveles (era {value!r})")
            return ()
        tiers: list[tuple[str, ...]] = []
        for i, tier in enumerate(value):
            if not isinstance(tier, (list, tuple)):
                self.problem(f"{key}[{i}]", f"cada nivel debe ser una lista de nombres (era {tier!r}); se descarta")
                continue
            names = _Reader({"n": list(tier)}, "", []).names("n")  # los nombres malos se descartan sin más
            if len(names) != len(tier):
                self.problem(f"{key}[{i}]", "tenía nombres vacíos o que no eran texto; se descartan")
            if names:
                tiers.append(names)
        return tuple(tiers)


# ---------------------------------------------------------------- objetos


@dataclass(frozen=True)
class UpgradeProfile:
    mode: str = Mode.MAXER.value
    order: str = Order.PRICIEST.value
    reserve_builders: int = 1
    cap_per_cycle: int | None = None
    priorities: tuple[tuple[str, ...], ...] = ()
    fallback: str = Fallback.ANY.value
    excluded: tuple[str, ...] = ()

    def settings(self) -> UpgradeSettings:
        return UpgradeSettings(
            mode=Mode(self.mode),
            order=Order(self.order),
            reserve_builders=self.reserve_builders,
            cap_per_cycle=self.cap_per_cycle,
            priorities=tuple(tuple(t) for t in self.priorities),
            fallback=Fallback(self.fallback),
            excluded=frozenset(self.excluded),
        )


@dataclass(frozen=True)
class V1Pending:
    """Ajustes de la v1 que PyClash aún no usa (ataque y muros). Se conservan para cuando se construyan."""

    earthquake_method: str = EARTHQUAKE_METHODS[0]
    wall_upgrade_threshold_m: int = 3
    random_minutes_min: int = 0
    random_minutes_max: int = 0
    repeat_pause_min: int = 0
    repeat_pause_max: int = 0
    min_loot_gold: int = 0
    min_loot_elixir: int = 0
    min_loot_dark: int = 0


@dataclass(frozen=True)
class AccountProfile:
    name: str
    enabled: bool = True
    upgrade: UpgradeProfile = UpgradeProfile()
    from_v1: V1Pending = V1Pending()


@dataclass(frozen=True)
class WindowSelection:
    """Identidad de la ventana fijada a mano. Vacía = autodetección."""

    title: str = ""
    top_class: str = ""
    child_class: str = ""

    def is_set(self) -> bool:
        return bool(self.title.strip() or self.child_class.strip())


@dataclass(frozen=True)
class AppConfig:
    window: WindowSelection = WindowSelection()
    active_profile: str | None = None


# ---------------------------------------------------------------- lectura y validación


def _parse_upgrade(raw: Any, problems: list[ConfigProblem]) -> UpgradeProfile:
    r = _Reader(raw, "upgrade", problems)
    d = UpgradeProfile()
    return UpgradeProfile(
        mode=r.choice("mode", d.mode, UPGRADE_MODES),
        order=r.choice("order", d.order, UPGRADE_ORDERS),
        reserve_builders=r.int("reserve_builders", d.reserve_builders, *RESERVE_RANGE),
        cap_per_cycle=r.optional_int("cap_per_cycle", *CAP_RANGE),
        priorities=r.tiers("priorities"),
        fallback=r.choice("fallback", d.fallback, UPGRADE_FALLBACKS),
        excluded=r.names("excluded", EXCLUSIONS),
    )


def _range_pair(r: _Reader, lo_key: str, hi_key: str) -> tuple[int, int]:
    low = r.int(lo_key, 0, *MINUTES_RANGE)
    high = r.int(hi_key, 0, *MINUTES_RANGE)
    if low > 0 and high < low:
        r.problem(hi_key, f"no puede ser menor que {lo_key} ({low}); se usa {low}")
        high = low
    return low, high


def _parse_v1(raw: Any, problems: list[ConfigProblem]) -> V1Pending:
    r = _Reader(raw, "from_v1", problems)
    d = V1Pending()
    rand_min, rand_max = _range_pair(r, "random_minutes_min", "random_minutes_max")
    pause_min, pause_max = _range_pair(r, "repeat_pause_min", "repeat_pause_max")
    return V1Pending(
        earthquake_method=r.choice("earthquake_method", d.earthquake_method, EARTHQUAKE_METHODS),
        wall_upgrade_threshold_m=r.int("wall_upgrade_threshold_m", d.wall_upgrade_threshold_m, *WALL_THRESHOLD_RANGE),
        random_minutes_min=rand_min,
        random_minutes_max=rand_max,
        repeat_pause_min=pause_min,
        repeat_pause_max=pause_max,
        min_loot_gold=r.int("min_loot_gold", 0, *LOOT_RANGE),
        min_loot_elixir=r.int("min_loot_elixir", 0, *LOOT_RANGE),
        min_loot_dark=r.int("min_loot_dark", 0, *LOOT_RANGE),
    )


def parse_profile(raw: Any, name_hint: str = "") -> tuple[AccountProfile, list[ConfigProblem]]:
    problems: list[ConfigProblem] = []
    r = _Reader(raw, "", problems)
    name = r.text("name", name_hint)
    if not name:
        name = name_hint
        if not name:
            r.problem("name", "falta el nombre de la cuenta")
    profile = AccountProfile(
        name=name,
        enabled=r.boolean("enabled", True),
        upgrade=_parse_upgrade(r.raw.get("upgrade"), problems),
        from_v1=_parse_v1(r.raw.get("from_v1"), problems),
    )
    return profile, problems


def parse_app(raw: Any) -> tuple[AppConfig, list[ConfigProblem]]:
    problems: list[ConfigProblem] = []
    r = _Reader(raw, "", problems)
    w = _Reader(r.raw.get("window"), "window", problems)
    window = WindowSelection(w.text("title"), w.text("top_class"), w.text("child_class"))
    active = r.text("active_profile") or None
    return AppConfig(window, active), problems


def validate_profile(profile: AccountProfile) -> list[ConfigProblem]:
    """Problemas de un perfil ya construido (lo que `ConfigStore` comprueba antes de guardar)."""
    return parse_profile(profile_to_dict(profile))[1]


def validate_app(app: AppConfig) -> list[ConfigProblem]:
    return parse_app(app_to_dict(app))[1]


# ---------------------------------------------------------------- escritura


def _plain(value: Any) -> Any:
    if isinstance(value, tuple):
        return [_plain(v) for v in value]
    if isinstance(value, list):
        return [_plain(v) for v in value]
    return value


def profile_to_dict(profile: AccountProfile) -> dict[str, Any]:
    body = {k: _plain(v) for k, v in dataclasses.asdict(profile).items()}
    body["upgrade"] = {k: _plain(v) for k, v in dataclasses.asdict(profile.upgrade).items()}
    return {"version": SCHEMA_VERSION, **body}


def app_to_dict(app: AppConfig) -> dict[str, Any]:
    return {"version": SCHEMA_VERSION, "window": dataclasses.asdict(app.window), "active_profile": app.active_profile}


# ---------------------------------------------------------------- versiones

Migration = Callable[[dict[str, Any]], dict[str, Any]]

# De la versión N a la N+1, por tipo de fichero. Vacío mientras el formato sea el 1: cuando cambie, se añade
# aquí el paso y `SCHEMA_VERSION` sube.
MIGRATIONS: dict[str, dict[int, Migration]] = {"app": {}, "profile": {}}


def migrate(
    data: Mapping[str, Any],
    kind: str,
    current: int = SCHEMA_VERSION,
    migrations: Mapping[int, Migration] | None = None,
) -> tuple[dict[str, Any], int, list[ConfigProblem]]:
    """Lleva un fichero a la versión actual sin perder valores.

    Devuelve los datos, la versión con que se encontró el fichero y los problemas. Un fichero de una
    versión **más nueva** que la del programa se lee con lo que se entiende, y el almacén se negará a
    sobrescribirlo (ver `ConfigStore`).
    """
    steps = MIGRATIONS[kind] if migrations is None else migrations
    problems: list[ConfigProblem] = []
    out = dict(data)
    found = out.get("version")
    if found is None:
        problems.append(ConfigProblem("version", "falta; se asume la versión 1"))
        found = 1
    elif isinstance(found, bool) or not isinstance(found, int) or found < 1:
        problems.append(ConfigProblem("version", f"no es una versión válida ({found!r}); se asume la actual"))
        out["version"] = current
        return out, current, problems
    version = found
    if version > current:
        problems.append(
            ConfigProblem("version", f"el fichero es de una versión más nueva ({version}) que la de este programa ({current})")
        )
        return out, version, problems
    while version < current:
        step = steps.get(version)
        if step is None:
            problems.append(ConfigProblem("version", f"no hay migración de la versión {version} a la {version + 1}"))
            break
        out = step(out)
        version += 1
        out["version"] = version
    return out, found, problems


# ---------------------------------------------------------------- en vivo o al reiniciar

LIVE = "live"
RESTART = "restart"

# Qué se aplica al siguiente límite de tarea (en vivo) y qué espera a reiniciar. Lo que no figura espera: es
# lo seguro. El modo cambia lo que el bot se permite hacer (Rusher sube el Ayuntamiento, y eso no se deshace).
SETTING_APPLIES: dict[str, str] = {
    "upgrade.order": LIVE,
    "upgrade.reserve_builders": LIVE,
    "upgrade.cap_per_cycle": LIVE,
    "upgrade.priorities": LIVE,
    "upgrade.fallback": LIVE,
    "upgrade.excluded": LIVE,
    "upgrade.mode": RESTART,
    "window": RESTART,
    "active_profile": RESTART,
    "enabled": RESTART,
    "name": RESTART,
    "from_v1": RESTART,
}


def applies_when(path: str) -> str:
    parts = path.split(".")
    for end in range(len(parts), 0, -1):
        found = SETTING_APPLIES.get(".".join(parts[:end]))
        if found:
            return found
    return RESTART


@dataclass(frozen=True)
class SettingChange:
    path: str
    old: Any
    new: Any
    applies: str  # LIVE o RESTART


def _flatten(value: Any, prefix: str = "") -> dict[str, Any]:
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        value = dataclasses.asdict(value)
    if isinstance(value, Mapping):
        out: dict[str, Any] = {}
        for k, v in value.items():
            out.update(_flatten(v, f"{prefix}.{k}" if prefix else str(k)))
        return out
    return {prefix: _plain(value)}


def changes(old: AccountProfile | AppConfig, new: AccountProfile | AppConfig) -> list[SettingChange]:
    """Qué ajustes difieren y si cada uno se aplica en vivo o al siguiente arranque."""
    before, after = _flatten(old), _flatten(new)
    return [
        SettingChange(path, before.get(path), after.get(path), applies_when(path))
        for path in sorted(before.keys() | after.keys())
        if before.get(path) != after.get(path)
    ]


def restart_needed(old: AccountProfile | AppConfig, new: AccountProfile | AppConfig) -> list[SettingChange]:
    return [c for c in changes(old, new) if c.applies == RESTART]
