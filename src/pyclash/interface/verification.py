"""Verificación manual en el juego real (issue #33): una lista de comprobaciones, de lo más seguro a lo que
gasta, que la pantalla de pruebas lanza contra el dispositivo de verdad.

Sin Qt: cada comprobación recibe un `Context` y devuelve líneas legibles, así que se prueba con `FakeDevice`.

Tres niveles de riesgo:
  - `read`:  no pulsa nada, solo captura y lee.
  - `dry`:   puede abrir y cerrar menús, pero no selecciona ni confirma nada (no gasta).
  - `spend`: ejecuta la tarea de verdad y puede gastar recursos; la pantalla pide confirmación antes.
"""

from __future__ import annotations

import dataclasses
import random
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Literal

import numpy as np
from PIL import Image

from ..device.base import Device, DeviceError
from ..policy.build_new import BuildSettings
from ..policy.upgrades import Mode, UpgradeSettings
from ..runtime.clock import Clock, SystemClock
from ..runtime.context import Context
from ..runtime.events import Event, WallBatchPlanned
from ..runtime.stop import StopRequested, StopToken
from ..screens.battle_bar import BarReader
from ..screens.classifier import Screen, ScreenClassifier
from ..screens.loot import read_enemy_loot, read_hud_gold, read_next_cost, read_search_cost
from ..screens.village import VillageReader
from ..tasks.base import Outcome, Task, TaskResult, run_task
from ..tasks.build_new import BuildNewConfig, BuildNewTask
from ..tasks.farm import FarmConfig, FarmTask
from ..tasks.panel import PanelTask
from ..tasks.upgrade import UpgradeConfig, UpgradeTask
from ..tasks.walls import WallBatchTask, WallConfig, WallInputs
from ..vision.base import MetadataVision

Risk = Literal["read", "dry", "spend"]

RISK_LABELS = {
    "read": "Solo leer (no pulsa nada)",
    "dry": "Simulacro (abre y cierra menús, no gasta)",
    "spend": "Con gasto real (pide confirmación)",
}
DEFAULT_CAPTURE_DIR = Path("captures_raw")  # ignorado por git: las capturas llevan datos personales


@dataclass
class CheckOutcome:
    lines: list[str] = field(default_factory=list)
    result: TaskResult | None = None
    stopped: bool = False

    @property
    def ok(self) -> bool:
        """Verde si no hubo error: `nothing_to_do` es normal (p. ej. no hay constructores libres)."""
        if self.result is None:
            return not self.stopped
        return self.result.outcome in (Outcome.DONE, Outcome.NOTHING_TO_DO)


@dataclass(frozen=True)
class Check:
    key: str
    label: str
    risk: Risk
    help: str
    run: Callable[[Context, Path], CheckOutcome]


def format_event(event: Event) -> str:
    fields = ", ".join(f"{f.name}={getattr(event, f.name)!r}" for f in dataclasses.fields(event))
    return f"· {type(event).__name__}({fields})"


def describe_result(result: TaskResult) -> str:
    return f"Resultado: {result.outcome.value}" + (f" — {result.reason}" if result.reason else "")


def save_frame(image: np.ndarray, directory: Path, prefix: str = "manual") -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{prefix}-{int(time.time() * 1000)}.png"
    Image.fromarray(image).save(path)
    return path


# ---------------------------------------------------------------------------------- solo leer


def _check_screen(ctx: Context, _dir: Path) -> CheckOutcome:
    image = ctx.device.capture().image
    classifier = ScreenClassifier()
    got = classifier.classify(image)
    lines = [f"Pantalla: {got.screen.value} (confianza {got.confidence:.2f})"]
    if got.reason:
        lines.append(f"Motivo: {got.reason}")
    for screen, conf in got.candidates:
        lines.append(f"  candidata {screen.value}: {conf:.2f}")
    if got.screen is not Screen.MAIN_VILLAGE:
        why = classifier.explain(image, Screen.MAIN_VILLAGE)
        if why:
            lines.append(f"Para la aldea: {why}")
    return CheckOutcome(lines)


def _check_village(ctx: Context, _dir: Path) -> CheckOutcome:
    state = VillageReader().read_state_stable(ctx)
    lines = [
        f"Constructores (libres/total): {state.builders}",
        f"Laboratorio: {state.lab}",
        f"Recursos (oro, elixir, oscuro): {state.resources}",
        f"Almacén de oro lleno: {state.gold_full}; de elixir: {state.elixir_full}",
    ]
    if state.unreliable:
        lines.append(f"SIN ACUERDO entre capturas (no fiable): {', '.join(sorted(state.unreliable))}")
    else:
        lines.append("Lectura estable: todas las capturas coinciden.")
    return CheckOutcome(lines)


