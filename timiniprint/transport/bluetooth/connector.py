from __future__ import annotations

from typing import Optional

from ... import reporting
from ...devices import BluetoothTarget, PrinterDevice
from ...devices.device import BluetoothEndpointTransport
from ...protocol import ProtocolJob
from .backend import SppBackend
from .types import DeviceInfo, DeviceTransport


class BleakBluetoothConnection:
    """Bluetooth connection backed by the repo's Bleak/Spp transport stack."""

    def __init__(
        self,
        backend: SppBackend,
        device: PrinterDevice,
    ) -> None:
        target = device.transport_target
        if not isinstance(target, BluetoothTarget):
            raise RuntimeError("BleakBluetoothConnector requires a PrinterDevice with BluetoothTarget")
        self._backend = backend
        self._device = device
        self._target = target

    async def send(self, job: ProtocolJob) -> None:
        """Send a protocol job using the device's stream tuning and runtime state."""
        await self._backend.write(
            job.payload,
            self._device.profile.stream.chunk_size,
            self._device.profile.stream.delay_ms,
            runtime_controller=job.runtime_controller,
        )

    async def disconnect(self) -> None:
        """Close the underlying Bluetooth backend connection."""
        await self._backend.disconnect()


class BleakBluetoothConnector:
    """Create Bluetooth connections for devices with ``BluetoothTarget``."""

    def __init__(self, reporter: reporting.Reporter = reporting.DUMMY_REPORTER) -> None:
        self._reporter = reporter

    async def connect(self, device: PrinterDevice) -> BleakBluetoothConnection:
        """Connect to a resolved Bluetooth device and return a live connection."""
        target = device.transport_target
        if not isinstance(target, BluetoothTarget):
            raise RuntimeError("BleakBluetoothConnector requires a PrinterDevice with BluetoothTarget")
        attempts = [
            self._to_device_info(endpoint, device)
            for endpoint in target.ordered_endpoints(prefer_spp=device.profile.use_spp)
        ]
        backend = SppBackend(reporter=self._reporter)
        await backend.connect_attempts(
            attempts,
            pairing_hint=target.paired is False,
        )
        return BleakBluetoothConnection(backend, device)

    @staticmethod
    def _to_device_info(endpoint, device: PrinterDevice) -> DeviceInfo:
        transport = (
            DeviceTransport.BLE
            if endpoint.transport is BluetoothEndpointTransport.BLE
            else DeviceTransport.CLASSIC
        )
        return DeviceInfo(
            name=endpoint.name,
            address=endpoint.address,
            paired=endpoint.paired,
            transport=transport,
            protocol_family=device.protocol_family,
        )
