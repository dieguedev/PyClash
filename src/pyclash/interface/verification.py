"""Verificación manual en el juego real (issue #33): una lista de comprobaciones, de lo más seguro a lo que
gasta, que la pantalla de pruebas lanza contra el dispositivo de verdad.

Sin Qt: cada comprobación recibe un `Context` y devuelve líneas legibles, así que se prueba con `FakeDevice`.

Cuatro niveles de riesgo:
  - `read`:  no pulsa nada, solo captura y lee.
  - `dry`:   puede abrir y cerrar menús, pero no selecciona ni confirma nada (no gasta).
  - `write`: no toca el juego, pero escribe en tu configuración de PyClash; la pantalla pide confirmación antes.
  - `spend`: ejecuta la tarea de verdad y puede gastar recursos; la pantalla pide confirmación antes.
"""

from __future__ import annotations

import dataclasses
import random
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Literal

import cv2
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
from ..screens.escapes import find_escape
from ..screens.red_zone import read_red_zone, read_red_zone_frames
from ..screens.loot import read_enemy_loot, read_hud_gold, read_next_cost, read_search_cost
from ..screens.village import VillageReader
from ..tasks.base import Outcome, Task, TaskResult, run_task
from ..tasks.build_new import BuildNewConfig, BuildNewTask
from ..tasks.farm import FarmConfig, FarmTask
from ..policy.gold_reserve import GoldReserve
from ..tasks.panel import PanelTask
from ..tasks.read_search_cost import ReadSearchCost
from ..tasks.upgrade import UpgradeConfig, UpgradeTask
from ..tasks.walls import WallBatchTask, WallConfig, WallInputs
from ..tasks.zoom import ZoomOut, ZoomOutConfig
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


def _check_inactivity(ctx: Context, _dir: Path) -> CheckOutcome:
    """Dice si ve el aviso «Anyone there?» y dónde pulsaría «RELOAD GAME», sin pulsar."""
    image = ctx.device.capture().image
    classifier = ScreenClassifier()
    report = classifier.diagnose(image, only=[Screen.INACTIVITY_POPUP])[Screen.INACTIVITY_POPUP]
    lines = [report.describe(), f"Aviso de inactividad: {'SÍ' if report.matched else 'no'} se reconoce."]
    tap = find_escape(image, Screen.INACTIVITY_POPUP, classifier.finder)
    lines.append(f"Pulsaría en {(tap.x, tap.y)} ({tap.why})." if tap else "No localiza el botón: no pulsaría nada.")
    lines.append("Para probarlo de verdad, espera a que el juego lo muestre (o ponlo con la aldea inactiva unos minutos).")
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


def _check_bar(ctx: Context, directory: Path) -> CheckOutcome:
    image = ctx.device.capture().image
    cards = BarReader().detect(image)
    if not cards:
        return CheckOutcome(["No se detecta ninguna carta. Ponte en la pantalla de despliegue de una batalla."])
    lines = [f"Cartas detectadas: {len(cards)} (pantalla {image.shape[1]}×{image.shape[0]})"]
    for i, c in enumerate(cards, 1):
        kind = c.kind.value if hasattr(c.kind, "value") else c.kind
        r = c.rect
        lines.append(f"  {i}. {kind} · x{c.count} · agotada={c.exhausted} · caja x={r.x} y={r.y} {r.w}×{r.h} · se pulsaría en {c.center}")
    return CheckOutcome(lines)


def _check_card_tap(ctx: Context, directory: Path) -> CheckOutcome:
    """Pulsa la primera carta SIN desplegar nada y mide si cambió: ¿llega el toque a seleccionarla?"""
    before = ctx.device.capture().image
    cards = BarReader().detect(before)
    if not cards:
        return CheckOutcome(["No se detecta ninguna carta. Ponte en la exploración o en el despliegue de una batalla."])
    card = cards[0]
    ctx.device.tap(*card.center)
    ctx.sleep(0.6)
    after = ctx.device.capture().image
    r = card.rect
    # La carta elegida se ve distinta (resaltada o elevada): se mide el cambio en su caja ampliada por arriba.
    top = max(0, r.y - 30)
    a, b = before[top : r.y + r.h, r.x : r.x + r.w].astype(np.int16), after[top : r.y + r.h, r.x : r.x + r.w].astype(np.int16)
    change = float(np.abs(a - b).mean())
    rest = float(np.abs(before[: top - 1] .astype(np.int16) - after[: top - 1].astype(np.int16)).mean()) if top > 1 else 0.0
    path_b, path_a = save_frame(before, directory, "card-before"), save_frame(after, directory, "card-after")
    lines = [
        f"Pulsada la carta 1 en {card.center} (caja x={r.x} y={r.y} {r.w}×{r.h}). No se ha desplegado nada.",
        f"Cambio medio en la zona de la carta: {change:.1f} · en el resto de la pantalla: {rest:.1f}",
        "Si la carta se selecciona, su zona cambia claramente más que el resto (y se ve elevada o resaltada).",
        f"Antes: {path_b}",
        f"Después: {path_a}",
    ]
    return CheckOutcome(lines)


