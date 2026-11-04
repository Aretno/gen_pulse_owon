"""Unidades elegibles del panel (sin equipo)."""
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PyQt6.QtWidgets import QApplication
    from owon_dge.gui import PulseGeneratorPanel, best_unit, FIELDS
    HAVE_QT = True
except ImportError:
    HAVE_QT = False

_APP = None


class FakeBackend:
    def __init__(self):
        self.calls = []
        self.state = {"channel": 1, "idn": "FAKE", "function": "PULS",
                      "freq": 1e6, "width": 9.46e-7, "duty": 94.6, "high": 1.87,
                      "low": 0.0, "lead": 1.5e-8, "trail": 1.5e-8,
                      "symmetry": None, "phase": 0.0, "output": True}

    def get_state(self, ch, full=False):
        return dict(self.state)

    def set_pulse(self, ch, **kw):
        self.calls.append(kw)
        for k, v in kw.items():
            if k in self.state:
                self.state[k] = v

    def set_output(self, ch, on):
        pass

    def close(self):
        pass


@unittest.skipUnless(HAVE_QT, "PyQt6 no instalado")
class UnitsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        global _APP
        _APP = QApplication.instance() or QApplication([])
        cls.app = _APP

    def make(self):
        backend = FakeBackend()
        panel = PulseGeneratorPanel(backend, poll_ms=10_000_000)
        self.addCleanup(panel.close)
        self.wait(panel)
        return panel, backend

    def wait(self, panel):
        import time
        end = time.monotonic() + 3
        while panel._busy and time.monotonic() < end:
            self.app.processEvents()
            time.sleep(0.005)
        self.app.processEvents()

    def test_best_unit(self):
        units = FIELDS["freq"][1]
        self.assertEqual(units[best_unit(units, 1e6)][0], "MHz")
        self.assertEqual(units[best_unit(units, 1e5)][0], "kHz")
        self.assertEqual(units[best_unit(units, 50.0)][0], "Hz")
        times = FIELDS["width"][1]
        self.assertEqual(times[best_unit(times, 9.46e-7)][0], "ns")
        self.assertEqual(times[best_unit(times, 2e-6)][0], "µs")
        self.assertEqual(times[best_unit(times, 0.0)][0], "ns")

    def test_units_follow_the_value(self):
        panel, _b = self.make()
        f = panel.spins["freq"]
        self.assertEqual(f.combo.currentText(), "MHz")
        self.assertAlmostEqual(f.spin.value(), 1.0)
        self.assertEqual(panel.spins["width"].combo.currentText(), "ns")
        self.assertAlmostEqual(panel.spins["width"].spin.value(), 946.0)
        self.assertEqual(panel.spins["high"].combo.currentText(), "V")

    def test_changing_unit_keeps_the_value(self):
        panel, _b = self.make()
        f = panel.spins["freq"]
        f.combo.setCurrentIndex(1)                      # kHz
        f.combo.activated.emit(1)
        self.assertAlmostEqual(f.spin.value(), 1000.0)
        self.assertAlmostEqual(f.si(), 1e6)
        f.combo.setCurrentIndex(0)                      # Hz
        f.combo.activated.emit(0)
        self.assertAlmostEqual(f.spin.value(), 1e6)
        self.assertTrue(f.pinned)

    def test_can_lower_the_frequency_to_100_khz_in_khz(self):
        panel, backend = self.make()
        f = panel.spins["freq"]
        f.combo.setCurrentIndex(1)
        f.combo.activated.emit(1)
        f.spin.setValue(100.0)                          # 100 kHz
        panel.apply()
        self.wait(panel)
        self.assertEqual(backend.calls[-1]["freq"], 1e5)
        self.assertEqual(set(backend.calls[-1]), {"freq", "function"})  # solo lo cambiado
        # tras la lectura la unidad elegida se respeta aunque 100 kHz cabría en MHz
        self.assertEqual(f.combo.currentText(), "kHz")
        self.assertAlmostEqual(f.spin.value(), 100.0)

    def test_down_arrow_does_not_jump_to_zero(self):
        panel, _b = self.make()
        f = panel.spins["freq"]                         # 1 MHz
        f.spin.stepBy(-1)
        self.assertGreater(f.spin.value(), 0.5)
        self.assertLess(f.spin.value(), 1.0)

    def test_typing_in_mhz_still_works(self):
        panel, backend = self.make()
        f = panel.spins["freq"]
        f.spin.setValue(0.1)                            # 0,1 MHz
        panel.apply()
        self.wait(panel)
        self.assertAlmostEqual(backend.calls[-1]["freq"], 1e5)

    def test_unchanged_fields_are_not_sent(self):
        panel, backend = self.make()
        panel.apply()
        self.wait(panel)
        self.assertEqual(set(backend.calls[-1]), {"function"})

    def test_each_time_field_can_use_ns_us_ms_s(self):
        panel, backend = self.make()
        w = panel.spins["width"]
        self.assertEqual([w.combo.itemText(i) for i in range(w.combo.count())],
                         ["ns", "µs", "ms", "s"])
        w.combo.setCurrentIndex(1)
        w.combo.activated.emit(1)
        w.spin.setValue(0.2)                            # 0,2 µs
        panel.apply()
        self.wait(panel)
        self.assertAlmostEqual(backend.calls[-1]["width"], 2e-7)


if __name__ == "__main__":
    unittest.main()
