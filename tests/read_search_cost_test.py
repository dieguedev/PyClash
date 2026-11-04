"""ReadSearchCost (#32): lee el coste de «Find a Match» sin pagar, cierra y actualiza la reserva."""

from farm_test import ATTACK, BATTLE, FIND, TERRAIN, FarmDevice, in_box

from pyclash.runtime.clock import FakeClock
from pyclash.runtime.context import Context
from pyclash.tasks.base import Outcome
from pyclash.tasks.read_search_cost import ReadSearchCost
from pyclash.vision.base import MetadataVision

import random

from pyclash.screens.loot import SEARCH_COST


class CostDevice(FarmDevice):
    def tap(self, x, y):
        self.taps.append((x, y))
        if self.state == "village" and in_box(BATTLE, x, y):
            self.state = "menu"
        elif self.state == "menu" and in_box(TERRAIN, x, y):
            self.state = "village"


def make(menu_image=None):
    clock = FakeClock()
    screens = {"menu": menu_image} if menu_image is not None else None
    device = CostDevice(clock, screens=screens)
    return Context(device=device, vision=MetadataVision(), clock=clock, rng=random.Random(0)), device


def test_lee_el_coste_actualiza_la_reserva_y_vuelve_a_la_aldea():
    ctx, device = make()
    task = ReadSearchCost()
    result = task.run(ctx)
    assert result.outcome is Outcome.DONE
    assert task.cost == 50
    assert ctx.gold_reserve.floor(ctx.clock.now()) == 75 * 50 + 50
    assert device.state == "village"


def test_nunca_pulsa_find_a_match():
    ctx, device = make()
    ReadSearchCost().run(ctx)
    assert not any(in_box(FIND, x, y) or in_box(ATTACK, x, y) for x, y in device.taps)


def test_si_no_se_lee_el_coste_falla_sin_tocar_la_reserva():
    import numpy as np

    ctx, device = make()
    original = device.images["menu"]
    blank = np.array(original)
    r = SEARCH_COST.rect(blank)
    blank[r.y: r.y + r.h, r.x: r.x + r.w] = 0  # tapa solo la cifra del botón: el menú se sigue reconociendo
    device.images["menu"] = blank
    task = ReadSearchCost()
    result = task.run(ctx)
    assert result.outcome is Outcome.FAILED
    assert task.cost is None and ctx.gold_reserve.floor(ctx.clock.now()) is None
    assert device.state == "village"
