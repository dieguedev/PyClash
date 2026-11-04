"""Muros por lotes (issue #13): política pura (umbral, colchón, lote, verificación en el panel)."""

import random

import pytest

from pyclash.policy.walls import (
    Preference,
    WallSettings,
    WallState,
    bar_is_full,
    batch_size,
    decide_walls,
    effective_reserve,
    next_add,
    next_window_delay,
    plan_clicks,
    spendable,
    verified_after_pass,
    verified_in_panel,
    wall_gate,
    window_due,
)

M = 1_000_000
GOLD_ONLY = WallSettings(threshold=2 * M, enabled=frozenset({"gold"}))


def state(gold=None, elixir=None, builders=1, floor=100_000, pending=False):
    return WallState((gold, elixir, 0), builders, floor, pending)


# --- R1: solo cuando toca ---------------------------------------------------------------------


def test_e1r1_gastable_sobre_el_umbral_inicia_la_tarea():
    d = decide_walls(state(gold=2_300_000), GOLD_ONLY)
    assert d.resource == "gold" and d.spendable == 2_200_000


def test_e2r1_bajo_el_umbral_se_salta_con_motivo():
    d = decide_walls(state(gold=1_900_000), GOLD_ONLY)
    assert d.resource is None and "umbral" in d.reason


def test_el_umbral_se_compara_con_el_gastable_no_con_el_almacen():
    # 2,05 M en el almacén, pero con colchón 100 000 el gastable es 1,95 M: no llega.
    assert decide_walls(state(gold=2_050_000), GOLD_ONLY).resource is None


def test_e3r1_oro_justo_no_se_gasta():
    d = decide_walls(state(gold=80_000, floor=100_000), WallSettings(threshold=1, enabled=frozenset({"gold"})))
    assert d.resource is None
    assert spendable(state(gold=80_000, floor=100_000), "gold") == 0


def test_colchon_desconocido_no_gasta_oro():
    d = decide_walls(WallState((9 * M, None, 0), 1, None, False), GOLD_ONLY)
    assert d.resource is None and "colchón" in d.reason


def test_e4r1_sin_constructor_libre_no_se_empieza():
    assert decide_walls(state(gold=9 * M, builders=0), GOLD_ONLY).resource is None
    assert wall_gate(0, True, False) and wall_gate(None, True, False)
    assert wall_gate(1, True, True) is None  # reserva activa y el último libre
    assert wall_gate(1, False, True)  # sin reserva, con mejoras pendientes: esperan los muros


def test_hay_oro_sin_lectura_del_hud_no_hace_nada():
    assert decide_walls(WallState((None, None, None), 1, 1000, False), GOLD_ONLY).resource is None


# --- preferencia de recurso ---------------------------------------------------------------------


def both(pref, **kw):
    return WallSettings(threshold=2 * M, preference=pref, **kw)


def test_elixir_primero_por_defecto():
    assert decide_walls(state(gold=5 * M, elixir=3 * M), both(Preference.ELIXIR_FIRST)).resource == "elixir"


def test_oro_primero():
    assert decide_walls(state(gold=5 * M, elixir=3 * M), both(Preference.GOLD_FIRST)).resource == "gold"


def test_falta_elixir_se_usa_el_oro_si_la_preferencia_lo_permite():
    s = state(gold=5 * M, elixir=500_000)
    assert decide_walls(s, both(Preference.ELIXIR_FIRST)).resource == "gold"
    assert decide_walls(s, both(Preference.ELIXIR_ONLY)).resource is None


def test_solo_oro_ignora_el_elixir():
    assert decide_walls(state(gold=5 * M, elixir=9 * M), both(Preference.GOLD_ONLY)).resource == "gold"


def test_recurso_no_habilitado_se_salta_y_se_usa_el_siguiente():
    d = decide_walls(state(gold=5 * M, elixir=9 * M), both(Preference.ELIXIR_FIRST, enabled=frozenset({"gold"})))
    assert d.resource == "gold" and any("elixir" in s for s in d.skipped)


def test_umbral_negativo_se_rechaza():
    with pytest.raises(ValueError):
        WallSettings(threshold=-1)


# --- constructores: las mejoras van primero ------------------------------------------------------


def test_reserva_activa_con_varios_libres_las_mejoras_van_primero():
    s = WallSettings(reserve_builder_for_walls=True, enabled=frozenset({"gold"}))
    d = decide_walls(state(gold=5 * M, builders=2, pending=True), s)
    assert d.resource is None and "mejoras" in d.reason


def test_reserva_activa_con_el_ultimo_libre_corren_los_muros():
    s = WallSettings(reserve_builder_for_walls=True, enabled=frozenset({"gold"}))
    assert decide_walls(state(gold=5 * M, builders=1, pending=True), s).resource == "gold"


def test_reserva_activa_sin_mejoras_pendientes_usan_cualquier_libre():
    s = WallSettings(reserve_builder_for_walls=True, enabled=frozenset({"gold"}))
    assert decide_walls(state(gold=5 * M, builders=3, pending=False), s).resource == "gold"


def test_reserva_desactivada_los_muros_van_al_final():
    s = WallSettings(enabled=frozenset({"gold"}), reserve_builder_for_walls=False)
    assert decide_walls(state(gold=5 * M, builders=1, pending=True), s).resource is None
    assert decide_walls(state(gold=5 * M, builders=1, pending=None), s).resource is None
    assert decide_walls(state(gold=5 * M, builders=1, pending=False), s).resource == "gold"


# --- umbral y ritmo ---------------------------------------------------------------------------------


