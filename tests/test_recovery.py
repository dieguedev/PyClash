"""Recuperación de pantalla (issue #6) con dispositivo falso: nada de juego, red ni pantalla real."""

import numpy as np
import pytest

from pyclash.device.base import BlackCapture, Frame, Rect, WindowMinimized, WindowNotFound
from pyclash.device.fake import Transition
from pyclash.runtime.events import CaptureSaved, Error, Event, StateChanged
from pyclash.screens.classifier import Screen, ScreenClassifier, ScreenSignature, TextIndicator
from pyclash.screens.layout import scaled_font_px
from pyclash.screens.signatures import DEFAULT_SIGNATURES, box
from pyclash.tasks.base import Outcome, run_task
from pyclash.tasks.recovery import RecoverScreen, RecoveryConfig, RecoveryHooks
from pyclash.vision.buttons import find_close_buttons
from pyclash.vision.color import BUTTON_GREEN, color_fraction, find_green_buttons
from pyclash.vision.text import render_text

from helpers import fixture_frame, make_ctx, make_scripted_ctx

W, H = 1280, 720


def recorder(ctx):
    seen = []
    ctx.events.subscribe(Event, seen.append)
    return seen


def of_type(seen, kind):
    return [e for e in seen if isinstance(e, kind)]


def make_task(**config):
    return RecoverScreen(config=RecoveryConfig(**config), hooks=config.pop("hooks", None))


def dialog(close=False, with_ok=True):
    """Un diálogo que el clasificador no conoce: marrón, con un «Okay» verde y, opcionalmente, una «X»."""
    img = np.full((H, W, 3), (70, 50, 40), np.uint8)
    if with_ok:
        img[420:500, 520:760] = (110, 200, 30)
        patch = render_text("Okay", 30)
        ink = patch.max(axis=2) > 0
        img[440:440 + patch.shape[0], 580:580 + patch.shape[1]][ink] = patch[ink]
    if close:
        img[60:104, 1150:1194] = (220, 30, 40)
        img[70:94, 1160:1184] = (235, 235, 235)
    return Frame(img, (W, H), 0.0, {})


# --- R1/R2: pantalla conocida y desconocida ----------------------------------------------------


def test_already_on_the_goal_screen_does_nothing():
    ctx = make_ctx([fixture_frame("main_village")])
    result = run_task(make_task(), ctx)
    assert result.outcome is Outcome.NOTHING_TO_DO
    assert ctx.device.actions == []


def test_known_panel_is_left_through_its_close_button():
    ctx = make_ctx(
        [fixture_frame("upgrade_panel"), fixture_frame("main_village")],
        [Transition(0, "tap", 1)],
    )
    result = run_task(make_task(), ctx)
    assert result.outcome is Outcome.DONE
    (action,) = ctx.device.actions
    assert find_close_buttons(fixture_frame("upgrade_panel").image)[0].rect.contains(*action.args)


def test_unknown_dialog_with_close_button_taps_the_x_and_reclassifies():
    """E1R2."""
    ctx = make_ctx(
        [fixture_frame("unknown_filter_menu"), fixture_frame("upgrade_panel"), fixture_frame("main_village")],
        [Transition(0, "tap", 1), Transition(1, "tap", 2)],
    )
    seen = recorder(ctx)
    result = run_task(make_task(), ctx)
    assert result.outcome is Outcome.DONE
    assert [e.state for e in of_type(seen, StateChanged)] == ["unknown", "upgrade_panel", "main_village"]
    first = ctx.device.actions[0].args
    assert find_close_buttons(fixture_frame("unknown_filter_menu").image)[0].rect.contains(*first)


