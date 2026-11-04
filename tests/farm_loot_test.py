"""Exploración con mínimos de botín (issue #12) por la costura de tarea, con capturas reales como pantallas."""

import random

from pyclash.policy.loot import LootMinimum, SessionStats
from pyclash.runtime.clock import FakeClock
from pyclash.runtime.context import Context
from pyclash.device.base import Rect
from pyclash.runtime.events import CaptureSaved, Event, LootObtained, Warning
from pyclash.tasks.base import Outcome, run_task
from pyclash.tasks.farm import FarmConfig, FarmTask
from pyclash.vision.base import MetadataVision
from pyclash.vision.ocr import FakeOcr, WordBox

from helpers import fixture_frame
from farm_test import W, H, FarmDevice, in_box

NEXT = (0.89, 0.70, 0.99, 0.78)

# Aldeas reales, con su botín: (oro, elixir)
POOR = "scouting_base"  # 930 / 920
MID = "scouting_barbarians"  # 1 000 / 2 000
RICH = "scouting_split_zone"  # 7 992 / 10 338


class ScoutDevice(FarmDevice):
    """FarmDevice cuya exploración recorre una lista de aldeas con cada «Next» (o ninguna si `next_works` es False)."""

    def __init__(self, clock, villages, *, next_works=True, **kw):
        super().__init__(clock, **kw)
        self.villages = [fixture_frame(v).image if isinstance(v, str) else v for v in villages]
        self.index = 0
        self.next_works = next_works
        self.nexts = 0
        self.images["scout"] = self.villages[0]

    def tap(self, x, y):
        if self.state == "scout" and in_box(NEXT, x, y):
            self.nexts += 1
            if self.next_works:
                self.index = min(self.index + 1, len(self.villages) - 1)
                self.images["scout"] = self.villages[self.index]
            return
        super().tap(x, y)


def make(villages, **kw):
    clock = FakeClock()
    device = ScoutDevice(clock, villages, **{k: v for k, v in kw.items() if k in ("next_works", "no_gold", "screens")})
    ctx = Context(device=device, vision=MetadataVision(), clock=clock, rng=random.Random(0))
    return ctx, device


def collect(ctx, kind):
    got = []
    ctx.events.subscribe(kind, got.append)
    return got


def cfg(**kw):
    return FarmConfig(**kw)


def test_sin_minimos_no_se_pulsa_next_y_se_ataca_la_primera():
    ctx, dev = make([POOR, RICH])
    result = run_task(FarmTask(cfg()), ctx)
    assert result.outcome is Outcome.DONE
    assert dev.nexts == 0


def test_e1r1_una_aldea_que_cumple_se_ataca_sin_saltar():
    ctx, dev = make([RICH, POOR])
    result = run_task(FarmTask(cfg(minimum=LootMinimum(gold=5_000))), ctx)
    assert result.outcome is Outcome.DONE and dev.nexts == 0 and dev.presses


def test_e2r1_se_pulsa_next_hasta_que_una_aldea_cumple():
    ctx, dev = make([POOR, MID, RICH])
    warnings = collect(ctx, Warning)
    result = run_task(FarmTask(cfg(minimum=LootMinimum(gold=5_000))), ctx)
    assert result.outcome is Outcome.DONE
    assert dev.nexts == 2 and dev.index == 2
    assert any("aceptada tras 2 saltos" in w.message for w in warnings)
    assert dev.presses  # y se ataca


def test_e5r1_hay_que_cumplir_oro_y_elixir_a_la_vez():
    # MID tiene 1 000 de oro (cumple) pero solo 2 000 de elixir (no llega a 5 000); RICH cumple los dos
    ctx, dev = make([MID, RICH])
    result = run_task(FarmTask(cfg(minimum=LootMinimum(gold=500, elixir=5_000))), ctx)
    assert result.outcome is Outcome.DONE and dev.nexts == 1 and dev.index == 1