def _check_loot(ctx: Context, _dir: Path) -> CheckOutcome:
    image = ctx.device.capture().image
    loot = read_enemy_loot(image)
    lines = [
        f"Botín del rival — oro {loot.gold}, elixir {loot.elixir}, oscuro {loot.dark} (fila oscura: {loot.dark_row})",
        f"Coste de «Next»: {read_next_cost(image)}",
        f"Coste de «Find a Match»: {read_search_cost(image)}",
        f"Oro propio del HUD de exploración: {read_hud_gold(image)}",
        "Ponte en la pantalla de exploración (rival a la vista) para el primero; en el menú de ataque para el segundo.",
    ]
    return CheckOutcome(lines)


def _check_bar(ctx: Context, _dir: Path) -> CheckOutcome:
    cards = BarReader().detect(ctx.device.capture().image)
    if not cards:
        return CheckOutcome(["No se detecta ninguna carta. Ponte en la pantalla de despliegue de una batalla."])
    lines = [f"Cartas detectadas: {len(cards)}"]
    lines += [f"  {c.kind.value if hasattr(c.kind, 'value') else c.kind} · x{c.count} · agotada={c.exhausted}" for c in cards]
    return CheckOutcome(lines)


def _check_capture(ctx: Context, directory: Path) -> CheckOutcome:
    frame = ctx.device.capture()
    path = save_frame(frame.image, directory)
    return CheckOutcome([f"Captura guardada en {path}", "Lleva datos personales: anonimízala con tools/anonymize.py antes de subirla."])


# ---------------------------------------------------------------------------------- tareas


def _run_task(task: Task, ctx: Context) -> CheckOutcome:
    result = run_task(task, ctx)
    return CheckOutcome([describe_result(result)], result)


class _ReadPanelTask(PanelTask):
    """Abre «Buildings & Heroes», lista lo que lee y lo cierra. No selecciona ni confirma nada."""

    name = "read_panel"

    def __init__(self, directory: Path) -> None:
        self._setup_panel(UpgradeConfig(capture_dir=directory), None, None, None)
        self.lines: list[str] = []

    def run(self, ctx: Context) -> TaskResult:
        back = self._to_village(ctx)
        if back is not None:
            return back
        rows = self._open_and_scan(ctx)
        if isinstance(rows, TaskResult):
            return rows
        scan = self.last_scan
        self.lines.append(f"Filas leídas: {len(rows)} en {scan.steps} paso(s); recorrido completo: {scan.reached_end}")
        for r in rows:
            cost = "gratis" if r.free else f"{r.cost} {r.resource}"
            red = "" if r.affordable is not False else " [EN ROJO]"
            self.lines.append(f"  {r.name or '¿?'} ×{r.quantity} · {cost}{red} · {r.kind} · «{r.section}» · conf {r.confidence:.2f}")
        back = self._close(ctx)
        return back or TaskResult.done()


def _check_panel(ctx: Context, directory: Path) -> CheckOutcome:
    task = _ReadPanelTask(directory)
    out = _run_task(task, ctx)
    out.lines = task.lines + out.lines
    return out


# En los simulacros no se reserva ningún constructor: así se ve qué decidiría el bot aunque solo quede uno
# libre. En los de gasto real se mantiene la reserva por defecto (1), que es lo que haría el bot de verdad.
_DRY_NOTE = "(Simulacro sin constructor de reserva: lo que decidiría con un constructor libre.)"


def _upgrade(mode: Mode) -> Callable[[Context, Path], CheckOutcome]:
    def run(ctx: Context, directory: Path) -> CheckOutcome:
        dry = mode is Mode.DRY_RUN
        settings = UpgradeSettings(mode=mode, reserve_builders=0 if dry else 1)
        out = _run_task(UpgradeTask(UpgradeConfig(settings=settings, capture_dir=directory)), ctx)
        if dry:
            out.lines.append(_DRY_NOTE)
        return out

    return run


def _walls(dry_run: bool) -> Callable[[Context, Path], CheckOutcome]:
    def run(ctx: Context, directory: Path) -> CheckOutcome:
        # Sin orquestador, lo que este normalmente le da a la tarea se da aquí: no hay mejoras pendientes que
        # tengan prioridad. El oro solo se ofrece en el simulacro (colchón 0): en el real, sin la reserva de la
        # #32, no se gasta oro y los muros solo gastan elixir.
        inputs = WallInputs(gold_floor=0 if dry_run else None, upgrades_pending=False)
        planned: list[WallBatchPlanned] = []
        ctx.events.subscribe(WallBatchPlanned, planned.append)
        out = _run_task(WallBatchTask(WallConfig(dry_run=dry_run, capture_dir=directory), inputs=lambda _ctx: inputs), ctx)
        if planned and planned[-1].resource == "elixir":
            out.lines.append(
                "Aviso: elige elixir porque el nivel de los muros no se reconoció (o es 5 o más). Los muros que suben "
                "al nivel 2-4 solo se pagan con oro; en el real, si el juego no ofrece el botón de elixir, no se pulsa nada."
            )
        out.lines.append(
            "(Simulacro con colchón de oro 0: también contempla el oro.)" if dry_run
            else "Nota: sin la reserva de oro (#32) los muros solo gastan elixir."
        )
        return out

    return run


