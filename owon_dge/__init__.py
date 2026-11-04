"""Control del generador de pulsos OWON DGE2070 (USB, SCPI)."""
from .client import RemoteDge2070
from .device import Dge2070
from .transport import DeviceError, UsbTransport

__all__ = ["Dge2070", "RemoteDge2070", "UsbTransport", "DeviceError"]