def test_e3r1_con_el_tope_de_saltos_se_ataca_la_aldea_que_tiene_delante():
    ctx, dev = make([POOR, MID, RICH])
    warnings = collect(ctx, Warning)
    config = cfg(minimum=LootMinimum(gold=10**6), max_skips=2)
    result = run_task(FarmTask(config), ctx)
    assert result.outcome is Outcome.DONE
    assert dev.nexts == 2 and dev.presses
    assert any("tope de 2 saltos" in w.message for w in warnings)


def test_con_poco_oro_se_salta_solo_lo_que_deja_una_busqueda_de_reserva(monkeypatch):
    monkeypatch.setattr("pyclash.tasks.farm.read_hud_gold", lambda image, digits=None: 150)  # 150 // 50 - 1 = 2
    ctx, dev = make([POOR, MID, RICH, RICH])
    warnings = collect(ctx, Warning)
    result = run_task(FarmTask(cfg(minimum=LootMinimum(gold=10**6))), ctx)
    assert result.outcome is Outcome.DONE
    assert dev.nexts == 2  # nunca se pulsa «Next» si luego no quedaría oro para otra búsqueda
    assert any("sin oro para otra búsqueda" in w.message for w in warnings)
    assert any("[1/3]" in w.message for w in warnings)


def test_sin_oro_para_ningun_salto_se_ataca_la_primera(monkeypatch):
    monkeypatch.setattr("pyclash.tasks.farm.read_hud_gold", lambda image, digits=None: 60)
    ctx, dev = make([POOR, RICH])
    result = run_task(FarmTask(cfg(minimum=LootMinimum(gold=10**6))), ctx)
    assert result.outcome is Outcome.DONE and dev.nexts == 0


def test_tres_busquedas_seguidas_con_poco_oro_avisan(monkeypatch):
    monkeypatch.setattr("pyclash.tasks.farm.read_hud_gold", lambda image, digits=None: 60)
    task = FarmTask(cfg(minimum=LootMinimum(gold=10**6)))
    seen = []
    for _ in range(3):
        ctx, dev = make([POOR])
        seen.append(collect(ctx, Warning))
        run_task(task, ctx)
    assert any("3 búsquedas seguidas" in w.message for w in seen[2])
    assert not any("seguidas con el oro bajo" in w.message for w in seen[0])


def erase_loot(image):
    out = image.copy()
    out[55:130, 20:150] = (60, 100, 40)  # tapa «Available Loot» y sus dos filas
    return out


def test_e4r1_botin_ilegible_se_relee_y_si_sigue_dudoso_se_ataca(tmp_path):
    ctx, dev = make([erase_loot(fixture_frame(MID).image), RICH])
    warnings = collect(ctx, Warning)
    saved = collect(ctx, CaptureSaved)
    result = run_task(FarmTask(cfg(minimum=LootMinimum(gold=5_000), capture_dir=tmp_path)), ctx)
    assert result.outcome is Outcome.DONE
    assert dev.nexts == 0 and dev.presses  # ataca esta aldea en vez de saltarla
    assert any("dudoso" in w.message for w in warnings)
    assert saved and list(tmp_path.glob("loot-*.png"))


def test_oscuro_exigido_y_sin_fila_de_oscuro_se_salta():
    ctx, dev = make([MID, RICH])
    warnings = collect(ctx, Warning)
    result = run_task(FarmTask(cfg(minimum=LootMinimum(dark=1_000), max_skips=1)), ctx)
    # las dos carecen de fila de oscuro: vale 0 y no cumple; se salta una vez y luego manda el tope
    assert result.outcome is Outcome.DONE and dev.nexts == 1
    assert any("tope de 1 saltos" in w.message for w in warnings)


def test_un_next_que_no_entra_reinicia_la_busqueda_y_si_no_falla():
    ctx, dev = make([POOR, RICH], next_works=False)
    warnings = collect(ctx, Warning)
    config = cfg(minimum=LootMinimum(gold=5_000), next_timeout_s=2.0)
    result = run_task(FarmTask(config), ctx)
    assert result.outcome is Outcome.FAILED and "Next" in result.reason
    assert any("se reinicia la búsqueda" in w.message for w in warnings)
    assert not dev.presses  # nunca se desplegó nada


