"""Transporte USB TMC (modo "USB TMC" del generador, VID:PID 5345:1235).

Se habla TMC directamente con pyusb (desacoplando el controlador usbtmc del
kernel) porque ``/dev/usbtmc0`` sólo entrega los primeros bytes de los bloques
largos (p. ej. DATA:DATA? EMEMory, 16 KB). Misma interfaz que UsbTransport.
"""
from __future__ import annotations

import struct
import threading

from .transport import DeviceError

VID, PID = 0x5345, 0x1235
DEV_DEP_MSG_OUT, REQUEST_DEV_DEP_MSG_IN = 1, 2


class UsbTmcTransport:
    settle_s = 0.05   # pausa tras escribir (el modo TMC es ~6x más rápido)

    def __init__(self, serial: str | None = None, timeout_ms: int = 2000):
        self.serial = serial
        self.timeout_ms = timeout_ms
        self._dev = self._out = self._in = None
        self._tag = 0
        self._lock = threading.RLock()

    def open(self):
        import usb.core
        import usb.util
        with self._lock:
            if self._dev is not None:
                return
            dev = None
            for d in usb.core.find(find_all=True, idVendor=VID, idProduct=PID):
                try:
                    sn = usb.util.get_string(d, d.iSerialNumber)
                except Exception:
                    sn = None
                if self.serial in (None, sn):
                    dev = d
                    break
            if dev is None:
                raise DeviceError("Generador OWON (modo USB TMC) no encontrado")
            try:
                if dev.is_kernel_driver_active(0):
                    dev.detach_kernel_driver(0)
                dev.set_configuration()
                intf = dev.get_active_configuration()[(0, 0)]
                match = lambda d: (lambda e: usb.util.endpoint_direction(e.bEndpointAddress) == d)
                self._out = usb.util.find_descriptor(intf, custom_match=match(usb.util.ENDPOINT_OUT))
                self._in = usb.util.find_descriptor(intf, custom_match=match(usb.util.ENDPOINT_IN))
            except Exception as e:
                raise DeviceError(f"No se pudo abrir el USB TMC: {e} "
                                  "(¿regla udev 99-owon-dge.rules instalada?)")
            self._dev = dev

    def close(self):
        with self._lock:
            if self._dev is not None:
                import usb.util
                dev, self._dev = self._dev, None
                usb.util.dispose_resources(dev)
                try:
                    dev.attach_kernel_driver(0)   # devuelve /dev/usbtmc0 al sistema
                except Exception:
                    pass

    def _next_tag(self) -> int:
        self._tag = self._tag % 255 + 1
        return self._tag

    def _drain(self):
        import usb.core
        try:
            while True:
                self._in.read(512, 10)
        except usb.core.USBError:
            pass

    def write(self, cmd: str | bytes):
        data = cmd if isinstance(cmd, bytes) else cmd.strip().encode() + b"\n"
        with self._lock:
            self.open()
            tag = self._next_tag()
            hdr = struct.pack("<BBBBIBBBB", DEV_DEP_MSG_OUT, tag, 0xFF ^ tag, 0,
                              len(data), 1, 0, 0, 0)          # EOM
            self._out.write(hdr + data + b"\0" * (-len(data) % 4), self.timeout_ms)

    def query_bytes(self, cmd: str, retries: int = 2) -> bytes:
        import usb.core
        with self._lock:
            self.open()
            for attempt in range(retries + 1):
                try:
                    self._drain()
                    self.write(cmd)
                    tag = self._next_tag()
                    self._out.write(struct.pack("<BBBBIBBBB", REQUEST_DEV_DEP_MSG_IN, tag,
                                                0xFF ^ tag, 0, 1 << 20, 0, 0, 0, 0),
                                    self.timeout_ms)
                    buf = bytes(self._in.read(65536, self.timeout_ms))
                    size = struct.unpack("<I", buf[4:8])[0]
                    while len(buf) - 12 < size:
                        buf += bytes(self._in.read(65536, self.timeout_ms))
                    if size == 0 or not buf[12:12 + size].strip():
                        raise usb.core.USBError("respuesta vacía")   # ocurre ~0,5 %
                    return buf[12:12 + size]
                except usb.core.USBError as e:
                    if attempt == retries:
                        self.close()
                        raise DeviceError(f"Sin respuesta a {cmd!r}: {e}")

    def query(self, cmd: str) -> str:
        return self.query_bytes(cmd).decode(errors="replace").strip()
