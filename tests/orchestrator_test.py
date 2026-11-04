"""Orquestador de sesión (issue #14): pruebas por la costura de tarea, con un orquestador real.

Dispositivo falso, reloj y azar inyectados. Las tareas son dobles sencillos (no tocan el juego); el
clasificador y el lector de la aldea son dobles guionados para controlar la pantalla y el estado.
"""

import json
import random

import pytest

from pyclash.device.fake import Transition
from pyclash.policy.session import SessionPolicy, failure_cooldown, farm_window, idle_wait
from pyclash.runtime.clock import FakeClock
from pyclash.runtime.events import Error, Event, Idle, SessionEnded, StateChanged, TaskFinished, TaskStarted
from pyclash.runtime.session_log import LootSession
from pyclash.runtime.settings import SessionSettings
from pyclash.policy.loot import SessionStats
from pyclash.screens.classifier import Classification, Screen
from pyclash.screens.village import VillageState
from pyclash.tasks.base import Outcome, SessionView, ShouldRun, TaskResult
from pyclash.tasks.build_new import BuildNewTask
from pyclash.tasks.farm import FarmTask
from pyclash.tasks.farm_window import FarmWindowTask
from pyclash.tasks.orchestrator import Orchestrator
from pyclash.tasks.upgrade import UpgradeTask
from pyclash.tasks.walls import WallBatchTask, WallConfig
from pyclash.policy.walls import WallSettings

from helpers import fixture_frame, frame, make_ctx


# ------------------------------------------------------------------------------ dobles


class VillageScript:
    """Lector de la aldea que devuelve el estado que toque; puede disparar acciones por lectura."""

    def __init__(self, states, on_read=None):
        self.states = list(states) if isinstance(states, (list, tuple)) else [states]
        self.reads = 0
        self.on_read = on_read

    def read_state_stable(self, ctx, *a, **k):
        i = min(self.reads, len(self.states) - 1)
        self.reads += 1
        if self.on_read is not None:
            self.on_read(self.reads, ctx)
        return self.states[i]


class Screens:
    """Clasificador guionado: la pantalla actual la fija el test. `finder` no se usa (siempre aldea)."""

    finder = None

    def __init__(self, screen=Screen.MAIN_VILLAGE):
        self.screen = screen

    def classify(self, image):
        return Classification(self.screen, 1.0)


def state(free=2, total=5, gold_full=False, elixir_full=False):
    return VillageState(
        builders=(free, total), lab=None, gold_full=gold_full, elixir_full=elixir_full, resources=(1, 1, None)
    )


class Fake:
    """Tarea de prueba: devuelve los resultados guionados (el último se repite)."""

    def __init__(self, name, priority, results=None, proceed=True, retry=False):
        self.name = name
        self.priority = priority
        self.results = list(results or [TaskResult.done()])
        self.proceed = proceed
        self.retry_without_cooldown = retry
        self.runs = 0
        self.seen_settings = []

    def should_run(self, ctx, view):
        ok = self.proceed(ctx, view) if callable(self.proceed) else self.proceed
        return ShouldRun.yes() if ok else ShouldRun.no(f"{self.name} no procede")

    def run(self, ctx):
        self.seen_settings.append(ctx.settings)
        result = self.results[min(self.runs, len(self.results) - 1)]
        self.runs += 1
        if callable(result):
            return result(ctx)
        return result


def session(tasks, village=None, screens=None, policy=None, settings_source=None, seed=0, **kw):
    ctx = make_ctx([frame()], seed=seed)
    ctx.settings_source = settings_source
    seen = []
    ctx.events.subscribe(Event, seen.append)
    orch = Orchestrator(
        ctx,
        tasks,
        policy=policy or SessionPolicy(),
        village=village or VillageScript(state()),
        classifier=screens or Screens(),
        **kw,
    )
    return ctx, orch, seen


def of(seen, kind):
    return [e for e in seen if isinstance(e, kind)]