def _build(dry_run: bool) -> Callable[[Context, Path], CheckOutcome]:
    def run(ctx: Context, directory: Path) -> CheckOutcome:
        settings = BuildSettings(dry_run=dry_run, reserve_builders=0 if dry_run else 1)
        out = _run_task(BuildNewTask(BuildNewConfig(settings=settings, capture_dir=directory)), ctx)
        if dry_run:
            out.lines.append(_DRY_NOTE)
        return out

    return run


def _farm(ctx: Context, directory: Path) -> CheckOutcome:
    return _run_task(FarmTask(FarmConfig(capture_dir=directory)), ctx)


CHECKS: tuple[Check, ...] = (
    Check("screen", "¿Qué pantalla ve?", "read", "Clasifica la captura actual y explica qué indicador falla.", _check_screen),
    Check("village", "Leer la aldea", "read", "Constructores, laboratorio, recursos y almacenes llenos (con acuerdo entre capturas).", _check_village),
    Check("loot", "Leer botín / costes", "read", "En exploración: botín del rival y coste de «Next».", _check_loot),
    Check("bar", "Leer barra de despliegue", "read", "En una batalla: cartas, contadores y cuáles están agotadas.", _check_bar),
    Check("capture", "Guardar captura", "read", "Guarda la pantalla actual en captures_raw/ (ignorado por git).", _check_capture),
    Check("panel", "Leer panel Buildings & Heroes", "dry", "Abre el panel, lista las filas con coste y color, y lo cierra.", _check_panel),
    Check("upgrade_dry", "Mejora (Dry run)", "dry", "Decide qué mejoraría y lo registra; no pulsa ninguna fila.", _upgrade(Mode.DRY_RUN)),
    Check("walls_dry", "Muros (Dry run)", "dry", "Calcula el lote de muros y lo registra; no lo arma.", _walls(True)),
    Check("build_dry", "Construir (Dry run)", "dry", "Decide qué edificio nuevo compraría; no lo coloca.", _build(True)),
    Check("upgrade", "Mejorar (REAL)", "spend", "Inicia mejoras de verdad (modo Maxer): gasta oro/elixir.", _upgrade(Mode.MAXER)),
    Check("walls", "Muros (REAL)", "spend", "Arma y confirma un lote de muros: gasta elixir.", _walls(False)),
    Check("build", "Construir (REAL)", "spend", "Compra y coloca edificios nuevos: gasta recursos.", _build(False)),
    Check("farm", "Farmeo (REAL)", "spend", "Busca partida (cuesta oro), ataca con Bárbaros y espera al resultado.", _farm),
)


def check_by_key(key: str) -> Check:
    return next(c for c in CHECKS if c.key == key)


# ---------------------------------------------------------------------------------- ejecución


def run_check(
    check: Check,
    device: Device,
    stop: StopToken | None = None,
    clock: Clock | None = None,
    capture_dir: Path = DEFAULT_CAPTURE_DIR,
    on_line: Callable[[str], None] | None = None,
) -> CheckOutcome:
    """Ejecuta una comprobación con un contexto real; nunca lanza: los fallos acaban en `lines`.

    `on_line` recibe en directo cada evento (la pantalla los muestra mientras la tarea trabaja).
    """
    ctx = Context(
        device=device,
        vision=MetadataVision(),
        clock=clock or SystemClock(),
        rng=random.Random(),
        stop=stop or StopToken(),
    )
    events: list[str] = []

    def on_event(event: Event) -> None:
        line = format_event(event)
        events.append(line)
        if on_line:
            on_line(line)

    ctx.events.subscribe(Event, on_event)
    try:
        outcome = check.run(ctx, capture_dir)
    except StopRequested:
        outcome = CheckOutcome(["Parado por el usuario."], stopped=True)
    except DeviceError as e:
        outcome = CheckOutcome([f"Error del dispositivo: {e}"], TaskResult.failed(str(e)))
    except Exception as e:  # noqa: BLE001 - esto es una herramienta de diagnóstico: se muestra todo, no se oculta
        outcome = CheckOutcome([f"Fallo inesperado: {type(e).__name__}: {e}"], TaskResult.failed(repr(e)))
    finally:
        if device.held:  # nunca dejar el ratón pulsado, pase lo que pase
            device.release()
    if on_line is None:  # sin directo, los eventos se devuelven con el resto
        outcome.lines = outcome.lines + events
    return outcome