def test_umbral_por_defecto_cero_sobre_el_colchon():
    s = WallSettings(enabled=frozenset({"gold"}))
    assert s.threshold == 0
    assert decide_walls(state(gold=100_001, floor=100_000), s).resource == "gold"
    assert decide_walls(state(gold=100_000, floor=100_000), s).resource is None  # gastable 0: no se usa
    assert decide_walls(state(gold=50_000, floor=100_000), s).resource is None


def test_umbral_pequeno_sirve_para_aldeas_de_muros_baratos():
    s = WallSettings(threshold=1000, enabled=frozenset({"gold"}))
    assert decide_walls(state(gold=101_500, floor=100_000), s).resource == "gold"


def test_ventana_de_muros_entre_diez_y_quince_minutos():
    s = WallSettings()
    rng = random.Random(7)
    delays = [next_window_delay(s, rng) for _ in range(200)]
    assert all(600 <= d <= 900 for d in delays) and len(set(delays)) > 50


def test_la_ventana_se_abre_al_pasar_el_intervalo():
    assert window_due(700, 700, False, False)
    assert window_due(900, 700, None, None)
    assert window_due(699, 700, False, False) is None


def test_la_ventana_se_abre_antes_si_un_almacen_se_llena():
    assert "oro" in window_due(10, 700, True, False)
    assert "elixir" in window_due(10, 700, False, True)
    assert window_due(10, 700, True, True)


def test_barra_al_95_por_ciento_cuenta_como_llena():
    assert bar_is_full(1.0) and bar_is_full(0.95) and bar_is_full(0.97)
    assert bar_is_full(0.94) is False and bar_is_full(0.5) is False
    assert bar_is_full(None) is None


def test_almacen_desconocido_no_abre_la_ventana():
    assert window_due(10, 700, None, None) is None


def test_intervalo_invalido_se_rechaza():
    with pytest.raises(ValueError):
        WallSettings(interval_min_s=900, interval_max_s=600)


# --- reserva de constructor -----------------------------------------------------------------------


def test_reserva_para_muros_deja_siempre_uno():
    assert effective_reserve(0, True) == 1
    assert effective_reserve(2, True) == 2
    assert effective_reserve(0, False) == 0


# --- el lote ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "gastable, unit, available, esperado",
    [(10_500, 1000, None, 10), (10_500, 1000, 8, 8), (999, 1000, None, 0), (5000, None, None, 0), (5000, 0, None, 0), (0, 1000, 5, 0)],
)
def test_tamano_del_lote(gastable, unit, available, esperado):
    assert batch_size(gastable, unit, available) == esperado


def test_pulsaciones_de_mas_diez_y_mas_uno():
    assert plan_clicks(0) == (0, 0)
    assert plan_clicks(7) == (0, 7)
    assert plan_clicks(26) == (2, 6)
    with pytest.raises(ValueError):
        plan_clicks(-1)


def test_siguiente_pulsacion():
    assert next_add(0, 26) == 10
    assert next_add(20, 26) == 1
    assert next_add(26, 26) == 0
    assert next_add(30, 26) == 0


# --- R4: verificación en el panel -----------------------------------------------------------------


def test_e1r4_misma_fila_con_menos_muros():
    assert verified_in_panel(30, 1000, 10, 20, 1000, True, True)


def test_misma_fila_sin_cambios_no_cuenta():
    assert not verified_in_panel(30, 1000, 10, 30, 1000, True, True)


def test_fila_con_otro_coste_no_cuenta_como_menos_muros():
    assert not verified_in_panel(30, 1000, 10, 20, 2000, True, True)


def test_e2r4_la_fila_desaparece_al_mejorar_todo_el_nivel():
    assert verified_in_panel(30, 1000, 30, None, None, False, True)


def test_e3r4_fila_ausente_con_lote_parcial_no_cuenta():
    assert not verified_in_panel(30, 1000, 10, None, None, False, True)


def test_fila_ausente_sin_panel_completo_no_cuenta():
    assert not verified_in_panel(30, 1000, 30, None, None, False, False)


def test_fila_ilegible_no_cuenta():
    assert not verified_in_panel(30, 1000, 10, None, 1000, True, True)
    assert not verified_in_panel(30, 1000, 10, 20, None, True, True)


def test_lote_vacio_nunca_verifica():
    assert not verified_in_panel(30, 1000, 0, None, None, False, True)


def test_verificacion_de_pasada_suma_los_muros_que_llegan_del_nivel_anterior():
    assert verified_after_pass(30, 1000, 10, 0, 0, 20, 1000, True, True)  # sin llegadas: como antes
    assert verified_after_pass(30, 1000, 10, 15, 15, 35, 1000, True, True)  # llegan 15: 30 − 10 + 15
    assert not verified_after_pass(30, 1000, 10, 15, 15, 20, 1000, True, True)
    assert verified_after_pass(30, 1000, 10, 0, 5, 23, 1000, True, True)  # llegadas inciertas: dentro del margen
    assert not verified_after_pass(30, 1000, 10, 0, 5, 26, 1000, True, True)


def test_verificacion_de_pasada_fila_ausente_solo_si_no_pudo_recibir_muros():
    assert verified_after_pass(30, 1000, 30, 0, 5, None, None, False, True)
    assert not verified_after_pass(30, 1000, 30, 5, 5, None, None, False, True)  # tenía que haber recibido 5
    assert not verified_after_pass(30, 1000, 30, 0, 0, None, None, False, False)  # panel sin recorrer entero


def test_la_reserva_de_constructor_para_muros_esta_activa_por_defecto():  # R5
    assert WallSettings().reserve_builder_for_walls is True