def stop_after(ctx, reads, village_states):
    """Lector que pide la parada tras `reads` lecturas del estado."""
    return VillageScript(village_states, on_read=lambda n, c: c.stop.request() if n >= reads else None)


# ------------------------------------------------------------------------------ política pura


def test_failure_cooldown_doubles_up_to_the_cap():
    p = SessionPolicy()
    assert [failure_cooldown(p, n) for n in (1, 2, 3, 4, 5, 9)] == [300, 600, 1200, 1800, 1800, 1800]


def test_idle_wait_is_random_within_range_and_not_constant():
    p, rng = SessionPolicy(), random.Random(7)
    waits = {round(idle_wait(p, rng)) for _ in range(20)}
    assert len(waits) > 10
    assert all(300 <= w <= 480 for w in waits)


def test_farm_window_within_15_to_30_minutes():
    rng = random.Random(1)
    assert all(900 <= farm_window(SessionPolicy(), rng) <= 1800 for _ in range(50))


# ------------------------------------------------------------------------------ R1: orden y límites


def test_first_task_by_priority_that_proceeds_runs_and_the_list_restarts_from_the_top():
    high = Fake("high", 10, [TaskResult.done()], proceed=lambda c, v: high.runs < 2)
    low = Fake("low", 20, [TaskResult.done()])
    ctx, orch, seen = session([low, high], village=None)
    orch.village = VillageScript(state(), on_read=lambda n, c: c.stop.request() if n >= 5 else None)
    orch.run()
    started = [e.task for e in of(seen, TaskStarted)]
    # «high» va antes aunque se pasó la lista al revés; al dejar de proceder, cede el turno a «low».
    assert started[:4] == ["high", "high", "low", "low"]


def test_a_failed_task_goes_into_cooldown_and_the_others_keep_working():
    broken = Fake("broken", 10, [TaskResult.failed("algo salió mal")])
    other = Fake("other", 20)
    ctx, orch, seen = session([broken, other])
    orch.village = stop_after(ctx, 4, state())
    orch.run()
    assert broken.runs == 1  # descansa 5 minutos
    assert other.runs >= 2
    assert not ctx.cooldowns.ready("session:broken")


def test_a_hung_task_is_cut_after_four_minutes_without_progress_and_logged_as_failed():
    def hang(ctx):
        while True:
            ctx.sleep(30)  # espera sin avanzar nunca

    stuck = Fake("stuck", 10, [hang])
    ctx, orch, seen = session([stuck])
    orch.village = stop_after(ctx, 2, state())
    orch.run()
    done = of(seen, TaskFinished)[0]
    assert (done.outcome, "sin avanzar" in done.reason) == ("failed", True)
    assert ctx.clock.now() < 400  # no esperó una hora


def test_progress_keeps_a_long_but_active_task_alive():
    def search(ctx):
        for _ in range(10):  # 10 minutos en total, pero avanza cada minuto
            ctx.sleep(60)
            ctx.progress()
        return TaskResult.done()

    active = Fake("search", 10, [search])
    ctx, orch, seen = session([active])
    orch.village = stop_after(ctx, 2, state())
    orch.run()
    assert of(seen, TaskFinished)[0].outcome == "done"


def test_a_timeout_runs_the_screen_recovery_with_permission_to_leave_a_battle():
    screens = Screens(Screen.RESULT)  # se quedó en el resumen del combate

    def hang(ctx):
        ctx.sleep(1000)

    stuck = Fake("stuck", 10, [hang])
    ctx, orch, seen = session([stuck], screens=screens)
    tapped = []
    orch._recover_after_timeout = lambda: tapped.append(True)
    orch.village = stop_after(ctx, 2, state())
    orch.run()
    assert tapped


def test_three_consecutive_failures_stop_the_session_with_a_clear_message():
    bad = Fake("upgrade_village", 10, [TaskResult.failed("no se pudo verificar")], retry=True)
    ctx, orch, seen = session([bad])
    result = orch.run()
    assert result.error and "upgrade_village" in result.reason and "3 veces seguidas" in result.reason
    assert bad.runs == 3  # reintentos sin cooldown
    assert of(seen, Error)[0].message == result.reason
    assert of(seen, SessionEnded)[0].error is True


