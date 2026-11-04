"""Pestaña «Verificación» de la pantalla de pruebas (issue #33): lanza las comprobaciones de
`verification.py` contra el juego real, de lo más seguro a lo que gasta. Solo Qt; la lógica está allí."""

from __future__ import annotations

import threading

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..device.base import Device
from ..policy.gold_reserve import GoldReserve
from ..runtime.stop import StopToken
from ..tasks.base import Outcome
from .verification import CHECKS, DEFAULT_CAPTURE_DIR, RISK_LABELS, Check, CheckOutcome, run_check


_CONFIRM = {
    "spend": ("Gasta recursos de verdad", "Esto actúa sobre tu cuenta real. ¿Continuar?"),
}


class _Bridge(QObject):
    """Pasa el texto y el final del hilo de trabajo al hilo de la interfaz (las señales de Qt lo hacen solas)."""

    line = Signal(str)
    finished = Signal(object)


class VerificationPanel(QWidget):
    def __init__(self, device: Device) -> None:
        super().__init__()
        self.device = device
        self._stop: StopToken | None = None
        self._gold_reserve = GoldReserve()  # una por ventana: «Leer coste de búsqueda» la rellena y «Muros» la usa
        self._buttons: list[QPushButton] = []
        self._bridge = _Bridge()
        self._bridge.line.connect(self._append)
        self._bridge.finished.connect(self._on_finished)

        layout = QVBoxLayout(self)
        intro = QLabel(
            "Prueba cada pieza en el juego real, de arriba (seguro) a abajo (gasta). Pon el juego en inglés "
            "y la ventana visible. «Parar» corta la tarea en el siguiente paso."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        for risk, title in RISK_LABELS.items():
            box = QGroupBox(title)
            row = QVBoxLayout(box)
            for check in (c for c in CHECKS if c.risk == risk):
                btn = QPushButton(check.label)
                btn.setToolTip(check.help)
                btn.clicked.connect(lambda _checked=False, c=check: self.start(c))
                row.addWidget(btn)
                self._buttons.append(btn)
            layout.addWidget(box)

        controls = QHBoxLayout()
        self.stop_btn = QPushButton("Parar")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self.on_stop)
        copy_btn = QPushButton("Copiar registro")
        copy_btn.clicked.connect(lambda: QGuiApplication.clipboard().setText(self.log.toPlainText()))
        clear_btn = QPushButton("Limpiar")
        clear_btn.clicked.connect(lambda: self.log.clear())
        for w in (self.stop_btn, copy_btn, clear_btn):
            controls.addWidget(w)
        layout.addLayout(controls)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setPlaceholderText(f"Aquí aparece lo que lee y decide el bot. Las capturas de fallos van a {DEFAULT_CAPTURE_DIR}/.")
        layout.addWidget(self.log, 1)

    # ------------------------------------------------------------------------------ ejecución

    def start(self, check: Check) -> None:
        if self._stop is not None:
            return
        if check.risk in _CONFIRM:
            title, warning = _CONFIRM[check.risk]
            answer = QMessageBox.warning(
                self,
                title,
                f"«{check.label}»: {check.help}\n\n{warning}",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        self._stop = StopToken()
        self._set_running(True)
        self._append(f"\n=== {check.label} [{check.risk}] ===")
        stop = self._stop

        def work() -> None:
            outcome = run_check(
                check, self.device, stop=stop, on_line=self._bridge.line.emit, gold_reserve=self._gold_reserve
            )
            self._bridge.finished.emit(outcome)

        threading.Thread(target=work, daemon=True).start()

    def on_stop(self) -> None:
        if self._stop is not None:
            self._stop.request()
            self._append("Parada solicitada: se corta en el siguiente paso…")

    def _on_finished(self, outcome: CheckOutcome) -> None:
        for line in outcome.lines:
            self._append(line)
        if not outcome.ok:
            self._append("✘ Con errores: copia el registro y guarda una captura.")
        elif outcome.result is not None and outcome.result.outcome is Outcome.NOTHING_TO_DO:
            self._append("⚠ No hizo nada (no es un error del programa, pero comprueba si debía haber actuado).")
        else:
            self._append("✔ Sin errores.")
        self._stop = None
        self._set_running(False)

    def _set_running(self, running: bool) -> None:
        for b in self._buttons:
            b.setEnabled(not running)
        self.stop_btn.setEnabled(running)

    def _append(self, text: str) -> None:
        self.log.appendPlainText(text)
