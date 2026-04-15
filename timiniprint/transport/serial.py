from __future__ import annotations

import asyncio
import time

SERIAL_BAUD_RATE = 115200


class SerialTransport:
    def __init__(self, port: str, baud_rate: int = SERIAL_BAUD_RATE) -> None:
        self._port = port
        self._baud_rate = baud_rate

    async def write(
        self,
        data: bytes,
        chunk_size: int,
        delay_ms: int,
        runtime_context=None,
    ) -> None:
        _ = runtime_context
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._write_blocking, data, chunk_size, delay_ms)

    def _write_blocking(self, data: bytes, chunk_size: int, delay_ms: int) -> None:
        try:
            import serial
        except Exception as exc:  # pragma: no cover
            raise RuntimeError("pyserial is required. Install with: pip install -r requirements.txt") from exc
        delay = max(0.0, delay_ms / 1000.0)
        try:
            with serial.Serial(self._port, self._baud_rate, timeout=1, write_timeout=5) as ser:
                offset = 0
                while offset < len(data):
                    chunk = data[offset : offset + chunk_size]
                    ser.write(chunk)
                    offset += len(chunk)
                    if delay:
                        time.sleep(delay)
                ser.flush()
        except Exception as exc:
            raise RuntimeError(f"Serial connection failed: {exc}") from exc
