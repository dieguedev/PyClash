"""Farmeo de aldea por la costura de tarea: dispositivo falso con las capturas reales como pantallas."""

import random

from pyclash.device.base import Capabilities, Frame
from pyclash.policy.armies import ARMIES, Army
from pyclash.runtime.clock import FakeClock
from pyclash.runtime.context import Context
from pyclash.runtime.events import CaptureSaved, Error, Warning
from pyclash.tasks.base import Outcome, run_task
from pyclash.tasks.farm import AcceptFirst, FarmConfig, FarmTask
from pyclash.vision.base import MetadataVision

from helpers import fixture_frame

W, H = 1280, 720

SCREENS = {
    "village": "main_village",
    "menu": "attack_menu",
    "army": "army_selection",
    "scout": "scouting_barbarians",
    "gold": "need_more_gold_search",
    "battle": "battle",
    "gray": "attacking_no_more_barbarians",
    "result": "result",
}

# Botones, como fracciones (x1, y1, x2, y2) del fotograma.
BATTLE = (0.0, 0.89, 0.11, 0.99)
FIND = (0.09, 0.64, 0.27, 0.72)
ATTACK = (0.76, 0.82, 0.95, 0.89)
END_BATTLE = (0.0, 0.76, 0.13, 0.83)
RETURN_HOME = (0.40, 0.76, 0.60, 0.94)
GOLD_X = (0.62, 0.28, 0.72, 0.40)
GEM_BUTTON = (0.40, 0.54, 0.60, 0.69)  # el botón verde de «You need more Gold»
TERRAIN = (0.55, 0.15, 0.80, 0.45)  # terreno libre que cierra el menú de ataque


def in_box(box, x, y):
    return box[0] * W <= x <= box[2] * W and box[1] * H <= y <= box[3] * H


class FarmDevice:
    def __init__(self, clock, *, no_gold=False, gray_after=60, end_after=3, never_ends=False, stuck=False, hold=True, screens=None):
        self.clock = clock
        self.state = "village"
        self.no_gold, self.gray_after, self.end_after = no_gold, gray_after, end_after
        self.never_ends, self.stuck = never_ends, stuck
        self.capabilities = Capabilities(hold=hold)
        self.images = {
            k: (fixture_frame(v).image if isinstance(v, str) else v) for k, v in {**SCREENS, **(screens or {})}.items()
        }
        self.held = False
        self.taps, self.presses, self.moves, self.releases = [], [], [], []
        self.released_captures = 0
        self.on_move = None

    # --- Device
    def capture(self):
        if self.state in ("battle", "gray") and self.releases and not self.held and not self.never_ends:
            self.released_captures += 1
            if self.released_captures > self.end_after:
                self.state = "result"
        return Frame(self.images[self.state], (W, H), self.clock.now(), {})

    def tap(self, x, y):
        self.taps.append((x, y))
        s = self.state
        if s == "village" and in_box(BATTLE, x, y):
            self.state = "menu"
        elif s == "menu" and in_box(FIND, x, y) and not self.stuck:
            self.state = "army"
        elif s == "menu" and in_box(TERRAIN, x, y):
            self.state = "village"
        elif s == "army" and in_box(ATTACK, x, y):
            self.state = "gold" if self.no_gold else "scout"
        elif s == "gold" and in_box(GOLD_X, x, y):
            self.state = "scout"
        elif s == "scout" and in_box(END_BATTLE, x, y):
            self.state = "village"
        elif s == "result" and in_box(RETURN_HOME, x, y):
            self.state = "village"

    def press(self, x, y):
        if not self.capabilities.hold:
            raise NotImplementedError
        self.held = True
        self.presses.append((x, y))
        if self.state == "scout":
            self.state = "battle"

    def move(self, x, y):
        assert self.held, "move sin press"
        self.moves.append((x, y))
        if self.state == "battle" and len(self.moves) >= self.gray_after:
            self.state = "gray"
        if self.on_move:
            self.on_move(len(self.moves))

    def release(self):
        self.held = False
        self.releases.append(self.clock.now())

    def swipe(self, *a, **k): ...
    def scroll(self, *a, **k): ...


def make(**kw):
    clock = FakeClock()
    device = FarmDevice(clock, **{k: v for k, v in kw.items() if k in FarmDevice.__init__.__code__.co_varnames})
    ctx = Context(device=device, vision=MetadataVision(), clock=clock, rng=random.Random(kw.get("seed", 0)))
    return ctx, device


def events_of(ctx, kind):
    got = []
    ctx.events.subscribe(kind, got.append)
    return got


def test_full_attack_from_the_village_to_the_return_home():
    ctx, dev = make()
    result = run_task(FarmTask(), ctx)
    assert result.outcome is Outcome.DONE, result
    assert dev.state == "village"
    assert len(dev.presses) == 1 and len(dev.releases) == 1 and not dev.held
    assert 10 < len(dev.moves) < 200  # un gesto continuo que se corta al agotarse la carta
    assert not any(in_box(GEM_BUTTON, *t) for t in dev.taps)


def test_deployment_stops_when_the_card_is_exhausted():
    ctx, dev = make(gray_after=40)
    run_task(FarmTask(), ctx)
    # la carta se pone gris tras 40 movimientos y se mira cada 0.5 s (~25 puntos): se suelta poco después
    assert 40 <= len(dev.moves) <= 40 + 40


def test_the_pointer_follows_one_gesture_outside_the_red_zone():
    from pyclash.policy.deploy_path import inside
    from pyclash.screens.red_zone import read_red_zone

    ctx, dev = make()
    run_task(FarmTask(), ctx)
    zone = read_red_zone(fixture_frame("scouting_barbarians").image).zone
    hull = [tuple(map(float, p)) for p in zone.hull]
    points = dev.presses + dev.moves
    assert not any(inside(hull, x, y) for x, y in points)
    assert all(0.10 * H <= y <= 0.845 * H for _, y in points)  # sobre la barra de cartas