def test_an_unknown_dialog_closed_with_the_x_leaves_a_capture_and_a_warning_before_the_tap(tmp_path):
    """#12: lo que no se reconoce se cierra, pero antes se guarda su captura para añadir su firma."""
    from pyclash.runtime.events import Warning as Warn

    ctx = make_ctx(
        [fixture_frame("unknown_filter_menu"), fixture_frame("upgrade_panel"), fixture_frame("main_village")],
        [Transition(0, "tap", 1), Transition(1, "tap", 2)],
    )
    seen = recorder(ctx)
    result = run_task(make_task(capture_dir=tmp_path), ctx)
    assert result.outcome is Outcome.DONE
    (saved,) = of_type(seen, CaptureSaved)  # solo la de la pantalla desconocida: el panel conocido no genera captura
    assert saved.path.startswith(str(tmp_path)) and list(tmp_path.glob("unknown-*.png"))
    assert not list(tmp_path.glob("recovery-*.png"))
    warning = next(w for w in of_type(seen, Warn) if "pantalla desconocida" in w.message)
    assert "ninguna firma se cumple" in warning.message and "botón de cerrar" in warning.message
    # la captura se guarda ANTES de pulsar: el evento va antes que el aviso de recuperación
    order = [type(e).__name__ for e in seen]
    assert order.index("CaptureSaved") < next(i for i, e in enumerate(seen) if isinstance(e, Warn) and "recuperación:" in e.message)


def test_an_unknown_dialog_without_a_capture_folder_still_closes_and_says_so():
    from pyclash.runtime.events import Warning as Warn

    ctx = make_ctx(
        [fixture_frame("unknown_filter_menu"), fixture_frame("upgrade_panel"), fixture_frame("main_village")],
        [Transition(0, "tap", 1), Transition(1, "tap", 2)],
    )
    seen = recorder(ctx)
    result = run_task(make_task(), ctx)
    assert result.outcome is Outcome.DONE and not of_type(seen, CaptureSaved)
    assert any("sin carpeta de capturas" in w.message for w in of_type(seen, Warn))


def test_unknown_dialog_without_safe_exit_taps_nothing_saves_a_capture_and_fails(tmp_path):
    """E2R2: hay un «Okay» verde enorme, pero no se pulsa: no se sabe qué diálogo es."""
    ctx = make_ctx([dialog()])
    seen = recorder(ctx)
    result = run_task(make_task(capture_dir=tmp_path), ctx)
    assert result.outcome is Outcome.FAILED
    assert "sin salida segura" in result.reason
    assert ctx.device.actions == []
    (saved,) = of_type(seen, CaptureSaved)
    assert list(tmp_path.glob("recovery-*.png")) and saved.path.startswith(str(tmp_path))
    assert of_type(seen, Error)


def test_login_stops_the_bot_without_touching_or_saving_credentials(tmp_path):
    """E3R2: el formulario de Supercell. No se interactúa y no se guarda captura (lleva credenciales)."""
    size = scaled_font_px(np.zeros((H, W, 3), np.uint8), 40)
    login = ScreenSignature(Screen.LOGIN, (TextIndicator("Supercell ID", box(0.3, 0.1, 0.7, 0.3), 40),))
    classifier = ScreenClassifier(signatures=DEFAULT_SIGNATURES + (login,))
    img = np.full((H, W, 3), (70, 50, 40), np.uint8)
    patch = render_text("Supercell ID", size)
    ink = patch.max(axis=2) > 0
    img[110:110 + patch.shape[0], 450:450 + patch.shape[1]][ink] = patch[ink]
    img[420:500, 520:760] = (110, 200, 30)
    ctx = make_ctx([Frame(img, (W, H), 0.0, {})])
    seen = recorder(ctx)
    result = run_task(RecoverScreen(classifier, RecoveryConfig(capture_dir=tmp_path)), ctx)
    assert result.outcome is Outcome.BLOCKED and "Supercell" in result.reason
    assert ctx.device.actions == []
    assert not list(tmp_path.glob("*")) and not of_type(seen, CaptureSaved)
    assert of_type(seen, Error)


# --- R3: límite de intentos --------------------------------------------------------------------