class NameOcr(FakeOcr):
    """Devuelve un nombre distinto en cada lectura."""

    def __init__(self, names):
        super().__init__()
        self.names = list(names)

    def read_words(self, image):
        name = self.names.pop(0) if self.names else "x"
        return [WordBox(name, Rect(0, 0, 10, 10), 0.9)]


def test_si_el_botin_coincide_desempata_el_nombre_del_rival():
    # mismo botín en las dos primeras aldeas, pero el nombre cambia: el «Next» entró
    ctx, dev = make([POOR, POOR, RICH])
    warnings = collect(ctx, Warning)
    config = cfg(minimum=LootMinimum(gold=5_000), next_timeout_s=2.0)
    result = run_task(FarmTask(config, ocr=NameOcr(["aldea uno", "aldea dos"])), ctx)
    assert result.outcome is Outcome.DONE and dev.nexts == 2 and dev.index == 2
    assert not any("sin efecto" in w.message for w in warnings)


def test_si_el_botin_y_el_nombre_coinciden_el_next_no_se_da_por_hecho():
    ctx, dev = make([POOR, POOR, RICH])
    warnings = collect(ctx, Warning)
    config = cfg(minimum=LootMinimum(gold=5_000), next_timeout_s=2.0)
    run_task(FarmTask(config, ocr=NameOcr(["misma", "misma"])), ctx)
    assert any("sin efecto verificable (1/2)" in w.message for w in warnings)


def test_los_nombres_no_se_registran_en_los_eventos():
    ctx, dev = make([POOR, POOR, RICH])
    events = []
    ctx.events.subscribe(Event, events.append)
    ocr = NameOcr(["JugadorSecreto", "OtroSecreto"])
    result = run_task(FarmTask(cfg(minimum=LootMinimum(gold=5_000), next_timeout_s=2.0), ocr=ocr), ctx)
    assert events and "Secreto" not in repr(events) and "Secreto" not in result.reason


def test_sin_ocr_un_botin_identico_no_se_da_por_verificado():
    ctx, dev = make([POOR, POOR], next_works=True)
    warnings = collect(ctx, Warning)
    run_task(FarmTask(cfg(minimum=LootMinimum(gold=5_000), next_timeout_s=2.0)), ctx)
    assert any("sin OCR para desempatar" in w.message for w in warnings)


# --- botín de «You got:» (R2) -------------------------------------------------------------------


def test_e1r2_el_botin_del_ataque_se_suma_a_los_totales_y_se_publica():
    ctx, dev = make([MID])
    gains = collect(ctx, LootObtained)
    stats = SessionStats()
    result = run_task(FarmTask(cfg(), stats=stats), ctx)
    assert result.outcome is Outcome.DONE
    assert stats.total == (4, 400, 0) and stats.attacks_counted == 1 and stats.attacks_unread == 0
    assert gains and gains[0].resources == {"gold": 4, "elixir": 400, "dark": 0}


def test_e2r2_un_botin_ilegible_no_se_suma_se_avisa_y_se_guarda_captura(tmp_path):
    blank = fixture_frame("result").image.copy()
    x0, y0, x1, y1 = 380, 320, 700, 440
    blank[y0:y1, x0:x1] = (60, 40, 30)
    ctx, dev = make([MID], screens={"result": blank})
    warnings = collect(ctx, Warning)
    saved = collect(ctx, CaptureSaved)
    stats = SessionStats()
    result = run_task(FarmTask(cfg(capture_dir=tmp_path), stats=stats), ctx)
    assert result.outcome is Outcome.DONE
    assert stats.total == (0, 0, 0) and stats.attacks_counted == 0 and stats.attacks_unread == 1
    assert any("You got" in w.message for w in warnings)
    assert saved and list(tmp_path.glob("result-*.png"))


def test_el_coste_de_buscar_se_anota_al_pasar_por_el_menu():
    ctx, dev = make([MID])
    task = FarmTask(cfg(minimum=LootMinimum(gold=500)))
    run_task(task, ctx)
    assert task.search_cost == 50 and task.next_cost == 50