def test_a_done_in_between_resets_the_consecutive_failures():
    flaky = Fake("flaky", 10, [TaskResult.failed("x"), TaskResult.failed("x"), TaskResult.done(),
                               TaskResult.failed("x"), TaskResult.failed("x"), TaskResult.done()], retry=True)
    ctx, orch, seen = session([flaky])
    orch.village = stop_after(ctx, 7, state())
    result = orch.run()
    assert not result.error


def test_nothing_to_do_and_blocked_do_not_count_as_failures():
    calm = Fake("calm", 10, [TaskResult.nothing_to_do("nada")])
    blocked = Fake("blocked", 20, [TaskResult.blocked("idioma")])
    ctx, orch, seen = session([calm, blocked])
    orch.village = stop_after(ctx, 12, state())
    result = orch.run()
    assert not result.error
    assert orch.failures.get("calm", 0) == 0 and orch.failures.get("blocked", 0) == 0


# ------------------------------------------------------------------------------ R2: idle


def test_no_builders_and_full_storages_goes_idle_with_the_reason_then_resumes_when_one_frees():
    full = state(free=0, gold_full=True, elixir_full=True)
    free = state(free=2)
    build = Fake("build_new", 10, proceed=lambda c, v: (v.village.free_builders or 0) > 1)
    farm = Fake("village_farming", 40, proceed=lambda c, v: not v.village.storages_full)
    ctx, orch, seen = session([build, farm])
    orch.village = VillageScript([full, full, free], on_read=lambda n, c: c.stop.request() if n >= 4 else None)
    orch.run()
    idles = of(seen, Idle)
    assert len(idles) == 2 and all(300 <= i.seconds <= 480 for i in idles)
    assert "build_new" in idles[0].reason and "village_farming" in idles[0].reason
    assert idles[0].seconds != idles[1].seconds
    assert build.runs >= 1  # en cuanto hay un constructor libre, vuelve al trabajo


def test_busy_builders_with_room_in_the_storages_keeps_farming_until_they_fill():
    build = Fake("build_new", 10, proceed=lambda c, v: (v.village.free_builders or 0) > 1)
    farm = Fake("village_farming", 40, proceed=lambda c, v: not v.village.storages_full)
    busy_room = state(free=0)
    busy_full = state(free=0, gold_full=True, elixir_full=True)
    ctx, orch, seen = session([build, farm])
    orch.village = VillageScript(
        [busy_room, busy_room, busy_full], on_read=lambda n, c: c.stop.request() if n >= 4 else None
    )
    orch.run()
    assert farm.runs == 2
    assert len(of(seen, Idle)) >= 1


def test_without_a_time_limit_the_session_only_ends_when_the_user_stops():
    calm = Fake("calm", 10, proceed=False)
    ctx, orch, seen = session([calm])
    orch.village = VillageScript(state(), on_read=lambda n, c: c.stop.request() if n >= 50 else None)
    result = orch.run()
    assert (result.error, result.reason) == (False, "parada solicitada")
    assert ctx.clock.now() > 50 * 300  # más de cuatro horas de espera simulada sin terminar sola


def test_walls_with_nothing_left_relax_the_builder_reserve():
    walls = Fake("upgrade_walls", 30, [TaskResult.nothing_to_do("sin muros", exhausted=True)])
    seen_flag = []
    build = Fake("build_new", 10, [lambda c: (seen_flag.append(c.walls_complete), TaskResult.done())[1]],
                 proceed=False)
    ctx, orch, seen = session([walls, build])
    orch.village = VillageScript(state(free=1), on_read=lambda n, c: (
        setattr(build, "proceed", True) if n == 3 else None, c.stop.request() if n >= 4 else None))
    orch.run()
    assert seen_flag == [True]


# ------------------------------------------------------------------------------ R3: cambios en vivo