def test_dialog_that_keeps_coming_back_fails_after_the_maximum(tmp_path):
    """E1R3: se cierra cinco veces y reaparece: la tarea falla y emite el evento de error."""
    ctx = make_ctx([fixture_frame("upgrade_panel")])  # los toques no cambian nada
    seen = recorder(ctx)
    result = run_task(make_task(max_attempts=5, capture_dir=tmp_path), ctx)
    assert result.outcome is Outcome.FAILED
    assert "5 intentos" in result.reason
    assert [a.kind for a in ctx.device.actions] == ["tap"] * 5
    assert of_type(seen, Error) and of_type(seen, CaptureSaved)


def test_the_last_attempt_still_counts_if_it_works():
    ctx = make_ctx(
        [fixture_frame("upgrade_panel"), fixture_frame("main_village")],
        [Transition(0, "tap", 1)],
    )
    assert run_task(make_task(max_attempts=1), ctx).outcome is Outcome.DONE


# --- batalla y avisos conocidos ----------------------------------------------------------------


def test_a_live_battle_is_not_surrendered_without_permission():
    ctx = make_ctx([fixture_frame("battle")])
    result = run_task(make_task(), ctx)
    assert result.outcome is Outcome.BLOCKED and "batalla" in result.reason
    assert ctx.device.actions == []


def test_with_permission_it_surrenders_confirms_and_returns_home_in_order():
    names = ["battle", "confirm_surrender", "result", "main_village"]
    ctx = make_ctx(
        [fixture_frame(n) for n in names],
        [Transition(0, "tap", 1), Transition(1, "tap", 2), Transition(2, "tap", 3)],
    )
    result = run_task(make_task(allow_surrender=True), ctx)
    assert result.outcome is Outcome.DONE
    taps = [a.args for a in ctx.device.actions]
    assert len(taps) == 3
    # «Okay» se toca solo en el aviso identificado como «Surrender?», a la derecha de «Cancel».
    assert taps[1][0] > W / 2
    assert find_green_buttons(fixture_frame("result").image)[0].rect.contains(*taps[2])


def test_the_surrender_dialog_is_cancelled_by_default():
    ctx = make_ctx(
        [fixture_frame("confirm_surrender"), fixture_frame("battle")],
        [Transition(0, "tap", 1)],
    )
    result = run_task(make_task(), ctx)
    assert result.outcome is Outcome.BLOCKED  # vuelve a la batalla, y de ahí no se sale sin permiso
    (action,) = ctx.device.actions
    assert action.args[0] < W / 2


# --- dispositivo: captura en negro, ventana perdida, juego cerrado -----------------------------


BLACK = Frame(np.zeros((H, W, 3), np.uint8), (W, H), 0.0, {})


def test_black_capture_waits_then_recovers_without_touching_anything():
    ctx = make_scripted_ctx([BLACK, BLACK, fixture_frame("main_village")])
    result = run_task(make_task(), ctx)
    assert result.outcome is Outcome.DONE
    assert ctx.device.taps == []


def test_persistent_black_capture_without_relaunch_stops_with_a_clear_message():
    """E2R2 de R3: el juego se cerró y el dispositivo no puede reabrirlo."""
    ctx = make_scripted_ctx([BLACK])
    seen = recorder(ctx)
    result = run_task(make_task(), ctx)
    assert result.outcome is Outcome.BLOCKED and "cerrado" in result.reason
    assert of_type(seen, Error)


def test_persistent_black_capture_relaunches_when_the_device_can():
    relaunched = []
    ctx = make_scripted_ctx([BLACK, BLACK, BLACK, fixture_frame("main_village")])
    hooks = RecoveryHooks(relaunch=lambda: relaunched.append(1) or True)
    result = run_task(RecoverScreen(config=RecoveryConfig(), hooks=hooks), ctx)
    assert result.outcome is Outcome.DONE and relaunched == [1]


def test_device_capture_errors_count_as_attempts_and_end():
    ctx = make_scripted_ctx([BlackCapture("negra")])
    result = run_task(make_task(max_attempts=3, black_patience=99), ctx)
    assert result.outcome is Outcome.FAILED and "3 intentos" in result.reason


