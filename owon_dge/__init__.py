"""Control del generador de pulsos OWON DGE2070 (USB, SCPI)."""
from .client import RemoteDge2070
from .device import FUNCTIONS, PARAMS, Dge2070
from .tmc import UsbTmcTransport
from .transport import DeviceError, UsbTransport

__all__ = ["FUNCTIONS", "PARAMS", "Dge2070", "RemoteDge2070", "UsbTransport", "UsbTmcTransport", "DeviceError"]
