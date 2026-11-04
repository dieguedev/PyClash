"""Botín (issue #12): política pura, lectores sobre capturas reales y registro de sesiones."""

import json

import pytest

from pyclash.policy.loot import (
    EnemyLoot,
    LootLimits,
    LootMinimum,
    RelaxedTracker,
    SessionStats,
    Verdict,
    decide,
    gold_floor,
    skip_budget,
)
from pyclash.runtime.clock import FakeClock
from pyclash.runtime.session_log import LootSession, append_session
from pyclash.screens.layout import scaled_font_px
from pyclash.screens.loot import (
    DARK_ICON,
    ENEMY_LOOT_PX,
    ENEMY_ROWS,
    dark_icon_present,
    read_enemy_loot,
    read_hud_gold,
    read_next_cost,
    read_result_loot,
    read_search_cost,
)
from pyclash.vision.text import render_text

from helpers import fixture_frame


def img(name):
    return fixture_frame(name).image.copy()


# --- política: los mínimos filtran aldeas (R1) --------------------------------------------------


def test_e1r1_la_aldea_que_cumple_se_acepta():
    assert decide(EnemyLoot(350_000, 100_000), LootMinimum(gold=300_000)) is Verdict.ACCEPT


def test_e2r1_la_aldea_que_no_cumple_se_salta():
    assert decide(EnemyLoot(100_000, 100_000), LootMinimum(gold=300_000)) is Verdict.SKIP


def test_e5r1_hay_que_cumplir_todos_los_minimos_a_la_vez():
    minimum = LootMinimum(gold=300_000, elixir=300_000)
    assert decide(EnemyLoot(400_000, 100_000), minimum) is Verdict.SKIP
    assert decide(EnemyLoot(400_000, 350_000), minimum) is Verdict.ACCEPT


def test_sin_minimos_no_se_mira_el_botin():
    assert decide(None, LootMinimum()) is Verdict.ACCEPT
    assert not LootMinimum().any


def test_un_recurso_sin_minimo_no_pide_relectura():
    # el elixir no se leyó pero no importa: solo el oro tiene mínimo
    assert decide(EnemyLoot(500, None), LootMinimum(gold=100)) is Verdict.ACCEPT


def test_lo_ilegible_pide_releer():
    assert decide(EnemyLoot(None, 500), LootMinimum(gold=100)) is Verdict.REREAD
    assert decide(None, LootMinimum(gold=100)) is Verdict.REREAD


def test_un_fallo_seguro_gana_a_una_duda():
    # el oro ya no llega: da igual que el elixir no se leyera
    assert decide(EnemyLoot(50, None), LootMinimum(gold=100, elixir=100)) is Verdict.SKIP


def test_e6r1_aldea_sin_fila_de_oscuro_con_minimo_de_oscuro_se_salta():
    minimum = LootMinimum(dark=5_000)
    assert decide(EnemyLoot(900, 900, None, dark_row=False), minimum) is Verdict.SKIP


def test_con_fila_de_oscuro_ilegible_se_relee_y_con_fila_dudosa_tambien():
    minimum = LootMinimum(dark=5_000)
    assert decide(EnemyLoot(900, 900, None, dark_row=True), minimum) is Verdict.REREAD
    assert decide(EnemyLoot(900, 900, None, dark_row=None), minimum) is Verdict.REREAD


def test_oscuro_leido_se_compara_con_el_minimo():
    minimum = LootMinimum(dark=5_000)
    assert decide(EnemyLoot(1, 1, 4_999, True), minimum) is Verdict.SKIP
    assert decide(EnemyLoot(1, 1, 5_000, True), minimum) is Verdict.ACCEPT


def test_oscuro_sin_minimo_no_importa_aunque_no_exista():
    assert decide(EnemyLoot(500, 500, None, False), LootMinimum(gold=100)) is Verdict.ACCEPT


def test_los_minimos_no_pueden_ser_negativos():
    with pytest.raises(ValueError):
        LootMinimum(gold=-1)