def test_lost_window_stops_when_it_cannot_be_reopened():
    ctx = make_scripted_ctx([WindowNotFound("sin ventana")])
    result = run_task(make_task(), ctx)
    assert result.outcome is Outcome.BLOCKED and "ventana" in result.reason


def test_lost_window_is_reopened_when_the_device_allows():
    ctx = make_scripted_ctx([WindowNotFound("sin ventana"), fixture_frame("main_village")])
    calls = []
    hooks = RecoveryHooks(relaunch=lambda: calls.append(1) or True)
    result = run_task(RecoverScreen(config=RecoveryConfig(), hooks=hooks), ctx)
    assert result.outcome is Outcome.DONE and calls == [1]


def test_minimized_window_is_restored_when_possible_and_blocks_when_not():
    restored = []
    ctx = make_scripted_ctx([WindowMinimized("minimizada"), fixture_frame("main_village")])
    hooks = RecoveryHooks(restore_window=lambda: restored.append(1))
    assert run_task(RecoverScreen(config=RecoveryConfig(), hooks=hooks), ctx).outcome is Outcome.DONE
    assert restored == [1]
    ctx = make_scripted_ctx([WindowMinimized("minimizada")])
    assert run_task(make_task(), ctx).outcome is Outcome.BLOCKED


def test_stop_request_ends_the_recovery_cooperatively():
    ctx = make_ctx([fixture_frame("upgrade_panel")])
    ctx.stop.request()
    assert run_task(make_task(), ctx).outcome is Outcome.STOPPED


# --- propiedad de seguridad --------------------------------------------------------------------


def green_rects(image):
    return [m.rect for m in find_green_buttons(image, min_area=200)]


def random_dialog(rng):
    """Un diálogo inventado: fondo marrón o gris, 1-3 botones verdes y a veces una «X»."""
    img = np.full((H, W, 3), rng.choice([(70, 50, 40), (90, 90, 95), (200, 190, 170)]), np.uint8)
    for _ in range(rng.integers(1, 4)):
        x, y = int(rng.integers(100, 900)), int(rng.integers(100, 560))
        img[y:y + int(rng.integers(50, 110)), x:x + int(rng.integers(120, 300))] = (110, 200, 30)
    if rng.random() < 0.5:
        x, y = int(rng.integers(900, 1200)), int(rng.integers(10, 120))
        img[y:y + 44, x:x + 44] = (220, 30, 40)
        img[y + 10:y + 34, x + 10:x + 34] = (235, 235, 235)
    return Frame(img, (W, H), 0.0, {})


def test_never_taps_a_green_button_of_an_unidentified_screen():
    """Propiedad: en una pantalla desconocida no se emite ningún toque sobre un botón verde («OK»)."""
    rng = np.random.default_rng(7)
    for _ in range(25):
        frame = random_dialog(rng)
        ctx = make_ctx([frame])
        run_task(make_task(max_attempts=3), ctx)
        for action in ctx.device.actions:
            x, y = action.args
            assert not any(r.contains(x, y) for r in green_rects(frame.image)), f"toque en ({x}, {y})"


@pytest.mark.parametrize("name", ["upgrade_panel", "army_selection", "unknown_filter_menu", "attack_menu", "finding_opponent", "placing_building"])
def test_every_tap_on_real_screens_is_an_exit_not_a_confirm_button(name):
    """El punto pulsado nunca es del verde de los botones de confirmación («OK», «Attack!»)."""
    frame = fixture_frame(name)
    ctx = make_ctx([frame])
    run_task(make_task(max_attempts=2), ctx)
    assert ctx.device.actions, name
    for action in ctx.device.actions:
        x, y = action.args
        patch = frame.image[y - 3:y + 4, x - 3:x + 4]
        assert color_fraction(patch, BUTTON_GREEN, tolerance=25) < 0.5, f"{name}: toque en ({x}, {y})"
