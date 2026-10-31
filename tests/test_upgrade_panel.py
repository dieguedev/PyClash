"""Lector del panel «Buildings & Heroes» (issue #7) sobre capturas reales anonimizadas.

Los valores esperados se confirmaron a ojo sobre las capturas. El panel de la cuenta de prueba es de
ayuntamiento bajo (costes de 3-5 dígitos). Faltan capturas de cuentas de nivel alto (costes de 5+
dígitos distintos de 25 000), del panel desplazado y de otras variantes: ver
docs/verificacion-manual-panel.md.
"""

import cv2
import pytest

from pyclash.device.base import Rect
from pyclash.device.fake import Transition
from pyclash.runtime.stop import StopRequested
from pyclash.screens.panel_layout import FILTER_BUTTON, PANEL_PROFILES, PanelLayout
from pyclash.screens.upgrade_panel import (
    PanelReader,
    PanelRow,
    PanelScanner,
    apply_sort,
    classify_icon,
    panel_diff,
    verify_order,
)
from pyclash.vision.ocr import FakeOcr, WordBox

from helpers import fixture_frame, make_ctx

W, H = 1280, 720

# (nombre, cantidad, coste, recurso, gratis, nuevo, segundos, asequible, tipo, sección)
OPEN_PANEL = [
    ("Laboratory", 1, 5000, "elixir", False, True, 60, True, "new_building", "suggested"),
    ("Elixir Collector", 1, 150, "gold", False, True, 5, True, "new_building", "suggested"),
    ("Builder's Hut", 1, 500, "gems", False, True, None, False, "new_building", "suggested"),
    ("Gold Mine", 1, 150, "elixir", False, True, 5, True, "new_building", "other"),
    ("Bomb", 2, 400, "gold", False, True, None, True, "new_building", "other"),
    ("Wall", 24, None, None, True, True, None, True, "wall", "other"),
    ("Clan Castle", 1, 10000, "elixir", False, False, None, True, "upgrade", "other"),
    ("Army Camp", 2, 2000, "elixir", False, False, 300, True, "upgrade", "other"),
]
FILTERED_PANEL = [
    ("Town Hall", 1, 25000, "gold", False, False, 10800, False, "upgrade", None),
    ("Clan Castle", 1, 10000, "elixir", False, False, None, True, "upgrade", None),
    ("Laboratory", 1, 5000, "elixir", False, True, 60, True, "new_building", None),
    ("Army Camp", 2, 2000, "elixir", False, False, 300, True, "upgrade", None),
    ("Archer Tower", 1, 2000, "gold", False, False, 120, True, "upgrade", None),
    ("Cannon", 2, 1000, "gold", False, False, 30, True, "upgrade", None),
    ("Wall", 26, 1000, "gold", False, False, None, True, "wall", None),
    ("Gold Storage", 2, 750, "elixir", False, False, 120, True, "upgrade", None),
    ("Elixir Storage", 2, 750, "gold", False, False, 120, True, "upgrade", None),
]


def tuples(rows):
    return [(r.name, r.quantity, r.cost, r.resource, r.free, r.new, r.duration, r.affordable, r.kind, r.section) for r in rows]


class CachingReader(PanelReader):
    """Memoriza lecturas por contenido de la imagen: las pruebas leen las mismas capturas muchas veces."""

    _memo: dict = {}

    def read(self, image, initial_section=None):
        key = (hash(image.tobytes()), image.shape, initial_section, self.layout)
        if key not in self._memo:
            self._memo[key] = super().read(image, initial_section)
        return self._memo[key]


def scanner():
    return PanelScanner(CachingReader())


@pytest.fixture(scope="module")
def reader():
    return CachingReader()


def image_of(name):
    return fixture_frame(name).image


# --- R1: cada fila se lee completa ------------------------------------------------------------


@pytest.mark.parametrize("name,expected", [("upgrade_panel", OPEN_PANEL), ("upgrade_panel_filtered", FILTERED_PANEL)])
def test_every_row_is_read_whole(reader, name, expected):
    reading = reader.read(image_of(name))
    assert tuples(reading.rows) == expected
    assert reading.partial == 0


def test_cost_followed_by_the_icon_is_not_read_with_the_icon_as_a_digit(reader):
    """E1R1: «5 000» con el icono de elixir a la derecha es 5000, no 50006."""
    lab = reader.read(image_of("upgrade_panel")).rows[0]
    assert (lab.cost, lab.resource) == (5000, "elixir")


def test_thin_one_in_1_000_is_read_as_1000(reader):
    """E2R1: la fila del muro cuesta «1 000»: 1000, no 000."""
    wall = [r for r in reader.read(image_of("upgrade_panel_filtered")).rows if r.name == "Wall"][0]
    assert (wall.cost, wall.resource, wall.quantity) == (1000, "gold", 26)


def test_free_row_is_free_without_cost_or_resource_but_not_unknown(reader):
    """E3R1: «FREE» no es una fila desconocida."""
    wall = [r for r in reader.read(image_of("upgrade_panel")).rows if r.name == "Wall"][0]
    assert wall.free and wall.cost is None and wall.resource is None
    assert wall.spendable and wall.kind == "wall" and wall.quantity == 24