def test_live_exclusions_apply_to_the_next_task_not_the_one_running():
    box = {"settings": SessionSettings()}
    during = []

    def first(ctx):
        box["settings"] = SessionSettings(excluded=frozenset({"heroes"}))  # el usuario lo cambia a mitad
        during.append(ctx.settings.excluded)
        return TaskResult.done()

    a = Fake("a", 10, [first], proceed=lambda c, v: a.runs < 1)
    b = Fake("b", 20, proceed=lambda c, v: a.runs >= 1)
    ctx, orch, seen = session([a, b], settings_source=lambda: box["settings"])
    orch.village = stop_after(ctx, 3, state())
    orch.run()
    assert during == [frozenset()]  # la tarea en curso no vio el cambio
    assert b.seen_settings[0].excluded == frozenset({"heroes"})  # la siguiente ya lo respeta


def test_a_task_switched_off_live_is_skipped():
    box = {"settings": SessionSettings()}
    a = Fake("a", 10, [lambda c: (box.update(settings=SessionSettings(disabled_tasks=frozenset({"a"}))), TaskResult.done())[1]])
    b = Fake("b", 20)
    ctx, orch, seen = session([a, b], settings_source=lambda: box["settings"])
    orch.village = stop_after(ctx, 4, state())
    orch.run()
    assert a.runs == 1 and b.runs >= 2


# ------------------------------------------------------------------------------ R4: parar y pausar


def test_stopping_mid_task_ends_cleanly_and_releases_the_input():
    def press_then_stop(ctx):
        ctx.device.press(5, 5)
        ctx.stop.request()
        ctx.sleep(1)  # punto de comprobación

    ctx, orch, seen = session([Fake("farm", 10, [press_then_stop])])
    ctx.device._caps = type(ctx.device.capabilities)(hold=True)
    result = orch.run()
    assert (result.error, result.reason) == (False, "parada solicitada")
    assert ctx.device.held is False
    assert of(seen, TaskFinished)[0].outcome == "stopped"
    assert of(seen, SessionEnded)[0].error is False


def test_stopping_with_a_menu_open_closes_it_with_its_escape():
    ctx = make_ctx([fixture_frame("upgrade_panel"), fixture_frame("main_village")], transitions=[Transition(0, "tap", 1)])
    ctx.stop.request()
    orch = Orchestrator(ctx, [Fake("a", 10)], village=VillageScript(state()))
    result = orch.run()
    assert result.reason == "parada solicitada"
    assert any(a.kind == "tap" for a in ctx.device.actions)  # cerró el menú aunque ya se había pedido parar


def test_stopping_during_a_battle_waits_for_it_to_end_and_never_surrenders():
    screens = Screens(Screen.BATTLE)
    ctx, orch, seen = session([Fake("farm", 10, [lambda c: (c.stop.request(), TaskResult.done())[1]])], screens=screens)
    ticks = {"n": 0}
    real = screens.classify

    def classify(image):
        ticks["n"] += 1
        if ticks["n"] > 6:  # el combate termina al cabo de un rato
            screens.screen = Screen.MAIN_VILLAGE
        return real(image)

    screens.classify = classify
    orch.run()
    assert not any(a.kind == "tap" for a in ctx.device.actions)  # nada de «Surrender»
    assert ctx.clock.now() > 0  # esperó (reloj simulado)


def test_pause_waits_at_the_task_boundary_and_resumes_with_the_state_kept():
    class PausingClock(FakeClock):
        def __init__(self, ctx_holder):
            super().__init__()
            self.holder = ctx_holder
            self.paused_for = 0.0

        def sleep(self, seconds):
            super().sleep(seconds)
            ctx = self.holder.get("ctx")
            if ctx is not None and ctx.pause.requested:
                self.paused_for += seconds
                if self.paused_for >= 5:
                    ctx.pause.clear()

    holder = {}
    first = Fake("a", 10, [lambda c: (c.pause.request(), TaskResult.done())[1]], proceed=lambda c, v: first.runs < 1)
    second = Fake("b", 20, proceed=lambda c, v: first.runs >= 1)
    ctx, orch, seen = session([first, second])
    clock = PausingClock(holder)
    ctx.clock = clock
    ctx.cooldowns._clock = clock
    holder["ctx"] = ctx
    orch.village = stop_after(ctx, 3, state())
    orch.run()
    states = [e.state for e in of(seen, StateChanged)]
    assert states.index("paused") < len(states) - 1 and "running" in states[states.index("paused"):]
    assert first.runs >= 1 and second.runs >= 1  # sigue trabajando tras reanudar


