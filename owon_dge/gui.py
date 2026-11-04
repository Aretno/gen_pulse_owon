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

_TIME = (("ns", 1e-9), ("µs", 1e-6), ("ms", 1e-3), ("s", 1.0))
# campo -> (etiqueta, unidades (nombre, factor a SI), mín SI, máx SI, decimales)
FIELDS = {
    "freq": ("Frecuencia", (("Hz", 1.0), ("kHz", 1e3), ("MHz", 1e6)),
             1e-3, 70e6, 6),
    "width": ("Anchura", _TIME, 0.0, 1e3, 6),
    "high": ("Nivel alto", (("mV", 1e-3), ("V", 1.0)), -10.0, 10.0, 4),
    "low": ("Nivel bajo", (("mV", 1e-3), ("V", 1.0)), -10.0, 10.0, 4),
    "lead": ("Flanco subida", _TIME, 0.0, 1e3, 6),
    "trail": ("Flanco bajada", _TIME, 0.0, 1e3, 6),
    "symmetry": ("Simetría", (("%", 1.0),), 0.0, 100.0, 1),
    "phase": ("Fase", (("°", 1.0),), 0.0, 360.0, 2),
}
FIELD_ORDER = ("freq", "width", "symmetry", "high", "low", "lead", "trail",
               "phase")


def best_unit(units, value_si: float) -> int:
    """Índice de la unidad más grande en la que el valor es ≥ 1 (o la menor)."""
    best = 0
    for index, (_name, factor) in enumerate(units):
        if abs(value_si) >= factor * (1 - 1e-12):
            best = index
    return best


class _Field:
    """Un parámetro editable: cuadro numérico + selector de unidades."""

    def __init__(self, name, panel):
        label, units, lo, hi, decimals = FIELDS[name]
        self.name, self.units, self.lo, self.hi = name, units, lo, hi
        self.pinned = False          # el usuario eligió la unidad: no cambiarla sola
        self.spin = QDoubleSpinBox(decimals=decimals)
        self.spin.setKeyboardTracking(False)
        # Paso proporcional al valor: se puede bajar sin saltar a 0.
        self.spin.setStepType(
            QDoubleSpinBox.StepType.AdaptiveDecimalStepType)
        self.combo = QComboBox()
        for unit_name, _factor in units:
            self.combo.addItem(unit_name)
        self.combo.setVisible(len(units) > 1)
        self.combo.setToolTip("Unidad en que se muestra y se escribe")
        self.widget = QWidget()
        row = QHBoxLayout(self.widget)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self.spin, 1)
        row.addWidget(self.combo)
        self._apply_range()
        self.spin.valueChanged.connect(lambda *_: setattr(panel, "edit", True))
        self.combo.activated.connect(self._unit_picked)

    @property
    def factor(self) -> float:
        return self.units[self.combo.currentIndex()][1]

    def _apply_range(self):
        k = self.factor
        self.spin.blockSignals(True)
        self.spin.setRange(self.lo / k, self.hi / k)
        self.spin.blockSignals(False)
        self._shown_factor = k       # unidad en la que está el valor mostrado

    def _unit_picked(self, _index):
        """Cambia de unidad conservando el valor (en SI)."""
        self.pinned = True
        value_si = self.spin.value() * self._shown_factor   # antes del cambio
        self._apply_range()
        self.set_si(value_si, keep_unit=True)

    def si(self) -> float:
        return self.spin.value() * self._shown_factor

    def set_si(self, value_si: float, *, keep_unit: bool = False):
        self.spin.blockSignals(True)
        if not self.pinned and not keep_unit:
            self.combo.setCurrentIndex(best_unit(self.units, value_si))
            self._apply_range()
        self.spin.setValue(value_si / self.factor)
        self.spin.blockSignals(False)

    def unchanged(self, current_si) -> bool:
        """¿El valor mostrado es el del equipo (a la resolución mostrada)?"""
        if current_si is None:
            return False
        tolerance = 0.5 * 10 ** -self.spin.decimals() * self.factor
        return abs(self.si() - current_si) <= max(tolerance, 1e-9 * abs(current_si))


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
            field = _Field(name, self)
            self.spins[name] = field
            self._labels[name] = QLabel(FIELDS[name][0])
            form.addRow(self._labels[name], field.widget)
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
        for name, field in self.spins.items():
            visible = name in wanted
            field.widget.setVisible(visible)
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
        state = self.last_state or {}
        vals = {}
        for n, field in self.spins.items():
            if n not in wanted:
                continue
            # Solo lo que ha cambiado (a la resolución mostrada): menos
            # escrituras y no se pelea con parámetros ligados (anchura/frecuencia).
            if state.get("function") == func and field.unchanged(state.get(n)):
                continue
            vals[n] = field.si()
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
            for n, field in self.spins.items():
                if s.get(n) is not None:
                    field.set_si(s[n])
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
