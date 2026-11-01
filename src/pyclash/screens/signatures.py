"""Firmas de las pantallas del juego (interfaz en inglés), medidas sobre capturas reales.

Las regiones son fracciones del fotograma y los tamaños de fuente están a BASE_HEIGHT (1440),
calibrados con capturas de la ventana de Google Play Games (ver tests/fixtures y la prueba canario).
Los umbrales de texto (0.5) quedan por encima de la mayor confianza medida en las demás pantallas
(0.38; «Multiplayer» y «Next», con 0.44, llevan 0.6) y por debajo de las coincidencias reales (>= 0.63
a 720p de alto). La ventana debe tener al menos ~720 px de alto: por debajo, los textos pequeños
bajan de umbral.

Sin firma todavía (no hay captura real; se clasifican como `UNKNOWN` y salen por la vía segura):
Builder Base, tienda, laboratorio, mascotas, fase de colocación, cofre/recompensa e inicio de
sesión de Supercell. Para añadir una: capturarla, anonimizarla y medir sus indicadores con
`ScreenClassifier.diagnose`.
"""

from __future__ import annotations

from .classifier import (
    CloseButtonIndicator,
    GreenButtonIndicator,
    Screen,
    ScreenSignature,
    TemplateIndicator,
    TextIndicator,
)
from .layout import RoiFraction


def box(x1: float, y1: float, x2: float, y2: float) -> RoiFraction:
    return RoiFraction(x1, y1, x2 - x1, y2 - y1)


# --- regiones compartidas -------------------------------------------------------------------
_BATTLE_BUTTON = TextIndicator("Battle", box(0.0, 0.89, 0.11, 0.99), 28)
_BUILD_BUTTON = TextIndicator("Build", box(0.90, 0.89, 0.99, 0.99), 28)
_BOTTOM_LEFT_RED = box(0.0, 0.76, 0.12, 0.82)  # «End Battle» y «Surrender» comparten sitio

INACTIVITY_TITLE = TemplateIndicator("inactivity_title", box(0.27, 0.38, 0.62, 0.50), label="Anyone there?")
INACTIVITY_RELOAD = TemplateIndicator("inactivity_reload", box(0.27, 0.51, 0.50, 0.63), label="RELOAD GAME")

DEFAULT_SIGNATURES: tuple[ScreenSignature, ...] = (
    ScreenSignature(Screen.MAIN_VILLAGE, (_BATTLE_BUTTON, _BUILD_BUTTON)),
    # La aldea con una «X» roja en el terreno: se está colocando un edificio.
    ScreenSignature(
        Screen.PLACING_BUILDING,
        (_BATTLE_BUTTON, _BUILD_BUTTON, CloseButtonIndicator(box(0.30, 0.20, 0.85, 0.75))),
        priority=1,
    ),
    ScreenSignature(
        Screen.UPGRADE_PANEL,
        (
            TextIndicator("Buildings & Heroes", box(0.78, 0.0, 0.97, 0.08), 23),
            CloseButtonIndicator(box(0.94, 0.0, 1.0, 0.10)),
        ),
        priority=1,
    ),
    ScreenSignature(
        Screen.ATTACK_MENU,
        (
            TextIndicator("Multiplayer", box(0.04, 0.04, 0.18, 0.10), 28, threshold=0.6),
            TextIndicator("Find a Match", box(0.09, 0.64, 0.27, 0.72), 32),
        ),
        priority=1,
    ),
    ScreenSignature(
        Screen.ARMY_SELECTION,
        (
            TextIndicator("My Army", box(0.17, 0.11, 0.31, 0.18), 29),
            TextIndicator("Saved Recipes", box(0.42, 0.11, 0.58, 0.17), 25),
            CloseButtonIndicator(box(0.88, 0.08, 0.97, 0.19)),
        ),
        priority=1,
    ),
    ScreenSignature(
        Screen.FINDING_OPPONENT,
        (
            TextIndicator("Next", box(0.89, 0.70, 0.99, 0.78), 38, threshold=0.6),
            TextIndicator("End Battle", _BOTTOM_LEFT_RED, 25),
        ),
        priority=1,
    ),
    ScreenSignature(
        Screen.BATTLE,
        (
            TextIndicator("Overall Damage", box(0.85, 0.73, 0.99, 0.79), 23),
            TextIndicator("Surrender", _BOTTOM_LEFT_RED, 24),
        ),
        priority=1,
    ),
    # El título del diálogo identifica cuál es: «Okay» y «Cancel» por sí solos no bastan, porque
    # otros diálogos los tienen y un «Okay» sin identificar nunca se pulsa. El título es grande y con
    # contorno, de ahí su umbral más bajo.
    ScreenSignature(
        Screen.CONFIRM_SURRENDER,
        (
            TextIndicator("Surrender?", box(0.40, 0.29, 0.60, 0.39), 46, threshold=0.45),
            TextIndicator("Okay", box(0.54, 0.58, 0.64, 0.66), 42),
            TextIndicator("Cancel", box(0.34, 0.58, 0.46, 0.66), 42),
        ),
        priority=2,
    ),
    # Falta oro al buscar partida (menú de ataque o exploración): el botón verde compra el oro con
    # gemas y no se pulsa nunca; la salida es la X roja. Tiene prioridad sobre la pantalla de debajo.
    ScreenSignature(
        Screen.NEED_MORE_GOLD,
        (
            TextIndicator("You need more Gold", box(0.34, 0.30, 0.66, 0.38), 48, threshold=0.5),
            CloseButtonIndicator(box(0.62, 0.28, 0.72, 0.40)),
        ),
        priority=3,
    ),
    # Diálogo del sistema (no usa la fuente del juego) sobre la aldea, que se ve detrás con «Battle» y «Build»:
    # por eso manda sobre todo lo demás. Se exige el título Y el botón: otro diálogo con «RELOAD GAME»
    # (p. ej. un error de conexión) no es este y no se pulsa.
    ScreenSignature(
        Screen.INACTIVITY_POPUP,
        (INACTIVITY_TITLE, INACTIVITY_RELOAD),
        priority=4,
    ),
    # Textos pequeños y con confianza moderada: se piden dos de tres.
    ScreenSignature(
        Screen.RESULT,
        (
            TextIndicator("Total damage:", box(0.44, 0.10, 0.56, 0.14), 19, threshold=0.4),
            TextIndicator("You got:", box(0.45, 0.41, 0.55, 0.45), 19, threshold=0.5),
            GreenButtonIndicator(box(0.40, 0.76, 0.60, 0.94), name="botón «Return Home»"),
        ),
        min_matches=2,
        priority=1,
    ),
)