def test_red_cost_is_not_affordable(reader):
    rows = {r.name: r for r in reader.read(image_of("upgrade_panel_filtered")).rows}
    assert rows["Town Hall"].affordable is False and rows["Town Hall"].cost == 25000
    assert rows["Clan Castle"].affordable is True


def test_gem_cost_in_red_is_read_with_its_resource(reader):
    hut = [r for r in reader.read(image_of("upgrade_panel")).rows if r.name == "Builder's Hut"][0]
    assert (hut.cost, hut.resource, hut.affordable) == (500, "gems", False)


def test_a_similar_name_is_not_confused(reader):
    """«Archer Tower» se parece a «Archer Queen» y a «Wizard Tower»: no se elige ninguna de las dos."""
    names = [r.name for r in reader.read(image_of("upgrade_panel_filtered")).rows]
    assert "Archer Tower" in names and "Archer Queen" not in names


# --- R3: cabeceras y filas especiales ---------------------------------------------------------


def test_section_headers_are_found_even_against_the_top_edge(reader):
    """E1R3: «Suggested upgrades» está pegada al borde superior del panel."""
    headers = reader.read(image_of("upgrade_panel")).headers
    assert set(headers) == {"suggested", "other"}
    assert headers["suggested"] < 0.1 * H < headers["other"]


def test_rows_with_a_description_are_a_single_row(reader):
    """E2R3: el Laboratorio y la Bomba llevan una línea de descripción: una fila cada una."""
    rows = reader.read(image_of("upgrade_panel")).rows
    assert [r.name for r in rows].count("Laboratory") == 1 and len(rows) == 8


def test_low_cost_decision_is_explicit(reader):
    """E3R3: costes de 3 dígitos (150) se aceptan porque `min_cost` es 100; subirlo es una decisión."""
    assert PanelLayout().min_cost == 100
    cheap = [r for r in reader.read(image_of("upgrade_panel")).rows if r.cost == 150]
    assert len(cheap) == 2
    strict = PanelReader(layout=PanelLayout(min_cost=1000)).read(image_of("upgrade_panel"))
    gold_mine = [r for r in strict.rows if r.name == "Gold Mine"][0]
    assert gold_mine.cost is None and gold_mine.resource is None and not gold_mine.spendable


# --- robustez ---------------------------------------------------------------------------------


@pytest.mark.parametrize("scale", [1.25, 1.5])
def test_reading_survives_window_resize(reader, scale):
    image = cv2.resize(image_of("upgrade_panel"), (int(W * scale), int(H * scale)), interpolation=cv2.INTER_AREA)
    assert tuples(reader.read(image).rows) == OPEN_PANEL


def test_rows_cut_by_the_panel_edge_are_counted_as_partial_not_read(reader):
    short = PanelReader(layout=PanelLayout(bottom=0.56)).read(image_of("upgrade_panel"))
    assert short.partial > 0 and len(short.rows) < 8
    assert all(r.y < 0.56 * H for r in short.rows)


def test_unreadable_name_gives_none_not_a_guess():
    image = image_of("upgrade_panel").copy()
    image[int(0.07 * H):int(0.17 * H), int(0.77 * W):int(0.90 * W)] = (110, 80, 60)  # tapa el nombre del Laboratorio
    first = PanelReader().read(image).rows[0]
    assert first.name is None and first.cost == 5000


def test_ocr_resolves_a_name_the_font_could_not():
    image = image_of("upgrade_panel").copy()
    image[int(0.07 * H):int(0.17 * H), int(0.77 * W):int(0.90 * W)] = (110, 80, 60)
    ocr = FakeOcr(words=[WordBox("Laboratorv", Rect(0, 0, 90, 14), 0.8)])
    assert PanelReader(ocr=ocr).read(image).rows[0].name == "Laboratory"
    assert ocr.calls == ["words"]


def test_unknown_currency_is_not_spendable():
    """Una moneda de crafteo (matiz que no es oro, elixir ni gema) no es gastable."""
    assert classify_icon(24, 220) == "gold" and classify_icon(39, 190) == "gems"
    assert classify_icon(146, 206) == "elixir" and classify_icon(140, 100) == "dark_elixir"
    assert classify_icon(100, 200) is None
    row = PanelRow("X", 1, 100, None, False, False, None, True, "upgrade", None, (0, 0), 0)
    assert not row.spendable


def test_profiles_exist_for_every_variant_and_only_the_village_is_measured():
    assert set(PANEL_PROFILES) == {"village", "lab", "pets", "builder_base"}
    assert [k for k, v in PANEL_PROFILES.items() if not v.provisional] == ["village"]


# --- R2: recorrido ----------------------------------------------------------------------------


def frames(*names):
    return [fixture_frame(n) for n in names]


def test_panel_that_never_changes_stops_without_reaching_the_step_limit():
    """E2R2: dos capturas seguidas idénticas cierran el recorrido."""
    ctx = make_ctx(frames("upgrade_panel"))
    scan = scanner().scan(ctx, max_steps=10)
    assert scan.reached_end and scan.steps == 1
    assert tuples(scan.rows) == OPEN_PANEL


