"""Dge2070 con un transporte simulado (nunca el equipo real)."""
import math
import re
import unittest

from owon_dge import Dge2070
from owon_dge.device import canonical_function


class FakeTransport:
    settle_s = 0.0

    def __init__(self):
        self.ch = {n: {"FUNC:SHAP": "PUL", "FREQ": 1e6, "VOLT:HIGH": 1.0,
                       "VOLT:LOW": 0.0, "PULS:WIDT": 1e-7, "PULS:DCYC": 10.0,
                       "PULS:TRAN:LEAD": 2e-8, "PULS:TRAN:TRA": 2e-8,
                       "FUNC:RAMP:SYMM": 50.0, "PHAS:ADJ": 0.0, "OUTP": "0"}
                   for n in (1, 2)}
        self.log, self.queried = [], []

    def close(self): pass

    def write(self, cmd):
        self.log.append(cmd)
        m = re.match(r"SOUR(\d):(\S+) (.+)", cmd)
        if m:
            ch, key, val = int(m.group(1)), m.group(2), m.group(3)
            key = {"FUNC:SHAP": "FUNC:SHAP"}.get(key, key)
            if key == "FUNC:SHAP":
                self.ch[ch][key] = {"PULS": "PUL"}.get(val, val)
            elif key == "PHAS:ADJ":
                self.ch[ch][key] = math.radians(float(val.rstrip("DEG")))
            elif val.endswith("ns"):
                self.ch[ch][key] = float(val[:-2]) * 1e-9
            else:
                self.ch[ch][key] = float(val)
        m = re.match(r"OUTP(\d) (ON|OFF)", cmd)
        if m:
            self.ch[int(m.group(1))]["OUTP"] = "1" if m.group(2) == "ON" else "0"

    def query(self, cmd):
        self.queried.append(cmd)
        if cmd == "*IDN?":
            return "FAKE"
        m = re.match(r"(?:SOUR|OUTP)(\d)(?::(\S+))?\?", cmd)
        ch, key = int(m.group(1)), m.group(2)
        if cmd.startswith("OUTP"):
            return "9.9E+37" if key == "IMP" else self.ch[ch]["OUTP"]
        return str(self.ch[ch][key])


class DeviceTests(unittest.TestCase):
    def setUp(self):
        self.t = FakeTransport()
        self.dev = Dge2070(self.t)

    def test_canonical_function(self):
        for raw, want in (("PUL", "PULS"), ("pulse", "PULS"), ("SIN", "SIN"),
                          ("RAMP", "RAMP"), ("PRN", "PRN"), ("", "")):
            self.assertEqual(canonical_function(raw), want)

    def test_state_only_queries_the_parameters_of_the_function(self):
        self.t.ch[1]["FUNC:SHAP"] = "SIN"
        st = self.dev.get_state(1)
        self.assertEqual(st["function"], "SIN")
        self.assertIsNone(st["width"])
        self.assertIsNone(st["duty"])
        self.assertNotIn("SOUR1:PULS:WIDT?", self.t.queried)
        self.assertEqual(st["freq"], 1e6)

    def test_pulse_state_has_width_and_duty(self):
        st = self.dev.get_state(2)
        self.assertEqual(st["function"], "PULS")
        self.assertAlmostEqual(st["width"], 1e-7)
        self.assertEqual(st["duty"], 10.0)

    def test_set_function_then_parameters(self):
        self.dev.set_pulse(1, function="RAMP", symmetry=25.0, phase=90.0)
        st = self.dev.get_state(1)
        self.assertEqual(st["function"], "RAMP")
        self.assertEqual(st["symmetry"], 25.0)
        self.assertAlmostEqual(st["phase"], 90.0, places=3)

    def test_parameter_not_in_function_is_rejected_without_writing(self):
        self.t.ch[1]["FUNC:SHAP"] = "SIN"
        with self.assertRaises(ValueError):
            self.dev.set_pulse(1, width=1e-7)
        self.assertEqual(self.t.log, [])

    def test_unknown_function(self):
        with self.assertRaises(ValueError):
            self.dev.set_pulse(1, function="TRIANGLE")

    def test_align_phase_emulated_restores_outputs(self):
        self.t.ch[1]["OUTP"] = "1"
        self.t.ch[2]["PHAS:ADJ"] = math.radians(30)
        self.assertEqual(self.dev.align_phase(), "emulado")
        self.assertEqual(self.t.ch[1]["OUTP"], "1")
        self.assertEqual(self.t.ch[2]["OUTP"], "0")
        self.assertAlmostEqual(self.t.ch[2]["PHAS:ADJ"], 0.0)


if __name__ == "__main__":
    unittest.main()
