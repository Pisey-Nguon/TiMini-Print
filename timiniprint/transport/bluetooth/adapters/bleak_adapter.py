<<<<<<< HEAD
"""Bluetooth Low Energy adapter using bleak for BLE communication."""
=======
"""Bluetooth Low Energy adapter using bleak for BLE communication.

The adapter keeps connection lifecycle in `_BleakSocket` and delegates endpoint
binding plus family-aware write routing to `_BleakTransportSession`.
"""
>>>>>>> 43c232936fb59e4ddab986334ca73b1fb5bab45f
from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional, Tuple

from .base import _BleBluetoothAdapter
<<<<<<< HEAD
from ..constants import IS_MACOS
from ..types import DeviceInfo, DeviceTransport, SocketLike
=======
from .bleak_adapter_endpoint_resolver import _BleWriteEndpointResolver, _WriteSelection
from .bleak_adapter_transport import _BleakTransportSession
from ..constants import IS_MACOS
from ..types import DeviceInfo, DeviceTransport, SocketLike
from .... import reporting
from ....protocol.families import get_protocol_behavior
from ....protocol.family import ProtocolFamily
>>>>>>> 43c232936fb59e4ddab986334ca73b1fb5bab45f


def _missing_bleak_error() -> RuntimeError:
    return RuntimeError(
        "bleak is required for BLE Bluetooth support. Install it with: pip install bleak"
    )


class _BleakSocket:
<<<<<<< HEAD
    """Socket-like wrapper around a bleak BLE client for GATT write operations."""

    def __init__(self, pairing_hint: Optional[bool] = None) -> None:
        self._client: Any = None
        self._write_char: Any = None
        self._notify_char: Any = None
        self._address: Optional[str] = None
        self._connected = False
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._mtu_size = 100  # Safe chunk size for X6h buffer
        self._timeout = 30.0
        # BLE thermal printers need small delays between GATT writes
        self._write_delay_ms = 20  # ms between BLE GATT writes
        self._pairing_hint = pairing_hint is True and not IS_MACOS

    def settimeout(self, timeout: float) -> None:
        """Set socket timeout (stored for use in async operations)."""
        self._timeout = timeout

    def connect(self, address_channel: Tuple[str, int]) -> None:
        """Connect to a BLE device.
        
        Args:
            address_channel: Tuple of (address, channel). Channel is ignored for BLE.
                           Address can be MAC address or macOS UUID.
        """
        address, _ = address_channel
        self._address = address

        try:
=======
    """Socket-like wrapper around a bleak BLE client.

    It owns connection setup/teardown and uses `_BleakTransportSession` for the
    protocol-specific parts of write routing and notify handling.
    """

    def __init__(
        self,
        pairing_hint: Optional[bool] = None,
        protocol_family: Optional[ProtocolFamily] = None,
        reporter: reporting.Reporter = reporting.DUMMY_REPORTER,
        device_cache: Optional[Dict[str, Any]] = None,
    ) -> None:
        self._client: Any = None
        self._address: Optional[str] = None
        self._connected = False
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._mtu_size = 180
        self._timeout = 30.0
        self._pairing_hint = pairing_hint is True and not IS_MACOS
        self._protocol_family = protocol_family
        self._reporter = reporter
        self._device_cache = device_cache if device_cache is not None else {}
        self._write_resolver = _BleWriteEndpointResolver(reporter=self._reporter)
        self._transport = _BleakTransportSession(
            protocol_family=self._protocol_family_or_default(),
            transport_profile=get_protocol_behavior(self._protocol_family_or_default()).transport,
            write_resolver=self._write_resolver,
            reporter=self._reporter,
        )

    def settimeout(self, timeout: float) -> None:
        """Store the timeout used by async BLE operations."""
        self._timeout = timeout

    @property
    def _flow_can_write(self) -> bool:
        return self._transport.flow_can_write

    @_flow_can_write.setter
    def _flow_can_write(self, value: bool) -> None:
        self._transport.flow_can_write = value

    @property
    def _notify_started(self) -> bool:
        return self._transport.notify_started

    @_notify_started.setter
    def _notify_started(self, value: bool) -> None:
        self._transport.notify_started = value

    def connect(self, address_channel: Tuple[str, int]) -> None:
        """Connect to the BLE device and prepare family-specific endpoints."""
        address, _ = address_channel
        self._address = address
        previous_loop = None

        try:
            try:
                previous_loop = asyncio.get_event_loop()
            except RuntimeError:
                previous_loop = None
>>>>>>> 43c232936fb59e4ddab986334ca73b1fb5bab45f
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            self._loop.run_until_complete(self._connect_async(address))
        except Exception:
