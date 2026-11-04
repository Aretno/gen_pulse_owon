"""Panel PyQt6 del generador DGE2070, embebible (p. ej. como pestaña de senda).

    from owon_dge.gui import PulseGeneratorPanel
    panel = PulseGeneratorPanel(backend)   # Dge2070 o RemoteDge2070

Ejecutable suelto:  python -m owon_dge.gui [--url http://host:8750 --token T]
"""
from __future__ import annotations

import argparse
import sys

from PyQt6.QtCore import QObject, QThread, QTimer, pyqtSignal
from PyQt6.QtWidgets import (QApplication, QCheckBox, QDoubleSpinBox, QFormLayout,
                             QGroupBox, QHBoxLayout, QLabel, QPushButton,
                             QSpinBox, QVBoxLayout, QWidget)

# (campo, etiqueta, unidad mostrada, factor a SI, máx, decimales)
FIELDS = [
    ("freq", "Frecuencia", "MHz", 1e6, 70.0, 6),
    ("width", "Anchura", "ns", 1e-9, 1e9, 3),
    ("high", "Nivel alto", "V", 1.0, 10.0, 3),
    ("low", "Nivel bajo", "V", 1.0, 10.0, 3),
    ("lead", "Flanco subida", "ns", 1e-9, 1e9, 3),
    ("trail", "Flanco bajada", "ns", 1e-9, 1e9, 3),
]


class _Worker(QObject):
    """Ejecuta las llamadas al backend fuera del hilo de la GUI."""
    done = pyqtSignal(object, str)      # resultado, error

    def run(self, fn):
        try:
            self.done.emit(fn(), "")
        except Exception as e:  # noqa: BLE001
            self.done.emit(None, str(e))


class PulseGeneratorPanel(QWidget):
    _request = pyqtSignal(object)

    def __init__(self, backend, channel=1, poll_ms=2000, parent=None):
        super().__init__(parent)
        self.backend, self.channel = backend, channel
        self._busy = False
        self._thread = QThread(self)
        self._worker = _Worker()
        self._worker.moveToThread(self._thread)
        self._request.connect(self._worker.run)
        self._worker.done.connect(self._on_done)
        self._thread.start()
        self._build()
        self._timer = QTimer(self, interval=poll_ms)
        self._timer.timeout.connect(self.refresh)
        self._timer.start()
        self.refresh()

    # --- UI
    def _build(self):
        lay = QVBoxLayout(self)
        self.idn = QLabel("Conectando…")
        lay.addWidget(self.idn)
        row = QHBoxLayout()
        row.addWidget(QLabel("Canal"))
        self.ch = QSpinBox(minimum=1, maximum=2, value=self.channel)
        self.ch.valueChanged.connect(self._ch_changed)
        row.addWidget(self.ch)
        self.out = QPushButton("Salida: ?")
        self.out.setCheckable(True)
        self.out.clicked.connect(self._toggle_out)
        row.addWidget(self.out)
        row.addStretch()
        lay.addLayout(row)

        box = QGroupBox("Pulso")
        form = QFormLayout(box)
        self.spins = {}
        for name, label, unit, k, mx, dec in FIELDS:
            sp = QDoubleSpinBox(decimals=dec, maximum=mx,
                                suffix=f" {unit}")
            sp.setKeyboardTracking(False)
            self.spins[name] = (sp, k)
            form.addRow(label, sp)
        self.duty = QLabel("—")
        form.addRow("Ciclo de trabajo", self.duty)
        lay.addWidget(box)

        self.edit = False
        btns = QHBoxLayout()
        apply_ = QPushButton("Aplicar")
        apply_.clicked.connect(self.apply)
        reread = QPushButton("Leer del equipo")
        reread.clicked.connect(lambda: self.refresh(force=True))
        btns.addWidget(apply_)
        btns.addWidget(reread)
        lay.addLayout(btns)
        self.status = QLabel("")
        lay.addWidget(self.status)
        lay.addStretch()
        for sp, _ in self.spins.values():
            sp.valueChanged.connect(lambda *_: setattr(self, "edit", True))

    # --- backend
    def _submit(self, fn, tag):
        if self._busy:
            return False
        self._busy, self._tag = True, tag
        self._request.emit(fn)
        return True

    def _ch_changed(self, v):
        self.channel, self.edit = v, False
        self.refresh(force=True)

    def refresh(self, force=False):
        if self.edit and not force:
            return   # no pisar lo que el usuario está escribiendo
        ch = self.channel
        self._submit(lambda: self.backend.get_state(ch), "state")

    def apply(self):
        ch = self.channel
        vals = {n: sp.value() * k for n, (sp, k) in self.spins.items()}
        def job():
            self.backend.set_pulse(ch, **vals)
            return self.backend.get_state(ch)
        if self._submit(job, "apply"):
            self.status.setText("Aplicando…")

    def _toggle_out(self, on):
        ch = self.channel
        def job():
            self.backend.set_output(ch, on)
            return self.backend.get_state(ch)
        self._submit(job, "out")

    def _on_done(self, res, err):
        tag, self._busy = self._tag, False
        if err:
            self.status.setText(f"⚠ {err}")
            return
        s = res
        self.idn.setText(s["idn"])
        self.out.setChecked(s["output"])
        self.out.setText("Salida: ON" if s["output"] else "Salida: OFF")
        if not self.edit or tag != "state":
            for n, (sp, k) in self.spins.items():
                if s.get(n) is not None:
                    sp.blockSignals(True)
                    sp.setValue(s[n] / k)
                    sp.blockSignals(False)
            self.edit = False
        self.duty.setText(f"{s['duty']:.2f} %")
        if tag == "apply":
            self.status.setText("Aplicado")
        elif tag != "state":
            self.status.setText("")

    def closeEvent(self, e):
        self._timer.stop()
        self._thread.quit()
        self._thread.wait(2000)
        super().closeEvent(e)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", help="servidor remoto; si falta, USB local")
    ap.add_argument("--token")
    a = ap.parse_args()
    if a.url:
        from .client import RemoteDge2070
        backend = RemoteDge2070(a.url, a.token)
    else:
        from .device import Dge2070
        backend = Dge2070()
    app = QApplication(sys.argv)
    w = PulseGeneratorPanel(backend)
    w.setWindowTitle("OWON DGE2070")
    w.resize(380, 420)
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
