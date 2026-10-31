"""Pantalla mínima para comprobar que el programa ve el juego: `python -m pyclash.interface.test_window`."""

from __future__ import annotations

import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..device import geometry
from ..device.base import DeviceError
from ..device.display import DisplayService
from ..device.gpg import GooglePlayGamesDevice
from ..device.probe import ProbeStatus, probe

_REASONS = {
    "not_found": "No se encuentra la ventana.",
    "minimized": "La ventana está minimizada.",
    "fullscreen": "La ventana está a pantalla completa sin marco; no se puede redimensionar.",
    "not_16_9_after_resize": "Tras redimensionar, el juego mantiene su propia proporción.",
    "no_16_9_mode": "La pantalla no tiene ningún modo 16:9.",
    "not_supported": "El modo 16:9 no es compatible con la pantalla.",
}


class TestWindow(QWidget):
    __test__ = False  # no es una clase de tests de pytest

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("PyClash — Probar dispositivo")
        self.device = GooglePlayGamesDevice()
        self.display = DisplayService()

        self.combo = QComboBox()
        self.refresh_btn = QPushButton("Actualizar lista")
        self.test_btn = QPushButton("Probar")
        self.restore_btn = QPushButton("Restaurar ventana")
        self.restore_btn.setVisible(False)
        self.status = QLabel("Pulsa «Probar».")
        self.status.setWordWrap(True)
        self.preview = QLabel()
        self.preview.setMinimumSize(480, 270)

        extra = QHBoxLayout()
        for text, handler in [
            ("Ajustar ventana a 16:9", self.on_resize),
            ("Ocultar ventana", self.on_hide),
            ("Mostrar ventana", self.on_show),
            ("Clic en el centro", self.on_click_center),
            ("Pantalla → 16:9", self.on_display_switch),
            ("Restaurar pantalla", self.on_display_restore),
        ]:
            btn = QPushButton(text)
            btn.clicked.connect(handler)
            extra.addWidget(btn)

        top = QHBoxLayout()
        top.addWidget(self.combo, 1)
        top.addWidget(self.refresh_btn)
        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self.test_btn)
        layout.addWidget(self.restore_btn)
        layout.addWidget(self.status)
        layout.addWidget(self.preview)
        layout.addLayout(extra)

        self.refresh_btn.clicked.connect(self.refresh_windows)
        self.combo.activated.connect(self.on_pick)
        self.test_btn.clicked.connect(self.on_test)
        self.restore_btn.clicked.connect(self.on_restore_window)
        self.refresh_windows()
        self.recover_pending()

    def say(self, text: str) -> None:
        self.status.setText(text)

    def recover_pending(self) -> None:
        """Al abrir: deshace lo que quedó a medias si la app se cerró a la fuerza."""
        notes = []
        if self.display.restore_if_pending():
            notes.append("Se restauró la resolución de pantalla que quedó pendiente.")
        if self.device.has_hidden_marker() and self.device.show_back()[0]:
            notes.append("Se volvió a mostrar la ventana del juego que quedó oculta.")
        if notes:
            self.say(" ".join(notes))

    def refresh_windows(self) -> None:
        self.windows = self.device.windows()
        self.combo.clear()
        self.combo.addItem("Detección automática", None)
        for w in self.windows:
            self.combo.addItem(w.label, w)

    def on_pick(self, _index: int) -> None:
        self.device.select(self.combo.currentData())

    def on_test(self) -> None:
        result = probe(self.device)
        self.restore_btn.setVisible(result.status in (ProbeStatus.MINIMIZED, ProbeStatus.BLACK))
        self.say(result.message)
        if result.frame is None:
            self.preview.clear()
            return
        img = result.frame.image
        h, w = img.shape[:2]
        qimg = QImage(img.data, w, h, 3 * w, QImage.Format.Format_RGB888).copy()
        self.preview.setPixmap(
            QPixmap.fromImage(qimg).scaled(self.preview.size(), Qt.AspectRatioMode.KeepAspectRatio)
        )

    def on_restore_window(self) -> None:
        try:
            self.device.restore_window()
        except DeviceError as e:
            self.say(str(e))
            return
        self.on_test()

    def on_resize(self) -> None:
        ok, size, reason = self.device.resize_to_16_9()
        detail = f"{size[0]}×{size[1]}" if size else ""
        self.say(f"Ventana ajustada a 16:9 ({detail})." if ok else f"No se pudo ajustar: {_REASONS.get(reason, reason)} {detail}")

    def on_hide(self) -> None:
        ok, reason = self.device.hide_offscreen()
        self.say("Ventana oculta fuera de pantalla." if ok else f"No se pudo ocultar: {_REASONS.get(reason, reason)}")

    def on_show(self) -> None:
        ok, reason = self.device.show_back()
        self.say("Ventana mostrada de nuevo." if ok else f"No se pudo mostrar: {reason}")

    def on_click_center(self) -> None:
        try:
            frame = self.device.capture()
            x, y = geometry.fraction_to_pixel(0.5, 0.5, frame.viewport)
            self.device.tap(x, y)
        except DeviceError as e:
            self.say(str(e))
            return
        self.say(f"Clic enviado en el centro del juego ({x}, {y}).")

    def on_display_switch(self) -> None:
        answer = QMessageBox.question(
            self, "Cambiar resolución", "Se cambiará la resolución de la pantalla principal a 16:9. ¿Continuar?"
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        ok, size, reason = self.display.switch_to_16_9()
        self.say(f"Pantalla en {size[0]}×{size[1]}." if ok and size else f"No se pudo cambiar: {_REASONS.get(reason, reason)}")

    def on_display_restore(self) -> None:
        ok, size, reason = self.display.restore()
        self.say(f"Pantalla restaurada a {size[0]}×{size[1]}." if ok and size else f"No se pudo restaurar: {reason}")

    def closeEvent(self, event) -> None:  # noqa: N802 (nombre impuesto por Qt)
        """Cierre normal: deja pantalla y ventana como estaban."""
        if self.display.has_pending_restore():
            self.display.restore()
        if self.device.has_hidden_marker():
            self.device.show_back()
        super().closeEvent(event)


def main() -> None:
    app = QApplication(sys.argv)
    win = TestWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