<<<<<<< HEAD
            self._cleanup_loop()
            raise

    async def _connect_async(self, address: str) -> None:
        """Async connection to BLE device."""
        try:
            from bleak import BleakClient, BleakScanner
        except ImportError as exc:
            raise _missing_bleak_error() from exc

        # On macOS, we might have a UUID instead of MAC address
        # Try to find the device first
        device = None
        
        # Check if address looks like a UUID (macOS style) or MAC address
        is_uuid = len(address) == 36 and address.count("-") == 4
        
        if is_uuid:
            # Direct connection with UUID
            self._client = BleakClient(address)
        else:
            # Try to find device by MAC address through scanning
            devices = await BleakScanner.discover(timeout=5.0)
            for dev in devices:
                # On macOS, dev.address is a UUID, but we can check metadata
                if hasattr(dev, "details") and dev.details:
                    # Try to match by name or address in metadata
                    pass
                # Also check if the name matches (some devices include MAC in name)
                if dev.address.upper() == address.upper():
                    device = dev
                    break
                if dev.name and address.upper() in dev.name.upper():
                    device = dev
                    break
            
            if device:
                self._client = BleakClient(device)
            else:
                # Try connecting directly with the address anyway
                self._client = BleakClient(address)
=======
            self._disconnect_after_failed_connect()
            self._cleanup_loop()
            raise
        finally:
            try:
                asyncio.set_event_loop(previous_loop)
            except Exception:
                pass

    async def _connect_async(self, address: str) -> None:
        """Create the client, connect it and bind writable characteristics."""
        try:
            from bleak import BleakClient
        except ImportError as exc:
            raise _missing_bleak_error() from exc

        self._client = BleakClient(await self._resolve_client_target(address))
>>>>>>> 43c232936fb59e4ddab986334ca73b1fb5bab45f

        try:
            await self._client.connect()
            self._connected = True
        except Exception as exc:
<<<<<<< HEAD
            raise RuntimeError(f"Failed to connect to BLE device {address}: {exc}") from exc

        # Update MTU size if available (ATT MTU minus 3 bytes header overhead)
        if hasattr(self._client, "mtu_size") and self._client.mtu_size:
            negotiated_mtu = self._client.mtu_size - 3
            # Use the negotiated MTU but cap at a reasonable size for thermal printers
            self._mtu_size = min(negotiated_mtu, 512)

        # Pair on platforms that support it if hinted.
        if self._pairing_hint:
            await self._pair_if_supported()

        # Discover services and find write + notify characteristics
        self._write_char, self._notify_char = await self._find_printer_characteristics()
        if not self._write_char:
=======
            detail = str(exc).strip() or repr(exc) or exc.__class__.__name__
            raise RuntimeError(f"Failed to connect to BLE device {address}: {detail}") from exc

        if hasattr(self._client, "mtu_size") and self._client.mtu_size:
            negotiated_mtu = self._client.mtu_size - 3
            self._mtu_size = min(negotiated_mtu, 512)

        if self._pairing_hint:
            await self._pair_if_supported()

        selection = await self._find_write_characteristic()
        if not selection:
>>>>>>> 43c232936fb59e4ddab986334ca73b1fb5bab45f
            await self._client.disconnect()
            self._connected = False
            raise RuntimeError(
                f"Could not find a writable GATT characteristic on device {address}. "
                "The device may not support BLE printing, or uses unknown UUIDs."
            )

<<<<<<< HEAD
        # Subscribe to the notify characteristic — many printers require this
        # before they will act on incoming print data.
        if self._notify_char:
            try:
                await self._client.start_notify(self._notify_char, lambda _s, _d: None)
            except Exception:
                pass  # Non-fatal; printer may still work without it

    async def _find_printer_characteristics(self) -> "Tuple[Optional[Any], Optional[Any]]":
        """Find the write and notify characteristics for a thermal printer.

        Thermal printers use write-without-response for bulk data (the print
        channel) and a paired notify characteristic for status/flow control.
        Write+read characteristics are config registers — not the data channel.
        Returns (write_char, notify_char).
        """
        if not self._client or not self._connected:
            return None, None

        services = self._client.services

        # Pass 1: find a service that has BOTH a write-without-response char AND
        # a notify char — this is the canonical SPP-over-BLE pattern used by
        # virtually all Chinese thermal printers (service ae30, ae3a, ff00, …).
        for service in services:
            chars = list(service.characteristics)
            write_char = None
            notify_char = None
            for char in chars:
                props = char.properties
                if "write-without-response" in props and write_char is None:
                    write_char = char
                if "notify" in props and notify_char is None:
                    notify_char = char
            if write_char and notify_char:
                return write_char, notify_char

        # Pass 2: any write-without-response char (no paired notify required).
        for service in services:
            for char in service.characteristics:
                if "write-without-response" in char.properties:
                    return char, None

        # Pass 3: fall back to write-with-response (config chars — last resort).
        for service in services:
            for char in service.characteristics:
                if "write" in char.properties:
                    return char, None

        return None, None

    def send(self, data: bytes) -> int:
        """Send data to the BLE device."""
