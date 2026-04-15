from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from tests.helpers import reset_registry_cache
from timiniprint.devices import DeviceResolver, PrinterCatalog
from timiniprint.devices.resolve import ResolvedBluetoothDevice
from timiniprint.transport.bluetooth.types import DeviceInfo, DeviceTransport


class DeviceResolverLogicalDeviceTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_registry_cache()
        self.catalog = PrinterCatalog.load()
        self.resolver = DeviceResolver(self.catalog)

    def test_unique_classic_and_ble_are_merged_into_one_logical_device(self) -> None:
        devices = [
            DeviceInfo(
                name="X6H-ABCD",
                address="AA:BB:CC:DD:EE:01",
                paired=True,
                transport=DeviceTransport.CLASSIC,
            ),
            DeviceInfo(
                name="X6H-ABCD",
                address="F4B3C8E3-C284-9C3A-C549-D786345CB553",
                paired=None,
                transport=DeviceTransport.BLE,
            ),
        ]

        resolved = self.resolver.build_resolved_bluetooth_devices(devices)

        self.assertEqual(len(resolved), 1)
        item = resolved[0]
        self.assertIsNotNone(item.classic_endpoint)
        self.assertIsNotNone(item.ble_endpoint)
        self.assertEqual(item.transport_label, "[classic+ble]")
        self.assertEqual(item.profile_key, "x6h")

    def test_ambiguous_group_is_not_merged(self) -> None:
        devices = [
            DeviceInfo(name="X6H-ABCD", address="AA:BB:CC:DD:EE:01", paired=True, transport=DeviceTransport.CLASSIC),
            DeviceInfo(name="X6H-ABCD", address="AA:BB:CC:DD:EE:02", paired=True, transport=DeviceTransport.CLASSIC),
            DeviceInfo(name="X6H-ABCD", address="F4B3C8E3-C284-9C3A-C549-D786345CB553", paired=None, transport=DeviceTransport.BLE),
        ]

        resolved = self.resolver.build_resolved_bluetooth_devices(devices)

        self.assertEqual(len(resolved), 3)
        self.assertTrue(all(item.classic_endpoint is None or item.ble_endpoint is None for item in resolved))

    def test_resolve_by_classic_or_ble_address_returns_same_logical_device(self) -> None:
        resolved_printer = self.catalog.resolve("X6H-ABCD")
        self.assertIsNotNone(resolved_printer)
        logical = ResolvedBluetoothDevice(
            name="X6H-ABCD",
            resolved_printer=resolved_printer,
            classic_endpoint=DeviceInfo(
                name="X6H-ABCD",
                address="AA:BB:CC:DD:EE:01",
                paired=True,
                transport=DeviceTransport.CLASSIC,
            ),
            ble_endpoint=DeviceInfo(
                name="X6H-ABCD",
                address="F4B3C8E3-C284-9C3A-C549-D786345CB553",
                paired=None,
                transport=DeviceTransport.BLE,
            ),
            display_address="AA:BB:CC:DD:EE:01",
            transport_label="[classic+ble]",
        )

        with patch.object(
            self.resolver,
            "scan_printer_devices_with_failures",
            AsyncMock(return_value=([logical], [])),
        ):
            by_classic = _run(self.resolver.resolve_printer_device("AA:BB:CC:DD:EE:01"))
            by_ble = _run(self.resolver.resolve_printer_device("F4B3C8E3-C284-9C3A-C549-D786345CB553"))

        self.assertEqual(by_classic, logical)
        self.assertEqual(by_ble, logical)

    def test_single_endpoint_builds_single_attempt(self) -> None:
        resolved_printer = self.catalog.resolve("X6H-ABCD")
        self.assertIsNotNone(resolved_printer)
        classic = DeviceInfo(
            name="X6H-ABCD",
            address="AA:BB:CC:DD:EE:01",
            paired=True,
            transport=DeviceTransport.CLASSIC,
        )
        resolved = ResolvedBluetoothDevice(
            name="X6H-ABCD",
            resolved_printer=resolved_printer,
            classic_endpoint=classic,
            ble_endpoint=None,
            display_address=classic.address,
            transport_label="[classic]",
        )

        attempts = self.resolver.build_connection_attempts(resolved)

        self.assertEqual(len(attempts), 1)
        self.assertEqual(attempts[0].address, classic.address)
        self.assertEqual(attempts[0].transport, DeviceTransport.CLASSIC)

    def test_scan_does_not_retry_ble_when_device_is_already_merged(self) -> None:
        classic = DeviceInfo(name="X6H-ABCD", address="AA:BB:CC:DD:EE:02", paired=True, transport=DeviceTransport.CLASSIC)
        ble = DeviceInfo(name="X6H-ABCD", address="F4B3C8E3-C284-9C3A-C549-D786345CB553", paired=None, transport=DeviceTransport.BLE)

        with patch(
            "timiniprint.devices.resolve.SppBackend.scan_with_failures",
            AsyncMock(return_value=([classic, ble], [])),
        ) as backend_scan:
            resolved, failures = _run(
                self.resolver.scan_printer_devices_with_failures(
                    include_classic=True,
                    include_ble=True,
                )
            )

        self.assertEqual(failures, [])
        self.assertEqual(backend_scan.await_count, 1)
        self.assertEqual(len(resolved), 1)
        self.assertEqual(resolved[0].transport_label, "[classic+ble]")


def _run(coro):
    import asyncio

    return asyncio.run(coro)


if __name__ == "__main__":
    unittest.main()