def test_the_task_waits_for_the_game_to_end_the_battle_and_never_surrenders():
    ctx, dev = make(end_after=40)
    result = run_task(FarmTask(), ctx)
    assert result.outcome is Outcome.DONE
    assert not any(in_box(END_BATTLE, *t) for t in dev.taps[-3:])  # no se pulsa Surrender: solo Return Home


def test_a_battle_that_never_ends_is_reported_but_not_cancelled():
    ctx, dev = make(never_ends=True)
    cfg = FarmConfig(battle_timeout_s=60.0)
    result = run_task(FarmTask(cfg), ctx)
    assert result.outcome is Outcome.FAILED and "no terminó" in result.reason
    surrender = (0.0, 0.76, 0.13, 0.83)
    assert not any(in_box(surrender, *t) for t in dev.taps[3:])  # tras empezar no se toca la zona de Surrender


def test_no_hold_capability_disables_the_strategy_with_a_clear_message():
    ctx, dev = make(hold=False)
    result = run_task(FarmTask(), ctx)
    assert result.outcome is Outcome.BLOCKED and "pulsación sostenida" in result.reason
    assert not dev.taps and not dev.presses


def test_stop_in_the_middle_releases_the_pointer_and_ends_the_task():
    ctx, dev = make()
    dev.on_move = lambda n: ctx.stop.request() if n == 12 else None
    result = run_task(FarmTask(), ctx)
    assert result.outcome is Outcome.STOPPED
    assert not dev.held and len(dev.releases) >= 1
    assert len(dev.moves) <= 13


def test_out_of_gold_closes_with_the_x_never_the_gem_button_and_blocks():
    ctx, dev = make(no_gold=True)
    warnings = events_of(ctx, Warning)
    result = run_task(FarmTask(), ctx)
    assert result.outcome is Outcome.BLOCKED and "oro insuficiente" in result.reason
    assert not any(in_box(GEM_BUTTON, *t) for t in dev.taps)
    assert any(in_box(GOLD_X, *t) for t in dev.taps)
    assert dev.state == "village"  # y sale de la búsqueda con End Battle
    assert not dev.presses
    assert any("gemas" in w.message for w in warnings)


def test_missing_troop_fails_before_touching_the_battle_and_leaves_the_search():
    archers = Army("barbarians_archers", "Bárbaros y Arqueras", ("barbarian", "archer"))
    ctx, dev = make()
    errors = events_of(ctx, Error)
    result = run_task(FarmTask(armies={"barbarians_archers": archers}, config=FarmConfig(army="barbarians_archers")), ctx)
    assert result.outcome is Outcome.FAILED and "Troop Archer not found" in result.reason
    assert not dev.presses
    assert dev.state == "village"  # End Battle, gratis
    assert errors


def test_the_red_zone_is_only_read_in_the_scouting_screen():
    # Si la pantalla ya no es la exploración (p. ej. empezó la batalla), no se lee zona ni se despliega.
    ctx, dev = make()
    dev.state = "battle"
    result = FarmTask()._attack(ctx, ARMIES["barbarians"])
    assert result.outcome is Outcome.FAILED and "exploración" in result.reason
    assert not dev.presses and not dev.moves


def test_a_village_with_a_split_red_zone_is_attacked():
    ctx, dev = make(screens={"scout": "scouting_split_zone"})
    result = run_task(FarmTask(), ctx)
    assert result.outcome is Outcome.DONE, result
    assert dev.moves


def test_a_chosen_army_the_bot_does_not_know_fails_clearly():
    ctx, dev = make()
    result = run_task(FarmTask(config=FarmConfig(army="giants_archers")), ctx)
    assert result.outcome is Outcome.FAILED and "ejército desconocido" in result.reason
    assert not dev.taps


def scouting_without_red_zone():
    """La exploración de siempre con el terreno central pintado de verde liso: sin contorno rojo."""
    image = fixture_frame("scouting_barbarians").image.copy()
    image[72:600, 200:1000] = (90, 150, 40)
    return image


def test_no_red_zone_means_no_deployment(tmp_path):
    ctx, dev = make(screens={"scout": scouting_without_red_zone()})
    saved = events_of(ctx, CaptureSaved)
    result = run_task(FarmTask(config=FarmConfig(capture_dir=tmp_path)), ctx)
    assert result.outcome is Outcome.FAILED and "zona roja" in result.reason
    assert not dev.presses
    assert dev.state == "village"
    assert saved and list(tmp_path.glob("farm-*.png"))


def test_a_search_that_never_advances_is_reset_and_then_fails():
    ctx, dev = make(stuck=True)
    warnings = events_of(ctx, Warning)
    result = run_task(FarmTask(config=FarmConfig(search_timeout_s=10.0, menu_timeout_s=4.0)), ctx)
    assert result.outcome is Outcome.FAILED and "atascada" in result.reason
    assert any("se reinicia" in w.message for w in warnings)
    assert not dev.presses


def test_a_village_the_acceptor_rejects_is_left_without_attacking():
    class Never:
        def accept(self, image):
            return False

    ctx, dev = make()
    result = run_task(FarmTask(acceptor=Never()), ctx)
    assert result.outcome is Outcome.NOTHING_TO_DO
    assert not dev.presses and dev.state == "village"


def test_the_default_acceptor_takes_the_first_village():
    assert AcceptFirst().accept(fixture_frame("scouting_barbarians").image) is True