=======
        self._transport.apply_write_selection(selection)
        self._transport.configure_endpoints(getattr(self._client, "services", None) or [])
        await self._transport.start_notify_if_available(self._client, self._handle_notification)
        await self._transport.initialize_connection(
            self._client,
            mtu_size=self._mtu_size,
            timeout=self._timeout,
        )

    async def _resolve_client_target(self, address: str) -> Any:
        """Return the address or discovered device object passed to BleakClient."""
        # On macOS, reuse of a cached BLEDevice from a previous scan loop can
        # fail with "Future attached to a different loop". Pass the CoreBluetooth
        # address string directly instead.
        if IS_MACOS:
            return address
        cached = self._device_cache.get(address.upper())
        if cached is not None:
            return cached

        try:
            from bleak import BleakScanner
        except ImportError as exc:
            raise _missing_bleak_error() from exc

        devices = await BleakScanner.discover(timeout=5.0)
        for dev in devices:
            if dev.address.upper() == address.upper():
                return dev
            if dev.name and address.upper() in dev.name.upper():
                return dev
        return address

    async def _find_write_characteristic(self) -> Optional[_WriteSelection]:
        """Resolve the primary writable characteristic for this connection."""
        if not self._client or not self._connected:
            return None
        return self._write_resolver.resolve(self._client.services)

    def send(self, data: bytes) -> int:
        return self.send_payload(data)

    def send_payload(self, data: bytes, runtime_controller=None) -> int:
        """Send one payload using the active BLE transport session."""
>>>>>>> 43c232936fb59e4ddab986334ca73b1fb5bab45f
        if not self._connected or not self._client:
            raise RuntimeError("Not connected to BLE device")
        if not self._loop:
            raise RuntimeError("Event loop not initialized")

        try:
<<<<<<< HEAD
            self._loop.run_until_complete(self._send_async(data))
            return len(data)
        except Exception as exc:
            raise RuntimeError(f"BLE write failed: {exc}") from exc

    def sendall(self, data: bytes) -> None:
        """Send all data to the BLE device."""
        self.send(data)

    async def _send_async(self, data: bytes) -> None:
        """Async send data via GATT write."""
        if not self._write_char:
            raise RuntimeError("No write characteristic available")

        props = self._write_char.properties
        if "write-without-response" in props:
            response = False  # thermal printer data channel — no ACK needed
        elif "write" in props:
            response = True
        else:
            raise RuntimeError("Characteristic does not support writing")
        
        # Use safe chunk size to avoid overflowing the printer's input buffer.
        chunk_size = max(20, min(self._mtu_size, 100))

        delay_seconds = self._write_delay_ms / 1000.0

        for i in range(0, len(data), chunk_size):
            chunk = data[i:i + chunk_size]
            await self._client.write_gatt_char(self._write_char, chunk, response=response)
            if delay_seconds > 0:
                await asyncio.sleep(delay_seconds)

    async def _pair_if_supported(self) -> None:
=======
            self._loop.run_until_complete(self._send_async(data, runtime_controller=runtime_controller))
            return len(data)
        except Exception as exc:
            bindings = self._transport.bindings
            detail = (
                f"service={bindings.write_service_uuid} "
                f"char={bindings.write_char_uuid}"
            )
            raise RuntimeError(f"BLE write failed ({detail}): {exc}") from exc

    def sendall(self, data: bytes) -> None:
        """Compatibility alias matching socket-style APIs."""
        self.send_payload(data)

    async def _send_async(self, data: bytes, runtime_controller=None) -> None:
        """Delegate payload routing and chunking to the transport session."""
        await self._transport.send(
            self._client,
            data,
            mtu_size=self._mtu_size,
            timeout=self._timeout,
            runtime_controller=runtime_controller,
        )

    async def _pair_if_supported(self) -> None:
        """Run platform pairing when the bleak client exposes it."""
>>>>>>> 43c232936fb59e4ddab986334ca73b1fb5bab45f
        pair = getattr(self._client, "pair", None)
        if not callable(pair):
            return
        try:
            result = await pair()
        except Exception as exc:
            raise RuntimeError(f"BLE pairing failed: {exc}") from exc
        if result is False:
            raise RuntimeError("BLE pairing failed")

    def close(self) -> None:
