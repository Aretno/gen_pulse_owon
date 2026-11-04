"""API de alto nivel del generador de pulsos OWON DGE2070.

Todas las magnitudes en unidades SI (Hz, V, s). Los canales son 1 o 2.
"""
from __future__ import annotations

import math
import threading
import time

from .transport import DeviceError, UsbTransport, auto_transport

# campo -> (comando de lectura/escritura, tipo)
_FIELDS = {
    "freq":  ("FREQ", float),
    "high":  ("VOLT:HIGH", float),
    "low":   ("VOLT:LOW", float),
    "width": ("PULS:WIDT", float),
    "lead":  ("PULS:TRAN:LEAD", float),
    "trail": ("PULS:TRAN:TRA", float),
}
# Magnitudes que no son un simple "SOUR<n>:<cmd>" en SI: simetría de la rampa
# (%) y fase (el equipo la devuelve en rad; aquí se usan grados).
_FIELDS["symmetry"] = ("FUNC:RAMP:SYMM", float)
_FIELDS["phase"] = ("PHAS:ADJ", float)
SETTABLE = tuple(_FIELDS) + ("duty",)

# Formas de onda del equipo y los parámetros que tiene cada una.
FUNCTIONS = {"SIN": "Seno", "SQU": "Cuadrada", "PULS": "Pulso",
             "RAMP": "Rampa", "PRN": "Ruido"}
PARAMS = {
    "SIN": ("freq", "high", "low", "phase"),
    "SQU": ("freq", "high", "low", "phase"),
    "PULS": ("freq", "width", "duty", "high", "low", "lead", "trail", "phase"),
    "RAMP": ("freq", "symmetry", "high", "low", "phase"),
    "PRN": ("high", "low"),
}
_TOL_FLOOR = {"phase": 1.0, "symmetry": 0.05}   # la fase va en grados enteros


def canonical_function(raw) -> str:
    """'PUL', 'PULSe', 'pulse'… -> 'PULS' (clave de FUNCTIONS); '' si no se conoce."""
    head = str(raw or "").strip().upper()[:3]
    for key in FUNCTIONS:
        if key.startswith(head) and head:
            return key
    return ""


_TIME_FIELDS = {"width", "lead", "trail"}


def _fmt(name: str, value: float) -> str:
    """Los tiempos se envían con sufijo ns (p. ej. '15ns').

    En notación científica ('1.5e-08') el equipo rechaza en silencio los valores
    que están justo en el límite (flanco mínimo 15 ns, ciclo máximo, ...).
    """
    value = float(value)
    if name in _TIME_FIELDS:
        return f"{value * 1e9:.6f}".rstrip("0").rstrip(".") + "ns"
    if name == "phase":
        return f"{value:.6g}DEG"
    return f"{value:.9g}"


