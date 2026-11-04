import random

import numpy as np

from pyclash.device.base import Capabilities, Frame, Rect
from pyclash.screens.battle_bar import BarReader
from pyclash.vision.text import render_text

from helpers import fixture_frame, make_ctx

BAR = BarReader()
W, H = 1280, 720


def cards_of(name):
    return BAR.detect(fixture_frame(name).image)


# ---- barras reales -----------------------------------------------------------------------------


def test_real_bar_with_one_barbarian_card():
    (card,) = cards_of("scouting_barbarians")
    assert (card.kind, card.count, card.exhausted) == ("troop", 40, False)
    assert 190 <= card.rect.x <= 200 and 76 <= card.rect.w <= 82  # medido sobre la captura


def test_real_bar_counter_changes_during_battle():
    (card,) = cards_of("battle")
    assert card.count == 33


def test_real_bar_with_two_troops():
    first, second = cards_of("two_troops_bar")
    assert (first.count, second.count) == (40, 20)
    assert first.kind == second.kind == "troop"
    assert first.rect.x + first.rect.w < second.rect.x  # el hueco normal separa las cartas


def test_exhausted_card_is_gray_and_reads_x0():
    (card,) = cards_of("attacking_no_more_barbarians")
    assert card.exhausted
    assert card.count == 0


def test_empty_placeholders_are_not_cards():
    # El tramo derecho de la barra solo tiene huecos punteados.
    assert len(cards_of("scouting_barbarians")) == 1


def test_screens_without_a_bar_give_no_cards():
    for name in ("main_village", "result", "attack_menu"):
        assert cards_of(name) == [], name


# ---- barras sintéticas (tipos que no hay en capturas reales: PROVISIONAL) ---------------------------


def card_image(rng, w=74, h=97):
    """Una carta con relleno saturado y distinto cada vez, para que el borde y el contenido se vean."""
    hue = rng.randrange(0, 180)
    base = np.zeros((h, w, 3), np.uint8)
    import cv2

    base[:] = cv2.cvtColor(np.uint8([[[hue, 200, 190]]]), cv2.COLOR_HSV2RGB)[0, 0]
    noise = np.array([[rng.randrange(-30, 30) for _ in range(w)] for _ in range(h)], dtype=np.int16)
    out = np.clip(base.astype(np.int16) + noise[..., None], 0, 255).astype(np.uint8)
    out[:3] = (240, 240, 250)
    out[-3:] = (240, 240, 250)
    out[:, :3] = (240, 240, 250)
    out[:, -3:] = (240, 240, 250)
    return out


def bar_canvas(specs, start=197, gap=10, type_gap=26, seed=0):
    """specs: lista de (contador | None, hueco_grande_antes)."""
    rng = random.Random(seed)
    canvas = np.full((H, W, 3), (40, 60, 30), np.uint8)
    canvas[int(H * 0.845) :] = (20, 25, 30)
    x = start
    positions = []
    for count, wide_before in specs:
        if positions:
            x += type_gap if wide_before else gap
        img = card_image(rng)
        if count is not None:
            text = render_text(f"x{count}", 15)
            img[6 : 6 + text.shape[0], 74 - 6 - text.shape[1] : 74 - 6] = np.maximum(
                img[6 : 6 + text.shape[0], 74 - 6 - text.shape[1] : 74 - 6], text
            )
        y = int(H * 0.855)
        canvas[y : y + 97, x : x + 74] = img
        positions.append(x)
        x += 74
    return canvas, positions


def test_synthetic_bar_classifies_troops_spells_and_hero():
    canvas, _ = bar_canvas([(10, False), (20, False), (30, False), (2, True), (3, False), (None, True)])
    cards = BAR.detect(canvas)
    assert [c.kind for c in cards] == ["troop", "troop", "troop", "spell", "spell", "hero"]
    assert [c.count for c in cards] == [10, 20, 30, 2, 3, None]


def test_synthetic_clan_card_between_two_type_gaps():
    canvas, _ = bar_canvas([(10, False), (20, False), (5, True), (2, True)])
    kinds = [c.kind for c in BAR.detect(canvas)]
    assert kinds == ["troop", "troop", "clan", "spell"]


def test_a_single_trailing_card_after_a_gap_is_a_spell_not_clan():
    canvas, _ = bar_canvas([(10, False), (2, True)])
    assert [c.kind for c in BAR.detect(canvas)] == ["troop", "spell"]


def test_long_bar_scrolls_until_the_last_card_stops_moving():
    pitch = 84
    # 16 cartas en una tira más ancha que la pantalla; cada desplazamiento mueve la ventana 4 cartas
    rng = random.Random(7)
    strip = np.full((H, 197 + 16 * pitch + 200, 3), (40, 60, 30), np.uint8)
    strip[int(H * 0.845) :] = (20, 25, 30)
    for i in range(16):
        strip[int(H * 0.855) : int(H * 0.855) + 97, 197 + i * pitch : 197 + i * pitch + 74] = card_image(rng)
    frames = [
        Frame(strip[:, shift : shift + W].copy(), (W, H), 0.0, {}) for shift in (0, 4 * pitch, 4 * pitch)
    ]
    from pyclash.device.fake import Transition

    ctx = make_ctx(frames, [Transition(0, "swipe", 1), Transition(1, "swipe", 2)])
    scan = BAR.scan(ctx)
    assert scan.scrolled
    assert len(scan.cards) == 16
    swipes = [a for a in ctx.device.actions if a.kind == "swipe"]
    assert len(swipes) >= 3  # dos para avanzar (el segundo ya no mueve nada) y al menos uno para devolverla


def test_short_bar_is_not_scrolled():
    frames = [fixture_frame("scouting_barbarians")]
    ctx = make_ctx(frames)
    scan = BAR.scan(ctx)
    assert not scan.scrolled and len(scan.cards) == 1
    assert not ctx.device.actions