def test_an_unrecoverable_screen_stops_the_session_with_the_recovery_reason():
    screens = Screens(Screen.LOGIN)
    ctx, orch, seen = session([Fake("a", 10)], screens=screens)
    result = orch.run()
    assert result.error and "Supercell" in result.reason


# ------------------------------------------------------------------------------ farmeo por ventanas


class FakeFarm:
    name = "village_farming"

    def __init__(self, ctx_clock, seconds_per_attack=120.0, results=None):
        self.clock = ctx_clock
        self.seconds = seconds_per_attack
        self.results = results or []
        self.attacks = 0

    def should_run(self, ctx, view):
        return ShouldRun.no("almacenes llenos") if view.village.storages_full else ShouldRun.yes()

    def run(self, ctx):
        self.attacks += 1
        ctx.clock.sleep(self.seconds)  # el ataque dura lo que dure y no se interrumpe
        if self.results:
            return self.results.pop(0)
        return TaskResult.done()


def window_task(ctx, farm, village, policy=None, on_attack=None):
    return FarmWindowTask(farm=farm, policy=policy or SessionPolicy(), village=village, on_attack=on_attack)


def test_farm_window_attacks_until_the_random_window_is_over_without_cutting_an_attack():
    ctx = make_ctx([frame()], seed=3)
    farm = FakeFarm(ctx.clock, 200.0)
    saved = []
    task = window_task(ctx, farm, VillageScript(state()), on_attack=lambda: saved.append(ctx.clock.now()))
    result = task.run(ctx)
    expected = farm_window(SessionPolicy(), random.Random(3))
    assert result.outcome is Outcome.DONE
    assert farm.attacks == len(saved)
    # Se pasó de la ventana como mucho lo que dura un ataque: el último terminó entero.
    assert expected <= ctx.clock.now() < expected + 200.0 + 1


def test_farm_window_ends_early_when_the_storages_fill():
    ctx = make_ctx([frame()])
    farm = FakeFarm(ctx.clock, 100.0)
    village = VillageScript([state(), state(), state(gold_full=True, elixir_full=True)])
    result = window_task(ctx, farm, village).run(ctx)
    assert farm.attacks == 3 and "almacenes llenos" in result.reason


def test_farm_window_ends_when_a_pause_is_requested_between_attacks():
    ctx = make_ctx([frame()])
    farm = FakeFarm(ctx.clock, 100.0)
    ctx.pause.request()
    result = window_task(ctx, farm, VillageScript(state())).run(ctx)
    assert farm.attacks == 1 and "pausa" in result.reason


def test_farm_window_ends_when_farming_is_switched_off_live():
    ctx = make_ctx([frame()])
    ctx.settings_source = lambda: SessionSettings(disabled_tasks=frozenset({"village_farming"}))
    farm = FakeFarm(ctx.clock, 100.0)
    result = window_task(ctx, farm, VillageScript(state())).run(ctx)
    assert farm.attacks == 1 and "desactivado" in result.reason


def test_a_failed_attack_is_returned_so_the_orchestrator_counts_it_and_retries_without_cooldown():
    ctx = make_ctx([frame()])
    farm = FakeFarm(ctx.clock, 10.0, results=[TaskResult.failed("búsqueda atascada")] * 3)
    task = window_task(ctx, farm, VillageScript(state()))
    orch = Orchestrator(ctx, [task], village=VillageScript(state()), classifier=Screens())
    result = orch.run()
    assert result.error and "village_farming" in result.reason
    assert farm.attacks == 3  # tres intentos seguidos, sin descansar entre ellos
    assert ctx.cooldowns.ready("session:village_farming")