# --- presupuesto de saltos y reserva de oro -----------------------------------------------------


def test_presupuesto_es_oro_entre_coste_menos_una_busqueda_de_reserva():
    assert skip_budget(gold=3_000, next_cost=50) == 59  # 60 búsquedas posibles, una queda de reserva
    assert skip_budget(gold=47_954, next_cost=50) == 75  # el tope manda


def test_presupuesto_nunca_es_negativo_y_sin_datos_no_salta():
    assert skip_budget(gold=60, next_cost=50) == 0
    assert skip_budget(gold=0, next_cost=50) == 0
    assert skip_budget(gold=None, next_cost=50) == 0
    assert skip_budget(gold=1000, next_cost=None) == 0
    assert skip_budget(gold=1000, next_cost=0) == 0


def test_gold_floor_es_75_saltos_mas_una_busqueda():
    assert gold_floor(next_cost=50, search_cost=50) == 75 * 50 + 50
    assert gold_floor(next_cost=50, search_cost=60) == 75 * 50 + 60  # los costes son campos separados


def test_las_busquedas_con_poco_oro_se_cuentan_y_se_reinician_al_recuperarlo():
    t = RelaxedTracker()
    assert t.plan(gold=3_000, next_cost=50).streak == 1
    p = t.plan(gold=3_000, next_cost=50)
    assert p.relaxed and p.streak == 2 and p.budget == 59
    p = t.plan(gold=100_000, next_cost=50)
    assert not p.relaxed and p.streak == 0 and p.budget == 75


# --- estadísticas de la sesión (R2) -------------------------------------------------------------


def test_e1r2_una_ganancia_normal_suma_y_cuenta_en_la_tasa():
    s = SessionStats(started_at=0.0)
    assert s.record((80_000, 0, 0))
    assert s.total == (80_000, 0, 0) and s.attacks_counted == 1
    assert s.rate_per_hour(1800.0) == (160_000, 0, 0)  # media hora de tiempo real


def test_e2r2_una_ganancia_imposible_no_se_suma():
    s = SessionStats()
    assert not s.record((91_000_000, 0, 0))
    assert s.total == (0, 0, 0) and s.attacks_counted == 0


def test_el_oscuro_tiene_su_propio_tope():
    s = SessionStats(LootLimits(max_gain_main=1000, max_gain_dark=10))
    assert not s.record((500, 500, 50))
    assert s.record((500, 500, 5))


def test_la_tasa_sin_tiempo_transcurrido_es_cero():
    assert SessionStats(started_at=5.0).rate_per_hour(5.0) == (0, 0, 0)


def test_el_registro_de_sesion_lleva_solo_cifras():
    s = SessionStats(started_at=0.0)
    s.record((3_600, 1_800, 0))
    s.record_unread()
    rec = s.to_record(3600.0, "2026-10-31T10:00:00", "2026-10-31T11:00:00")
    assert rec["oro"] == 3_600 and rec["oro_por_hora"] == 3_600 and rec["ataques_sin_lectura"] == 1
    assert all(isinstance(v, (int, float, str)) for v in rec.values())
    assert set(rec) == {
        "inicio", "fin", "duracion_s", "ataques_contados", "ataques_sin_lectura",
        "oro", "elixir", "oscuro", "oro_por_hora", "elixir_por_hora", "oscuro_por_hora",
    }


def test_la_sesion_guarda_una_linea_al_cerrar_aunque_haya_excepcion(tmp_path):
    clock = FakeClock()
    path = tmp_path / "sub" / "sessions.jsonl"
    stats = SessionStats()
    with pytest.raises(RuntimeError):
        with LootSession(stats, clock, path, wall_now=lambda: "t") as s:
            s.record((100, 0, 0))
            clock.sleep(1800)
            raise RuntimeError("se cayó")
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1 and rows[0]["oro"] == 100 and rows[0]["duracion_s"] == 1800.0