<<<<<<< HEAD
        """Close the BLE connection."""
        if self._loop and self._client and self._connected:
            try:
                self._loop.run_until_complete(self._client.disconnect())
            except Exception:
                pass
        self._connected = False
        self._cleanup_loop()

    def _cleanup_loop(self) -> None:
        """Clean up the event loop."""
=======
        """Close the BLE connection and release the private event loop."""
        self._disconnect_after_failed_connect()
        self._cleanup_loop()

    def _disconnect_after_failed_connect(self) -> None:
        """Best-effort disconnect path shared by connect failures and close()."""
        if self._loop and self._client:
            try:
                self._loop.run_until_complete(self._safe_disconnect_async())
            except Exception:
                pass
        self._connected = False
        self._client = None
        self._transport = _BleakTransportSession(
            protocol_family=self._protocol_family_or_default(),
            transport_profile=get_protocol_behavior(self._protocol_family_or_default()).transport,
            write_resolver=self._write_resolver,
            reporter=self._reporter,
        )

    async def _safe_disconnect_async(self) -> None:
        """Stop notifications before disconnecting the bleak client."""
        if not self._client:
            return
        await self._transport.stop_notify_if_started(self._client)
        disconnect = getattr(self._client, "disconnect", None)
        if not callable(disconnect):
            return
        try:
            await disconnect()
        except Exception:
            pass

    def _cleanup_loop(self) -> None:
        """Dispose the temporary event loop used by the socket wrapper."""
>>>>>>> 43c232936fb59e4ddab986334ca73b1fb5bab45f
        if self._loop:
            try:
                self._loop.close()
            except Exception:
                pass
            self._loop = None

<<<<<<< HEAD
=======
    def _protocol_family_or_default(self) -> ProtocolFamily:
        return ProtocolFamily.from_value(self._protocol_family)

    def _handle_notification(self, _sender: Any, data: Any) -> None:
        self._transport.handle_notification(bytes(data))

    @classmethod
    def _find_notify_characteristic(cls, services):
        return _BleakTransportSession.find_notify_characteristic(services)

>>>>>>> 43c232936fb59e4ddab986334ca73b1fb5bab45f

class _BleakBleAdapter(_BleBluetoothAdapter):
    """Bluetooth Low Energy adapter using bleak for GATT writes."""

    def __init__(self) -> None:
<<<<<<< HEAD
        self._device_cache: Dict[str, DeviceInfo] = {}

    def scan_blocking(self, timeout: float) -> List[DeviceInfo]:
        """Scan for BLE devices."""
=======
        self._device_cache: Dict[str, Any] = {}

    def scan_blocking(self, timeout: float) -> List[DeviceInfo]:
>>>>>>> 43c232936fb59e4ddab986334ca73b1fb5bab45f
        try:
            from bleak import BleakScanner
        except ImportError as exc:
            raise _missing_bleak_error() from exc

        async def scan() -> List[DeviceInfo]:
            devices = await BleakScanner.discover(timeout=timeout)
            results = []
            for device in devices:
                name = device.name or ""
<<<<<<< HEAD
=======
                self._device_cache[device.address.upper()] = device
>>>>>>> 43c232936fb59e4ddab986334ca73b1fb5bab45f
                results.append(
                    DeviceInfo(
                        name=name,
                        address=device.address,
                        paired=None,
                        transport=DeviceTransport.BLE,
                    )
                )
            return results

        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                devices = loop.run_until_complete(scan())
            finally:
                loop.close()
        except Exception as exc:
            raise RuntimeError(f"BLE scan failed: {exc}") from exc

<<<<<<< HEAD
        # Cache devices for later connection
        for device in devices:
            self._device_cache[device.address] = device

        return devices

    def create_socket(self, pairing_hint: Optional[bool] = None) -> SocketLike:
        """Create a BLE socket-like object for communication."""
        return _BleakSocket(pairing_hint=pairing_hint)

    def ensure_paired(self, address: str, pairing_hint: Optional[bool] = None) -> None:
        # BLE pairing is handled during connect if requested and supported.
=======
        return devices

    def create_socket(
        self,
        pairing_hint: Optional[bool] = None,
        protocol_family: Optional[ProtocolFamily] = None,
        reporter: reporting.Reporter = reporting.DUMMY_REPORTER,
    ) -> SocketLike:
        return _BleakSocket(
            pairing_hint=pairing_hint,
            protocol_family=protocol_family,
            reporter=reporter,
            device_cache=self._device_cache,
        )

    def ensure_paired(self, address: str, pairing_hint: Optional[bool] = None) -> None:
>>>>>>> 43c232936fb59e4ddab986334ca73b1fb5bab45f
        return None