# ------------------------------------------------------------------------------ should_run de las tareas


def test_builder_tasks_respect_the_reserve_unless_the_walls_are_complete():
    ctx = make_ctx([frame()])
    view = SessionView(state(free=1))
    for task in (BuildNewTask(), UpgradeTask()):
        assert not task.should_run(ctx, view).proceed  # 1 libre y reserva de 1
        ctx.walls_complete = True
        assert task.should_run(ctx, view).proceed  # sin muros, el último constructor es para mejorar
        ctx.walls_complete = False
        assert task.should_run(ctx, SessionView(state(free=2))).proceed


def test_builder_tasks_do_not_proceed_with_unknown_builders():
    ctx = make_ctx([frame()])
    unknown = SessionView(VillageState(None, None, None, None, None))
    assert not BuildNewTask().should_run(ctx, unknown).proceed


def test_walls_take_the_last_free_builder_only():
    ctx = make_ctx([frame()])
    walls = WallBatchTask(WallConfig(settings=WallSettings(reserve_builder_for_walls=True)))
    assert not walls.should_run(ctx, SessionView(state(free=0))).proceed
    assert not walls.should_run(ctx, SessionView(state(free=2))).proceed  # las mejoras van primero
    assert walls.should_run(ctx, SessionView(state(free=1))).proceed


def test_farming_does_not_proceed_with_both_storages_full():
    ctx = make_ctx([frame()])
    farm = FarmTask()
    assert farm.should_run(ctx, SessionView(state())).proceed
    assert farm.should_run(ctx, SessionView(state(gold_full=True))).proceed  # uno con hueco: se sigue
    assert not farm.should_run(ctx, SessionView(state(gold_full=True, elixir_full=True))).proceed


def test_the_battle_timeout_stays_under_the_inactivity_limit():
    from pyclash.tasks.farm import FarmConfig

    assert FarmConfig().battle_timeout_s < SessionPolicy().inactivity_s


# ------------------------------------------------------------------------------ botín de la sesión


def test_loot_checkpoint_survives_a_hard_crash_and_is_recovered_as_interrupted(tmp_path):
    path = tmp_path / "sessions.jsonl"
    clock = FakeClock()
    stats = SessionStats()
    first = LootSession(stats, clock, path, wall_now=lambda: "t0")
    first.__enter__()
    stats.record((1000, 2000, 0))
    clock.sleep(60)
    first.checkpoint()  # el proceso muere aquí: nunca se llama a __exit__

    second = LootSession(SessionStats(), clock, path, wall_now=lambda: "t1")
    with second:
        pass
    lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert lines[0]["oro"] == 1000 and lines[0]["interrumpida"] is True
    assert "interrumpida" not in lines[1]
    assert not (tmp_path / "session_partial.json").exists()


def test_a_normal_close_replaces_the_partial_with_the_final_record(tmp_path):
    path = tmp_path / "sessions.jsonl"
    stats = SessionStats()
    with LootSession(stats, FakeClock(), path, wall_now=lambda: "t") as s:
        s.record((5, 6, 0))
    assert not (tmp_path / "session_partial.json").exists()
    assert len(path.read_text(encoding="utf-8").splitlines()) == 1


def test_the_orchestrator_saves_the_loot_after_each_task(tmp_path):
    path = tmp_path / "sessions.jsonl"
    stats = SessionStats()
    loot = LootSession(stats, FakeClock(), path, wall_now=lambda: "t")
    task = Fake("a", 10, [lambda c: (stats.record((10, 20, 0)), TaskResult.done())[1]])
    ctx, orch, seen = session([task], loot=loot)
    saved = []
    original = loot.checkpoint
    loot.checkpoint = lambda: (saved.append(stats.total), original())[1]
    orch.village = stop_after(ctx, 3, state())
    orch.run()
    assert saved and saved[0] == (10, 20, 0)
    assert json.loads(path.read_text(encoding="utf-8").splitlines()[-1])["oro"] >= 10
