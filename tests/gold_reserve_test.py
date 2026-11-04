"""Reserva de oro (#32): umbral, caducidad del coste y comprobación de gasto (lógica pura)."""

from pyclash.policy.gold_reserve import MAX_COST_AGE_S, GoldReserve, check_gold_spend


def test_el_umbral_es_75_saltos_mas_una_busqueda():
    r = GoldReserve()
    r.update(next_cost=50, search_cost=60, now=0.0)
    assert r.floor(0.0) == 75 * 50 + 60


def test_sin_costes_no_hay_umbral_y_hay_que_leer():
    r = GoldReserve()
    assert r.floor(0.0) is None and r.needs_read(0.0)


def test_el_coste_caduca_pasados_60_minutos():
    r = GoldReserve()
    r.update(50, 50, now=100.0)
    assert MAX_COST_AGE_S == 3600
    assert r.floor(100.0 + 3600) == 3800  # justo en el límite aún vale
    assert not r.needs_read(100.0 + 3600)
    assert r.floor(100.0 + 3600.1) is None and r.needs_read(100.0 + 3600.1)


def test_una_lectura_nueva_renueva_la_antiguedad():
    r = GoldReserve()
    r.update(50, 50, now=0.0)
    r.update(50, 50, now=3000.0)
    assert r.floor(6000.0) == 3800


def test_gasto_que_cabe_se_permite():
    assert check_gold_spend(100_000, 20_000, 30_000) is None


def test_gasto_que_deja_menos_que_la_reserva_se_rechaza_con_motivo():
    reason = check_gold_spend(40_000, 20_000, 30_000)
    assert reason is not None and "30000" in reason


def test_gasto_que_deja_justo_la_reserva_se_permite():
    assert check_gold_spend(50_000, 20_000, 30_000) is None


def test_reserva_o_oro_desconocidos_bloquean():
    assert check_gold_spend(100_000, 1, None) is not None
    assert check_gold_spend(None, 1, 0) is not None


def test_los_muros_del_orquestador_reciben_el_umbral_de_la_reserva():
    from helpers import frame, make_ctx
    from pyclash.tasks.orchestrator import default_tasks

    walls = next(t for t in default_tasks() if t.__class__.__name__ == "WallBatchTask")
    ctx = make_ctx([frame()])
    assert walls.inputs(ctx).gold_floor is None  # sin coste leído no se gasta oro
    ctx.gold_reserve.update(50, 50, ctx.clock.now())
    assert walls.inputs(ctx).gold_floor == 3800
