"""Pruebas de la visión base sobre fotogramas sintéticos (renderizados con la fuente del juego).

Los fixtures reales anonimizados llegan cuando haya capturas; aquí no hay red ni EasyOCR.
"""

import numpy as np
import pytest

from pyclash.device.base import Rect
from pyclash.runtime.stop import StopRequested, StopToken
from pyclash.vision import color
from pyclash.vision.numeric import parse_duration, parse_numeric_field
from pyclash.vision.ocr import DigitGlyph, FakeOcr, WordBox, read_digit_string, read_words
from pyclash.vision.preprocess import binarize_white, filter_components
from pyclash.vision.result import fraction_rect
from pyclash.vision.templates import TemplateMatcher
from pyclash.vision.text import DigitReader, TextFinder, render_text
from pyclash.vision.vocabulary import Vocabulary

BG = (40, 60, 110)


def canvas(w=640, h=360):
    img = np.empty((h, w, 3), np.uint8)
    img[:] = BG
    return img


def paste(img, patch, x, y):
    """Pega la tinta del parche (lo que no es negro) sobre el fondo."""
    h, w = patch.shape[:2]
    ink = patch.max(axis=2) > 0
    img[y:y + h, x:x + w][ink] = patch[ink]


# --- R1: etiquetas conocidas por la fuente del juego ---------------------------------------

def test_e1r1_boton_con_texto_conocido():
    img = canvas()
    paste(img, render_text("Find a Match", 29), 300, 200)
    m = TextFinder().find(img, "Find a Match", 29)
    assert m is not None and m.confidence > 0.9
    assert m.rect.x == 300 and m.rect.y == 200
    assert m.provenance.method == "font:white:s29"


def test_e2r1_texto_ausente_no_da_coincidencia_espuria():
    img = canvas()
    paste(img, render_text("Battle", 37), 100, 100)
    assert TextFinder().find(img, "Find a Match", 29) is None
    assert TextFinder().find(canvas(), "Find a Match", 29) is None


def test_e3r1_texto_verde_new():
    img = canvas()
    paste(img, render_text("Cannon", 17, "green"), 450, 120)
    finder = TextFinder()
    assert finder.find(img, "Cannon", 17, "white") is None
    m = finder.find(img, "Cannon", 17, "green")
    assert m is not None and m.confidence > 0.9
    best = finder.find_any(img, "Cannon", [17], colors=("white", "green"))
    assert best is not None and best.provenance.method == "font:green:s17"


def test_barrido_de_tamanos_encuentra_el_real():
    img = canvas()
    paste(img, render_text("Battle", 37), 100, 100)
    sweep = TextFinder().calibrate_size(img, "Battle", range(20, 50))
    assert sweep.best_size == 37 and sweep.best_confidence > 0.9


def test_region_limita_la_busqueda_y_devuelve_coordenadas_del_fotograma():
    img = canvas()
    paste(img, render_text("Battle", 37), 100, 100)
    paste(img, render_text("Battle", 37), 400, 250)
    finder = TextFinder()
    m = finder.find(img, "Battle", 37, region=Rect(320, 200, 320, 160))
    assert m is not None and (m.rect.x, m.rect.y) == (400, 250)
    assert m.provenance.region == Rect(320, 200, 320, 160)
    assert finder.find(img, "Battle", 37, region=Rect(0, 0, 50, 50)) is None


# --- Dígitos con la fuente ----------------------------------------------------------------

@pytest.mark.parametrize("number", ["2000", "1234567890", "18", "7"])
def test_digitos_con_la_fuente(number):
    img = canvas()
    paste(img, render_text(number, 25), 200, 150)
    r = DigitReader().read(img, 25)
    assert r is not None and r.value == number and r.confidence > 0.75
    assert r.provenance.method == "digits:font:s25"


def test_digitos_sin_numero_da_none():
    assert DigitReader().read(canvas(), 25) is None


# --- Buscador de plantillas ---------------------------------------------------------------

def test_find_all_suprime_solapes_y_ordena_por_confianza():
    img = canvas()
    tpl = render_text("Gold", 20)
    for x in (50, 200, 350):
        paste(img, tpl, x, 80)
    found = TemplateMatcher(threshold=0.9).find_all(img, tpl)
    assert sorted(m.rect.x for m in found) == [50, 200, 350]
    assert [m.confidence for m in found] == sorted((m.confidence for m in found), reverse=True)


def test_plantilla_mayor_que_la_region_no_falla():
    assert TemplateMatcher().find(canvas(20, 20), render_text("Find a Match", 29)) is None


def test_lote_devuelve_un_resultado_por_nombre():
    img = canvas()
    paste(img, render_text("Battle", 37), 100, 100)
    out = TemplateMatcher().find_batch(img, {"a": render_text("Battle", 37), "b": render_text("Zzzz", 37)})
    assert out["a"] is not None and out["b"] is None


def test_lote_respeta_la_parada():
    token = StopToken()
    token.request()

    def check():
        if token.requested:
            raise StopRequested()

    with pytest.raises(StopRequested):
        TemplateMatcher().find_batch(canvas(), {"a": render_text("A", 20)}, check_stop=check)


# --- Preprocesado y color -----------------------------------------------------------------

