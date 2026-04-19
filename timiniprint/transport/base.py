from __future__ import annotations

from typing import Protocol

from ..devices import PrinterDevice
from ..protocol import ProtocolJob


class PrinterConnection(Protocol):
    """Active transport connection able to send ``ProtocolJob`` objects."""

    async def send(self, job: ProtocolJob) -> None: ...

    async def disconnect(self) -> None: ...


class PrinterConnector(Protocol):
    """Transport factory that connects using a resolved ``PrinterDevice``."""

    async def connect(self, device: PrinterDevice) -> PrinterConnection: ...
