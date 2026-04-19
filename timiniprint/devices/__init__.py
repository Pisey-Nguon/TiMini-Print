<<<<<<< HEAD
from .models import PrinterModel, PrinterModelRegistry
from .resolve import DeviceResolver

__all__ = ["DeviceResolver", "PrinterModel", "PrinterModelRegistry"]
=======
from .device import BluetoothTarget, PrinterDevice, SerialTarget, TransportTarget
from .catalog import PrinterCatalog
from .profiles import PrinterProfile

__all__ = [
    "BluetoothTarget",
    "PrinterCatalog",
    "PrinterDevice",
    "PrinterProfile",
    "SerialTarget",
    "TransportTarget",
]
>>>>>>> 43c232936fb59e4ddab986334ca73b1fb5bab45f