def test_las_sesiones_se_anaden_sin_pisar_las_anteriores(tmp_path):
    path = tmp_path / "sessions.jsonl"
    append_session(path, {"a": 1})
    append_session(path, {"a": 2})
    assert [json.loads(x)["a"] for x in path.read_text(encoding="utf-8").splitlines()] == [1, 2]


# --- lectores sobre capturas reales -------------------------------------------------------------


@pytest.mark.parametrize(
    "name, gold, elixir",
    [
        ("scouting_barbarians", 1_000, 2_000),  # el «1» de «1 000» es fino: se leía «000» = 0
        ("scouting_base", 930, 920),
        ("scouting_split_zone", 7_992, 10_338),
        ("finding_opponent", 408, 464),
        ("finding_opponent_red_zone", 782, 770),
    ],
)
def test_botin_del_rival_en_capturas_reales(name, gold, elixir):
    loot = read_enemy_loot(img(name))
    assert (loot.gold, loot.elixir) == (gold, elixir)
    # estas aldeas no tienen fila de oscuro: vale 0, no es «no leído»
    assert loot.dark is None and loot.dark_row is False


@pytest.mark.parametrize(
    "name, gold",
    [("scouting_barbarians", 48_072), ("scouting_base", 47_954), ("scouting_split_zone", 88_699), ("finding_opponent", 3_329)],
)
def test_oro_del_hud_y_coste_de_next_en_la_exploracion(name, gold):
    image = img(name)
    assert read_hud_gold(image) == gold
    assert read_next_cost(image) == 50


def test_el_coste_de_buscar_se_lee_del_boton_del_menu_sin_pagar():
    assert read_search_cost(img("attack_menu")) == 50


def test_el_botin_ganado_se_lee_de_you_got():
    loot = read_result_loot(img("result"))
    assert (loot.gold, loot.elixir, loot.dark) == (4, 400, 0)


def test_you_got_ilegible_o_imposible_da_none():
    image = img("result")
    image[320:440, 380:700] = (60, 40, 30)  # se tapan las cifras
    assert read_result_loot(image) is None


def paste(image, patch, x, y):
    h, w = patch.shape[:2]
    ink = patch.max(axis=2) > 0
    image[y:y + h, x:x + w][ink] = patch[ink]


def test_una_fila_de_oscuro_con_digitos_se_lee():
    image = img("scouting_barbarians")
    row = ENEMY_ROWS[2].rect(image)
    paste(image, render_text("4200", scaled_font_px(image, ENEMY_LOOT_PX)), row.x + 2, row.y + 6)
    loot = read_enemy_loot(image)
    assert loot.dark == 4_200 and loot.dark_row is True


def test_fila_de_oscuro_con_icono_y_sin_digitos_es_fila_presente_no_leida():
    # PROVISIONAL: el color del icono de oscuro no está medido con una captura real
    image = img("scouting_barbarians")
    icon = DARK_ICON.rect(image)
    image[icon.y + 3:icon.y + icon.h - 3, icon.x + 4:icon.x + icon.w - 4] = (50, 25, 85)
    assert dark_icon_present(image)
    loot = read_enemy_loot(image)
    assert loot.dark_row is True and loot.dark is None
    assert decide(loot, LootMinimum(dark=1_000)) is Verdict.REREAD


def test_el_elixir_rosa_no_se_confunde_con_el_icono_de_oscuro():
    image = img("scouting_base")
    assert not dark_icon_present(image)


def test_un_botin_inverosimil_no_se_acepta():
    image = img("scouting_barbarians")
    row = ENEMY_ROWS[0].rect(image)
    image[row.y:row.y + row.h, row.x:row.x + row.w] = (60, 100, 40)
    paste(image, render_text("91000000", scaled_font_px(image, ENEMY_LOOT_PX)), row.x + 2, row.y + 6)
    assert read_enemy_loot(image).gold is None  # más de lo que un ataque puede dar