def test_pages_are_merged_and_each_row_is_delivered_once():
    """E1R2: más filas que pantalla. Tras desplazar, cada fila sale una sola vez."""
    ctx = make_ctx(frames("upgrade_panel", "upgrade_panel_filtered"), [Transition(0, "swipe", 1)])
    scan = scanner().scan(ctx, max_steps=10)
    keys = [(r.name, r.quantity) for r in scan.rows]
    assert len(keys) == len(set(keys)) == 8 + 9 - 3  # Laboratory, Clan Castle y Army Camp están en las dos
    assert scan.reached_end and scan.steps == 2
    assert keys[:3] == [("Laboratory", 1), ("Elixir Collector", 1), ("Builder's Hut", 1)]


def test_step_limit_ends_the_scan_without_claiming_the_end():
    ctx = make_ctx(
        frames("upgrade_panel", "upgrade_panel_filtered"),
        [Transition(0, "swipe", 1), Transition(1, "swipe", 0)],
    )
    scan = scanner().scan(ctx, max_steps=3)
    assert not scan.reached_end and scan.steps == 3


def test_scroll_direction_follows_where_the_scan_starts():
    down = make_ctx(frames("upgrade_panel"))
    scanner().scan(down, from_top=True)
    up = make_ctx(frames("upgrade_panel"))
    scanner().scan(up, from_top=False)
    (y1, y2) = down.device.actions[0].args[1::2][:2]
    assert y1 > y2  # desde arriba: el contenido sube
    (y1, y2) = up.device.actions[0].args[1::2][:2]
    assert y1 < y2


def test_scan_from_the_bottom_delivers_rows_in_panel_order():
    """Desde abajo, lo que aparece al subir va encima, sin invertir el orden dentro de cada página."""
    ctx = make_ctx(frames("upgrade_panel_filtered", "upgrade_panel"), [Transition(0, "swipe", 1)])
    scan = scanner().scan(ctx, from_top=False)
    assert [(r.name, r.quantity) for r in scan.rows] == [
        ("Elixir Collector", 1), ("Builder's Hut", 1), ("Gold Mine", 1), ("Bomb", 2), ("Wall", 24),
        ("Town Hall", 1), ("Clan Castle", 1), ("Laboratory", 1), ("Army Camp", 2), ("Archer Tower", 1),
        ("Cannon", 2), ("Wall", 26), ("Gold Storage", 2), ("Elixir Storage", 2),
    ]


def test_scan_honours_a_stop_request():
    ctx = make_ctx(frames("upgrade_panel"))
    ctx.stop.request()
    with pytest.raises(StopRequested):
        scanner().scan(ctx)


def test_panel_diff_is_zero_for_identical_and_large_for_different_pages():
    a, b = image_of("upgrade_panel"), image_of("upgrade_panel_filtered")
    assert panel_diff(a, a) == 0.0 and panel_diff(a, b) > 0.01


# --- orden y filtro del panel -----------------------------------------------------------------


def test_verify_order_confirms_cost_max_and_flags_the_default_order(reader):
    filtered = reader.read(image_of("upgrade_panel_filtered")).rows
    assert verify_order(filtered, "cost_max") == (True, "")
    assert not verify_order(filtered, "cost_min")[0]
    ok, reason = verify_order(reader.read(image_of("upgrade_panel")).rows, "cost_max")
    assert not ok and "rompe el orden" in reason


def test_scan_reports_whether_the_requested_order_was_applied():
    ok = scanner().scan(make_ctx(frames("upgrade_panel_filtered")), expect_order="cost_max")
    bad = scanner().scan(make_ctx(frames("upgrade_panel")), expect_order="cost_max")
    assert ok.order_ok is True and bad.order_ok is False and bad.order_reason


def sort_ctx(popup="unknown_filter_menu", after="upgrade_panel_filtered"):
    h, w = fixture_frame("upgrade_panel").image.shape[:2]
    button = FILTER_BUTTON.rect(fixture_frame("upgrade_panel").image)
    return make_ctx(
        frames("upgrade_panel", popup, after),
        [Transition(0, "tap", 1, button), Transition(1, "tap", 2)],
    )


def test_apply_sort_opens_the_popup_and_taps_the_requested_button():
    ctx = sort_ctx()
    assert apply_sort(ctx, "cost_max") is True
    first, second = (a.args for a in ctx.device.actions)
    assert FILTER_BUTTON.rect(image_of("upgrade_panel")).contains(*first)
    assert second[0] < 0.5 * W  # «Cost Max» es el botón de la izquierda


def test_apply_sort_picks_cost_min_on_the_right():
    ctx = sort_ctx()
    assert apply_sort(ctx, "cost_min") is True
    assert ctx.device.actions[1].args[0] > 0.5 * W


def test_apply_sort_taps_nothing_more_when_the_popup_did_not_open():
    ctx = make_ctx(frames("upgrade_panel"))
    assert apply_sort(ctx, "cost_max") is False
    assert len(ctx.device.actions) == 1  # solo el botón del filtro
