from __future__ import annotations

import asyncio
import time

<<<<<<< HEAD
SERIAL_BAUD_RATE = 115200


class SerialTransport:
    def __init__(self, port: str, baud_rate: int = SERIAL_BAUD_RATE) -> None:
        self._port = port
        self._baud_rate = baud_rate

    async def write(self, data: bytes, chunk_size: int, interval_ms: int) -> None:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._write_blocking, data, chunk_size, interval_ms)

    def _write_blocking(self, data: bytes, chunk_size: int, interval_ms: int) -> None:
=======
from ..devices import PrinterDevice, SerialTarget
from ..protocol import ProtocolJob

SERIAL_BAUD_RATE = 115200


class SerialConnection:
    """Serial connection that writes jobs using the device's stream tuning."""

    def __init__(self, device: PrinterDevice) -> None:
        target = device.transport_target
        if not isinstance(target, SerialTarget):
            raise RuntimeError("SerialConnector requires a PrinterDevice with SerialTarget")
        self._device = device
        self._target = target

    async def send(self, job: ProtocolJob) -> None:
        """Send a protocol job over serial in blocking chunks via an executor."""
        _ = job.runtime_controller
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None,
            self._write_blocking,
            job.payload,
            self._device.profile.stream.chunk_size,
            self._device.profile.stream.delay_ms,
        )

    async def disconnect(self) -> None:
        """Serial writes are short-lived, so disconnect is a no-op."""
        return None

    def _write_blocking(self, data: bytes, chunk_size: int, delay_ms: int) -> None:
>>>>>>> 43c232936fb59e4ddab986334ca73b1fb5bab45f
        try:
            import serial
        except Exception as exc:  # pragma: no cover
            raise RuntimeError("pyserial is required. Install with: pip install -r requirements.txt") from exc
<<<<<<< HEAD
        interval = max(0.0, interval_ms / 1000.0)
        try:
            with serial.Serial(self._port, self._baud_rate, timeout=1, write_timeout=5) as ser:
=======
        delay = max(0.0, delay_ms / 1000.0)
        try:
            with serial.Serial(self._target.path, self._target.baud_rate, timeout=1, write_timeout=5) as ser:
>>>>>>> 43c232936fb59e4ddab986334ca73b1fb5bab45f
                offset = 0
                while offset < len(data):
                    chunk = data[offset : offset + chunk_size]
                    ser.write(chunk)
                    offset += len(chunk)
<<<<<<< HEAD
                    if interval:
                        time.sleep(interval)
                ser.flush()
        except Exception as exc:
            raise RuntimeError(f"Serial connection failed: {exc}") from exc
=======
                    if delay:
                        time.sleep(delay)
                ser.flush()
        except Exception as exc:
            raise RuntimeError(f"Serial connection failed: {exc}") from exc


class SerialConnector:
    """Create serial connections for devices with ``SerialTarget``."""

    async def connect(self, device: PrinterDevice) -> SerialConnection:
        """Return a serial connection bound to the given device."""
        return SerialConnection(device)
>>>>>>> 43c232936fb59e4ddab986334ca73b1fb5bab45f
