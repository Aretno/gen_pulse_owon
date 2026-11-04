"""Transporte USB bulk (SCPI en texto) para generadores OWON DGE."""
from __future__ import annotations

import threading

VID, PID = 0x5345, 0x1234
EP_OUT, EP_IN = 0x01, 0x81


class DeviceError(RuntimeError):
    pass


class UsbTransport:
    def __init__(self, serial: str | None = None, timeout_ms: int = 500):
        self.serial = serial
        self.timeout_ms = timeout_ms
        self._dev = None
        self._lock = threading.RLock()

    def open(self):
        import usb.core
        import usb.util
        with self._lock:
            if self._dev is not None:
                return
            for d in usb.core.find(find_all=True, idVendor=VID, idProduct=PID):
                try:
                    sn = usb.util.get_string(d, d.iSerialNumber)
                except Exception:
                    sn = None
                if self.serial in (None, sn):
                    self._dev = d
                    break
            if self._dev is None:
                raise DeviceError("Generador OWON no encontrado (¿USB conectado?)")
            try:
                if self._dev.is_kernel_driver_active(0):
                    self._dev.detach_kernel_driver(0)
            except Exception:
                pass
            try:
                self._dev.set_configuration()
            except Exception as e:
                self._dev = None
                raise DeviceError(f"No se pudo abrir el USB: {e} "
                                  "(¿regla udev 99-owon-dge.rules instalada?)")

    def close(self):
        with self._lock:
            if self._dev is not None:
                import usb.util
                usb.util.dispose_resources(self._dev)
                self._dev = None

    def _drain(self):
        import usb.core
        try:
            while True:
                self._dev.read(EP_IN, 512, 20)
        except usb.core.USBError:
            pass

    def write(self, cmd: str):
        with self._lock:
            self.open()
            self._dev.write(EP_OUT, cmd.strip().encode() + b"\n", self.timeout_ms)

    def query(self, cmd: str, retries: int = 2) -> str:
        try:
            return self._query(cmd)
        except DeviceError:
            if retries <= 0:
                raise
            return self.query(cmd, retries - 1)

    def _query(self, cmd: str) -> str:
        import usb.core
        with self._lock:
            self.open()
            self._drain()
            self._dev.write(EP_OUT, cmd.strip().encode() + b"\n", self.timeout_ms)
            try:
                data = bytes(self._dev.read(EP_IN, 512, self.timeout_ms))
            except usb.core.USBTimeoutError:
                raise DeviceError(f"Sin respuesta a {cmd!r} (comando no soportado)")
            except usb.core.USBError as e:
                self.close()
                raise DeviceError(f"Error USB: {e}")
            return data.decode(errors="replace").strip()