class Dge2070:
    def __init__(self, transport=None):
        self.t = transport or auto_transport()
        self._lock = threading.RLock()

    # --- bajo nivel
    def idn(self) -> str:
        return self.t.query("*IDN?")

    def raw(self, cmd: str, query: bool | None = None) -> str | None:
        query = cmd.strip().endswith("?") if query is None else query
        return self.t.query(cmd) if query else (self.t.write(cmd) or None)

    def read_waveform(self) -> list[int]:
        """Memoria de edición: 8192 puntos de 14 bit (0..16383). Sólo en modo TMC."""
        import struct
        if not hasattr(self.t, "query_bytes"):
            raise DeviceError("read_waveform requiere el modo USB TMC")
        d = self.t.query_bytes("DATA:DATA? EMEMory")
        n = int(d[2:2 + int(d[1:2])])
        off = 2 + int(d[1:2])
        return list(struct.unpack(f">{n // 2}H", d[off:off + n]))

    def errors(self) -> str:
        return self.t.query("SYST:ERR?")

    @staticmethod
    def _ch(ch: int) -> int:
        if ch not in (1, 2):
            raise ValueError("canal debe ser 1 o 2")
        return ch

    # --- lectura
    def get_state(self, ch: int = 1, full: bool = False) -> dict:
        ch = self._ch(ch)
        with self._lock:
            q = self.t.query
            if not hasattr(self, "_idn"):
                self._idn = self.idn()
            s = {"channel": ch, "idn": self._idn}
            s["function"] = canonical_function(q(f"SOUR{ch}:FUNC:SHAP?"))
            wanted = PARAMS.get(s["function"], PARAMS["PULS"])
            for name, (cmd, typ) in _FIELDS.items():
                s[name] = None
                if name not in wanted:      # las consultas no válidas dan timeout
                    continue
                try:
                    s[name] = typ(q(f"SOUR{ch}:{cmd}?"))
                except DeviceError:
                    s[name] = None
            if s["phase"] is not None:
                s["phase"] = math.degrees(s["phase"])
            s["duty"] = (float(q(f"SOUR{ch}:PULS:DCYC?"))
                         if "duty" in wanted else None)
            s["period"] = 1.0 / s["freq"] if s["freq"] else None
            s["output"] = q(f"OUTP{ch}?") in ("1", "ON")
            s["impedance"] = q(f"OUTP{ch}:IMP?")
            if full:
                s["burst"] = q(f"SOUR{ch}:BURS:STAT?") in ("1", "ON")
                s["burst_mode"] = q(f"SOUR{ch}:BURS:MODE?")
                s["burst_cycles"] = q(f"SOUR{ch}:BURS:NCYC?")
            return s

    # --- escritura
    def set_function(self, ch: int, function: str):
        """Cambia la forma de onda (clave de FUNCTIONS) y lo verifica."""
        ch = self._ch(ch)
        func = canonical_function(function)
        if not func or str(function).strip().upper()[:3] != func[:3]:
            raise ValueError(f"forma de onda desconocida: {function!r}")
        with self._lock:
            for _attempt in range(3):
                time.sleep(self.t.settle_s)
                self.t.write(f"SOUR{ch}:FUNC:SHAP {func}")
                time.sleep(self.t.settle_s)
                try:
                    got = canonical_function(self.t.query(f"SOUR{ch}:FUNC:SHAP?"))
                except DeviceError:
                    continue
                if got == func:
                    return
            raise DeviceError(f"function: pedido {func}, el equipo tiene {got!r}")

    def set_pulse(self, ch: int = 1, **kw):
        """Fija parámetros: function, freq/period, width o duty, high, low, lead,
        trail, symmetry (rampa) y phase (grados); sólo los de la forma de onda."""
        ch = self._ch(ch)
        if "period" in kw:
            p = kw.pop("period")
            if p is None or p <= 0:
                raise ValueError("period debe ser > 0")
            kw["freq"] = 1.0 / p
        function = kw.pop("function", None)
        bad = set(kw) - set(SETTABLE)
        if bad:
            raise ValueError(f"parámetros desconocidos: {sorted(bad)}")
        if "width" in kw and "duty" in kw:
            raise ValueError("usa width o duty, no ambos")
        with self._lock:
            if function:
                self.set_function(ch, function)
                func = canonical_function(function)
            else:
                func = canonical_function(self.t.query(f"SOUR{ch}:FUNC:SHAP?"))
            if kw and func:
                invalid = sorted(set(kw) - set(PARAMS[func]))
                if invalid:
                    raise ValueError(
                        f"{invalid} no existe(n) en la forma de onda {func}")
            # Notas del firmware (medidas en el equipo):
            #  - SYST:ERR? es pegajoso/no fiable -> se verifica releyendo.
            #  - Un comando justo tras *CLS se pierde -> no usar *CLS.
            #  - Tras escribir hay que dejar respirar al USB (SETTLE_S).
            for name in ["freq", "width", "duty", "symmetry", "phase", "high",
                         "low", "lead", "trail"]:
                if name not in kw:
                    continue
                cmd = "PULS:DCYC" if name == "duty" else _FIELDS[name][0]
                tol = _TOL_FLOOR.get(name, 1e-12)
                for attempt in range(3):   # el equipo a veces pierde una escritura
                    time.sleep(self.t.settle_s)
                    self.t.write(f"SOUR{ch}:{cmd} {_fmt(name, kw[name])}")
                    time.sleep(self.t.settle_s)
                    if name == "duty":
                        break
                    got = self._read_field(ch, name)
                    if got is not None and abs(got - kw[name]) <= max(
                            1e-3 * abs(kw[name]), tol):
                        break
                else:
                    if name == "width" and self._width_via_duty(ch, kw):
                        continue
                    raise DeviceError(
                        f"{name}: pedido {kw[name]:g}, el equipo tiene {got} "
                        "(fuera de rango o dependiente de otro parámetro)")

    def _width_via_duty(self, ch, kw) -> bool:
        """Anchuras justo en el límite sólo entran como ciclo de trabajo."""
        freq = kw.get("freq") or self._read_field(ch, "freq")
        if not freq:
            return False
        self.t.write(f"SOUR{ch}:PULS:DCYC {kw['width'] * freq * 100:.6g}")
        time.sleep(self.t.settle_s)
        got = self._read_field(ch, "width")
        return got is not None and abs(got - kw["width"]) <= 1e-3 * kw["width"]

    def _read_field(self, ch, name, tries=3):
        for _ in range(tries):
            try:
                value = _FIELDS[name][1](self.t.query(f"SOUR{ch}:{_FIELDS[name][0]}?"))
                return math.degrees(value) if name == "phase" else value
            except DeviceError:
                time.sleep(self.t.settle_s)
        return None

    # SCPI que alinea la fase de los dos canales (el «Align Phase» del panel
    # frontal), si el equipo lo admite; vacío = se emula.
    ALIGN_COMMANDS: tuple = ()

    def align_phase(self) -> str:
        """Sincroniza temporalmente CH1 y CH2; devuelve el método usado."""
        with self._lock:
            if self.ALIGN_COMMANDS:
                for cmd in self.ALIGN_COMMANDS:
                    self.t.write(cmd)
                    time.sleep(self.t.settle_s)
                return "scpi"
            # Emulación: fase 0 en los dos canales con las salidas apagadas y
            # reencendidas a la vez, de modo que arranquen juntos.
            states = [self.get_state(ch) for ch in (1, 2)]
            for st in states:
                self.t.write(f"OUTP{st['channel']} OFF")
            time.sleep(self.t.settle_s)
            for st in states:
                if st.get("phase") is not None:
                    self.set_pulse(st["channel"], phase=0.0)
            for st in states:
                self.t.write(f"OUTP{st['channel']} {'ON' if st['output'] else 'OFF'}")
            time.sleep(self.t.settle_s)
            return "emulado"

    def set_output(self, ch: int, on: bool):
        self.t.write(f"OUTP{self._ch(ch)} {'ON' if on else 'OFF'}")
        time.sleep(self.t.settle_s)

    def close(self):
        self.t.close()
