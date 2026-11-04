"""Panel PyQt6 del generador DGE2070, embebible (p. ej. como pestaña de senda).

    from owon_dge.gui import PulseGeneratorPanel
    panel = PulseGeneratorPanel(backend)   # Dge2070 o RemoteDge2070

Ejecutable suelto:  python -m owon_dge.gui [--url http://host:8750 --token T]
"""
from __future__ import annotations

import argparse
import sys

from PyQt6.QtCore import QObject, QThread, QTimer, pyqtSignal
from PyQt6.QtWidgets import (QApplication, QButtonGroup, QComboBox,
                             QDoubleSpinBox, QFormLayout, QGroupBox,
                             QHBoxLayout, QLabel, QPushButton, QVBoxLayout,
                             QWidget)

from .device import FUNCTIONS, PARAMS

# campo -> (etiqueta, unidad mostrada, factor a SI, mín, máx, decimales)
FIELDS = {
    "freq": ("Frecuencia", "MHz", 1e6, 0.0, 70.0, 6),
    "width": ("Anchura", "ns", 1e-9, 0.0, 1e9, 3),
    "high": ("Nivel alto", "V", 1.0, -10.0, 10.0, 3),
    "low": ("Nivel bajo", "V", 1.0, -10.0, 10.0, 3),
    "lead": ("Flanco subida", "ns", 1e-9, 0.0, 1e9, 3),
    "trail": ("Flanco bajada", "ns", 1e-9, 0.0, 1e9, 3),
    "symmetry": ("Simetría", "%", 1.0, 0.0, 100.0, 1),
    "phase": ("Fase", "°", 1.0, 0.0, 360.0, 2),
}
FIELD_ORDER = ("freq", "width", "symmetry", "high", "low", "lead", "trail",
               "phase")

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
    # Estado del canal tras cada lectura/aplicación (dict de get_state).
    stateRead = pyqtSignal(dict)
    channelChanged = pyqtSignal(int)

    def __init__(self, backend, channel=1, poll_ms=2000, parent=None):
        super().__init__(parent)
        self.backend, self.channel = backend, channel
        self.last_state = None
        self._busy = False
        self._function = "PULS"
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
        self._ch_group = QButtonGroup(self)
        self._ch_buttons = {}
        for ch in (1, 2):
            btn = QPushButton(f"CH{ch}")
            btn.setCheckable(True)
            btn.setChecked(ch == self.channel)
            self._ch_group.addButton(btn, ch)
            self._ch_buttons[ch] = btn
            row.addWidget(btn)
        self._ch_group.idClicked.connect(self._ch_changed)
        self.out = QPushButton("Salida: ?")
        self.out.setCheckable(True)
        self.out.clicked.connect(self._toggle_out)
        row.addWidget(self.out)
        row.addStretch()
        lay.addLayout(row)

        box = QGroupBox("Señal")
        form = QFormLayout(box)
        self.func = QComboBox()
        for key, name in FUNCTIONS.items():
            self.func.addItem(name, key)
        self.func.setToolTip("Forma de onda del canal; Aplicar la escribe en "
                             "el equipo")
        self.func.currentIndexChanged.connect(self._function_picked)
        form.addRow("Tipo", self.func)
        self.spins, self._labels = {}, {}
        for name in FIELD_ORDER:
            label, unit, k, mn, mx, dec = FIELDS[name]
            sp = QDoubleSpinBox(decimals=dec, minimum=mn, maximum=mx,
                                suffix=f" {unit}")
            sp.setKeyboardTracking(False)
            sp.valueChanged.connect(lambda *_: setattr(self, "edit", True))
            self.spins[name] = (sp, k)
            self._labels[name] = QLabel(label)
            form.addRow(self._labels[name], sp)
        self.duty_label = QLabel("Ciclo de trabajo")
        self.duty = QLabel("—")
        form.addRow(self.duty_label, self.duty)
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
        self.status.setWordWrap(True)
        lay.addWidget(self.status)
        lay.addStretch()
        self._show_function("PULS")

    def _show_function(self, func):
        """Muestra solo los parámetros que tiene la forma de onda."""
        self._function = func
        wanted = PARAMS.get(func, PARAMS["PULS"])
        for name, (sp, _k) in self.spins.items():
            visible = name in wanted
            sp.setVisible(visible)
            self._labels[name].setVisible(visible)
        self.duty.setVisible("duty" in wanted)
        self.duty_label.setVisible("duty" in wanted)
        self.func.blockSignals(True)
        self.func.setCurrentIndex(max(0, self.func.findData(func)))
        self.func.blockSignals(False)

    def _function_picked(self, _index):
        # Cambio pendiente de aplicar: se enseñan los parámetros de la nueva
        # forma de onda y no se pisa lo escrito con la lectura periódica.
        self._show_function(self.func.currentData())
        self.edit = True

    # --- backend
    def _submit(self, fn, tag):
        if self._busy:
            return False
        self._busy, self._tag = True, tag
        self._request.emit(fn)
        return True

    def _ch_changed(self, v):
        self.channel, self.edit = v, False
        self.channelChanged.emit(v)
        self.refresh(force=True)

    def refresh(self, force=False):
        if self.edit and not force:
            return   # no pisar lo que el usuario está escribiendo
        ch = self.channel
        self._submit(lambda: self.backend.get_state(ch), "state")

    def apply(self):
        ch, func = self.channel, self.func.currentData()
        wanted = PARAMS.get(func, PARAMS["PULS"])
        vals = {n: sp.value() * k for n, (sp, k) in self.spins.items()
                if n in wanted}
        # La forma de onda se manda siempre: tras reiniciar el equipo puede
        # decir PULS y emitir un nivel continuo hasta que se reescribe.
        vals["function"] = func

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
        self.last_state = s
        self.idn.setText(s["idn"])
        self.out.setChecked(s["output"])
        self.out.setText("Salida: ON" if s["output"] else "Salida: OFF")
        if not self.edit or tag != "state":
            self._show_function(s.get("function") or "PULS")
            for n, (sp, k) in self.spins.items():
                if s.get(n) is not None:
                    sp.blockSignals(True)
                    sp.setValue(s[n] / k)
                    sp.blockSignals(False)
            self.edit = False
        self.duty.setText("—" if s.get("duty") is None
                          else f"{s['duty']:.2f} %")
        if tag == "apply":
            self.status.setText("Aplicado")
        elif tag != "state":
            self.status.setText("")
        self.stateRead.emit(s)

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