def test_binarizar_blanco_y_filtrar_componentes_por_area():
    img = canvas(60, 30)
    img[5:15, 5:15] = 255   # 100 px
    img[20, 40] = 255       # ruido de 1 px
    mask = binarize_white(img)
    assert (mask > 0).sum() == 101
    assert (filter_components(mask, min_area=10) > 0).sum() == 100


def test_recorte_fraccional():
    r = fraction_rect(canvas(200, 100), 0.5, 0.0, 1.0, 0.5)
    assert r == Rect(100, 0, 100, 50)
    with pytest.raises(ValueError):
        fraction_rect(canvas(), 0.6, 0.0, 0.5, 1.0)


def test_fraccion_de_color_en_lab():
    img = canvas(10, 10)
    img[:5] = color.INSUFFICIENT_RED
    assert color.color_fraction(img, color.INSUFFICIENT_RED) == pytest.approx(0.5)
    assert not color.has_color(canvas(), color.INSUFFICIENT_RED)
    near = (250, 120, 110)
    img[:5] = near
    assert color.color_fraction(img, color.INSUFFICIENT_RED) == pytest.approx(0.5)


def test_boton_verde_por_blob():
    img = canvas()
    img[200:250, 300:420] = color.BUTTON_GREEN
    img[10:20, 10:20] = color.BUTTON_GREEN  # blob pequeño: ruido
    buttons = color.find_green_buttons(img)
    assert len(buttons) == 1
    assert buttons[0].rect == Rect(300, 200, 120, 50)
    assert color.find_green_buttons(canvas()) == []


# --- R2: el OCR corrige solo lo que debe --------------------------------------------------

def test_e1r2_coste_con_letra_mal_leida():
    assert parse_numeric_field("2O0O") == 2000
    assert parse_numeric_field("1,250") == 1250


@pytest.mark.parametrize("name", ["Spring Trap", "Bomb Tower", "SOS", "Barbarian"])
def test_e2r2_los_nombres_no_son_numeros(name):
    assert parse_numeric_field(name) is None


def test_duracion():
    assert parse_duration("1d 4h 20m") == 86400 + 4 * 3600 + 20 * 60
    assert parse_duration("45s") == 45
    assert parse_duration("Cannon") is None


def test_ocr_falso_aporta_procedencia_y_coordenadas_de_fotograma():
    ocr = FakeOcr(
        words=[WordBox("Cannon", Rect(5, 6, 40, 10), 0.93)],
        digits=[DigitGlyph("0", Rect(20, 0, 8, 10), 0.7), DigitGlyph("2", Rect(0, 0, 8, 10), 0.9)],
    )
    img = canvas()
    [w] = read_words(ocr, img, Rect(100, 50, 200, 100))
    assert w.value == "Cannon" and w.confidence == 0.93
    assert w.provenance.region == Rect(105, 56, 40, 10)
    assert w.provenance.method == "ocr:fake:words"
    d = read_digit_string(ocr, img)
    assert d is not None and d.value == "20" and d.confidence == 0.7
    assert read_digit_string(FakeOcr(), img) is None


def test_importar_ocr_no_importa_easyocr():
    import sys
    import pyclash.vision.ocr  # noqa: F401
    assert "easyocr" not in sys.modules and "torch" not in sys.modules


# --- R3: vocabulario ----------------------------------------------------------------------

@pytest.fixture(scope="module")
def vocab():
    return Vocabulary.load_packaged()


def test_e1r3_errata_se_rechaza_con_sugerencia(vocab):
    [v] = vocab.validate(["Archer Quen"])
    assert not v.valid and v.suggestions[0] == "Archer Queen"


def test_e2r3_nombre_inexistente_sin_sugerencia_falsa(vocab):
    [v] = vocab.validate(["Torre Mágica Suprema"])
    assert not v.valid and v.suggestions == ()


def test_e3r3_el_vocabulario_empaquetado_funciona_sin_red(vocab, monkeypatch):
    import socket

    def no_network(*a, **k):
        raise AssertionError("no debe usar la red")

    monkeypatch.setattr(socket, "socket", no_network)
    assert Vocabulary.load_packaged().validate(["Archer Queen"])[0].valid


@pytest.mark.parametrize("name", ["archer queen", "P.E.K.K.A", "pekka", "Builders Hut", "x-bow"])
def test_nombres_validos_sin_importar_mayusculas_ni_puntuacion(vocab, name):
    assert vocab.validate([name])[0].valid


@pytest.mark.parametrize(
    "read, expected",
    [("Archer Qveen", "Archer Queen"), ("Cannon", "Cannon"), ("Alr Defense", "Air Defense"), ("zzzzzz", None)],
)
def test_resolver_texto_leido_al_canonico(vocab, read, expected):
    assert vocab.resolve(read).canonical == expected


def test_resolver_ambiguo_no_elige(vocab):
    v = Vocabulary({"a": ["Gold Mine", "Gold Mina"]})
    r = v.resolve("Gold Minx")
    assert r.canonical is None and set(r.suggestions) == {"Gold Mine", "Gold Mina"}


def test_resolver_por_categoria(vocab):
    assert vocab.resolve("Barbarian", ["heroes"]).canonical is None
    assert vocab.resolve("Barbarian", ["troops"]).canonical == "Barbarian"
    with pytest.raises(KeyError):
        vocab.names(["inexistente"])