def _check_red_zone(ctx: Context, directory: Path) -> CheckOutcome:
    """Lee la zona roja en varios fotogramas (el contorno parpadea) y dice cuánto se ve en cada uno."""
    frames = []
    for _ in range(8):
        frames.append(ctx.device.capture().image)
        ctx.sleep(0.3)
    lines = [f"Fotogramas leídos: {len(frames)} (pantalla {frames[0].shape[1]}×{frames[0].shape[0]})"]
    for i, image in enumerate(frames, 1):
        one = read_red_zone(image)
        lines.append(f"  fotograma {i}: " + (f"zona de {one.zone.pixels} px (ratio {one.zone.ratio:.1f})" if one.zone else one.reason))
    both = read_red_zone_frames(frames)
    if both.zone is None:
        lines.append(f"COMBINADOS: no se encuentra zona roja — {both.reason}")
        return CheckOutcome(lines)
    lines.append(f"COMBINADOS: zona de {both.zone.pixels} px (ratio {both.zone.ratio:.1f}), envolvente de {len(both.zone.hull)} puntos")
    marked = np.ascontiguousarray(frames[0].copy())
    cv2.polylines(marked, [np.array(both.zone.hull, np.int32)], True, (0, 120, 255), 4)
    lines.append(f"Captura con la envolvente dibujada (naranja): {save_frame(marked, directory, 'red-zone')}")
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


def _upgrade_rusher_dry(ctx: Context, directory: Path) -> CheckOutcome:
    """Simulacro con las reglas del Rusher: qué elegiría, qué descarta y por qué, y si daría la cuenta por terminada."""
    settings = UpgradeSettings(mode=Mode.RUSHER, dry_run=True, reserve_builders=0)
    out = _run_task(UpgradeTask(UpgradeConfig(settings=settings, capture_dir=directory)), ctx)
    out.lines.append(_DRY_NOTE)
    return out


def _walls(dry_run: bool) -> Callable[[Context, Path], CheckOutcome]:
    def run(ctx: Context, directory: Path) -> CheckOutcome:
        # Sin orquestador, lo que este normalmente le da a la tarea se da aquí: no hay mejoras pendientes que
        # tengan prioridad. El oro solo se ofrece en el simulacro (colchón 0) o con la reserva (#32) ya leída.
        floor = 0 if dry_run else ctx.gold_reserve.floor(ctx.clock.now())
        inputs = WallInputs(gold_floor=floor, upgrades_pending=False)
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
            else (
                f"Reserva de oro: {floor}." if floor is not None
                else "Nota: reserva de oro sin leer (usa «Leer coste de búsqueda»): los muros solo gastan elixir."
            )
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


def _search_cost(ctx: Context, directory: Path) -> CheckOutcome:
    """Lee el coste de «Find a Match» sin pagar y muestra la reserva de oro que se deriva. Solo lectura."""
    task = ReadSearchCost()
    out = _run_task(task, ctx)
    if task.cost is None:
        out.lines.append("No se leyó el coste: con esto el bot no gastaría oro. Ponte en la aldea y con el juego en inglés.")
        return out
    reserve = GoldReserve()
    reserve.update(task.cost, task.cost, ctx.clock.now())
    out.lines.append(f"Coste de «Find a Match»: {task.cost}")
    out.lines.append(f"Reserva de oro (75 × {task.cost} + {task.cost}): {reserve.floor(ctx.clock.now())}")
    return out


def _zoom_out(ctx: Context, directory: Path) -> CheckOutcome:
    """Cierra el menú abierto con su X, aleja al máximo y guarda la captura final para verla. No gasta."""
    out = _run_task(ZoomOut(ZoomOutConfig(capture_dir=directory)), ctx)
    if out.result is not None and out.result.outcome is Outcome.DONE:
        path = save_frame(ctx.device.capture().image, directory, "zoom-final")
        out.lines.append(f"Captura final guardada en {path}")
    return out


CHECKS: tuple[Check, ...] = (
    Check("screen", "¿Qué pantalla ve?", "read", "Clasifica la captura actual y explica qué indicador falla.", _check_screen),
    Check("inactivity", "¿Ve el aviso de inactividad?", "read", "Con «Anyone there?» en pantalla: si lo reconoce y dónde pulsaría «RELOAD GAME». No pulsa.", _check_inactivity),
    Check("village", "Leer la aldea", "read", "Constructores, laboratorio, recursos y almacenes llenos (con acuerdo entre capturas).", _check_village),
    Check("loot", "Leer botín / costes", "read", "En exploración: botín del rival y coste de «Next».", _check_loot),
    Check("search_cost", "Leer coste de búsqueda", "dry", "Abre Battle, lee el coste de «Find a Match» sin pagar, cierra y muestra la reserva de oro. No gasta.", _search_cost),
    Check("bar", "Leer barra de despliegue", "read", "En una batalla: cartas, contadores y cuáles están agotadas.", _check_bar),
    Check("red_zone", "Leer zona roja (varios fotogramas)", "read", "En la exploración: cuánto se ve el contorno rojo en cada fotograma y combinado.", _check_red_zone),
    Check("card_tap", "Probar pulsar la carta (no despliega)", "dry", "Pulsa la primera carta de la barra y mide si cambia; guarda antes y después.", _check_card_tap),
    Check("zoom_out", "Zoom-out máximo de la aldea", "dry", "Cierra el menú abierto con su X (si lo hay), gira la rueda hasta el tope y guarda la captura final. No gasta.", _zoom_out),
    Check("capture", "Guardar captura", "read", "Guarda la pantalla actual en captures_raw/ (ignorado por git).", _check_capture),
    Check("panel", "Leer panel Buildings & Heroes", "dry", "Abre el panel, lista las filas con coste y color, y lo cierra.", _check_panel),
    Check("upgrade_dry", "Mejora (Dry run)", "dry", "Decide qué mejoraría y lo registra; no pulsa ninguna fila.", _upgrade(Mode.DRY_RUN)),
    Check("upgrade_rusher_dry", "Mejora Rusher (Dry run)", "dry", "Decide como Rusher (lista blanca hasta TH9) y lista lo que descarta y por qué, con el TH leído; no pulsa ninguna fila.", _upgrade_rusher_dry),
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
