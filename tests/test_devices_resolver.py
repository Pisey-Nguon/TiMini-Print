from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from tests.helpers import reset_registry_cache
from timiniprint.devices import DeviceResolver, PrinterCatalog
from timiniprint.devices.resolve import ResolvedBluetoothDevice
from timiniprint.protocol.family import ProtocolFamily
from timiniprint.transport.bluetooth.types import DeviceInfo, DeviceTransport


class DevicesResolverTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_registry_cache()
        self.catalog = PrinterCatalog.load()
        self.resolver = DeviceResolver(self.catalog)

    def test_filter_printer_devices(self) -> None:
        devices = [
            DeviceInfo(name="X6H-ABCD", address="AA:BB:CC:DD:EE:01", transport=DeviceTransport.CLASSIC),
            DeviceInfo(name="Unknown Device", address="AA:BB:CC:DD:EE:02", transport=DeviceTransport.BLE),
        ]

        out = self.resolver.filter_printer_devices(devices)

        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].name, "X6H-ABCD")

    def test_resolve_printer_device_selects_by_name_contains_and_address(self) -> None:
        resolved_printer = self.catalog.resolve("X6H-FF5F")
        self.assertIsNotNone(resolved_printer)
        logical = ResolvedBluetoothDevice(
            name="X6H-FF5F",
            resolved_printer=resolved_printer,
            classic_endpoint=DeviceInfo(
                name="X6H-FF5F",
                address="AA:BB:CC:DD:EE:01",
                transport=DeviceTransport.CLASSIC,
            ),
            ble_endpoint=None,
            display_address="AA:BB:CC:DD:EE:01",
            transport_label="[classic]",
        )

        with patch.object(
            self.resolver,
            "scan_printer_devices_with_failures",
            AsyncMock(return_value=([logical], [])),
        ):
            by_name = _run(self.resolver.resolve_printer_device("X6H-FF5F"))
            by_contains = _run(self.resolver.resolve_printer_device("FF5F"))
            by_address = _run(self.resolver.resolve_printer_device("AA:BB:CC:DD:EE:01"))

        self.assertEqual(by_name, logical)
        self.assertEqual(by_contains, logical)
        self.assertEqual(by_address, logical)

    def test_scan_retry_ble_when_classic_only_detected(self) -> None:
        classic = DeviceInfo(name="X6H-ABCD", address="AA:BB:CC:DD:EE:01", transport=DeviceTransport.CLASSIC)
        ble = DeviceInfo(name="X6H-ABCD", address="UUID-1", transport=DeviceTransport.BLE)

        with patch(
            "timiniprint.devices.resolve.SppBackend.scan_with_failures",
            AsyncMock(side_effect=[([classic], []), ([ble], [])]),
        ) as scan_mock:
            resolved, failures = _run(
                self.resolver.scan_printer_devices_with_failures(include_classic=True, include_ble=True)
            )

        self.assertEqual(failures, [])
        self.assertEqual(scan_mock.await_count, 2)
        self.assertEqual(len(resolved), 1)
        self.assertEqual(resolved[0].transport_label, "[classic+ble]")
        self.assertEqual(resolved[0].profile_key, "x6h")

    def test_build_connection_attempts_respects_profile_use_spp(self) -> None:
        classic = DeviceInfo(name="X6H-ABCD", address="AA:BB:CC:DD:EE:01", transport=DeviceTransport.CLASSIC)
        ble = DeviceInfo(name="X6H-ABCD", address="UUID-1", transport=DeviceTransport.BLE)
        spp_printer = self.catalog.resolve("X6H-ABCD")
        ble_printer = self.catalog.resolve("CP01-ABCD")
        self.assertIsNotNone(spp_printer)
        self.assertIsNotNone(ble_printer)

        spp_first = ResolvedBluetoothDevice("X6H-ABCD", spp_printer, classic, ble, classic.address, "[classic+ble]")
        ble_first = ResolvedBluetoothDevice("CP01-ABCD", ble_printer, classic, ble, classic.address, "[classic+ble]")

        self.assertEqual(
            self.resolver.build_connection_attempts(spp_first),
            [
                DeviceInfo(
                    name=classic.name,
                    address=classic.address,
                    paired=classic.paired,
                    transport=classic.transport,
                    protocol_family=ProtocolFamily.LEGACY,
                ),
                DeviceInfo(
                    name=ble.name,
                    address=ble.address,
                    paired=ble.paired,
                    transport=ble.transport,
                    protocol_family=ProtocolFamily.LEGACY,
                ),
            ],
        )
        self.assertEqual(
            self.resolver.build_connection_attempts(ble_first),
            [
                DeviceInfo(
                    name=ble.name,
                    address=ble.address,
                    paired=ble.paired,
                    transport=ble.transport,
                    protocol_family=ble_printer.protocol_family,
                ),
                DeviceInfo(
                    name=classic.name,
                    address=classic.address,
                    paired=classic.paired,
                    transport=classic.transport,
                    protocol_family=ble_printer.protocol_family,
                ),
            ],
        )

    def test_manual_profile_preserves_detected_bluetooth_metadata(self) -> None:
        auto = self.resolver.resolve_printer("MX10-ABCD", address="AA:BB:CC:DD:EE:58")
        manual = self.resolver.resolve_printer("MX10-ABCD", profile_key="v5g_small_203", address="AA:BB:CC:DD:EE:58")

        self.assertEqual(manual.profile_key, "v5g_small_203")
        self.assertEqual(manual.protocol_family, auto.protocol_family)
        self.assertEqual(manual.image_pipeline, auto.image_pipeline)
        self.assertEqual(manual.v5g_dynamic_helper, auto.v5g_dynamic_helper)

    def test_manual_profile_preserves_mac59_family_switch(self) -> None:
        auto = self.resolver.resolve_printer("MX10-ABCD", address="AA:BB:CC:DD:EE:59")
        manual = self.resolver.resolve_printer("MX10-ABCD", profile_key="v5g_small_203", address="AA:BB:CC:DD:EE:59")

        self.assertEqual(auto.protocol_family, ProtocolFamily.V5X)
        self.assertEqual(manual.protocol_family, auto.protocol_family)
        self.assertEqual(manual.image_pipeline, auto.image_pipeline)
        self.assertEqual(manual.v5g_dynamic_helper, auto.v5g_dynamic_helper)

    def test_manual_profile_changes_connection_order_when_transport_preference_differs(self) -> None:
        classic = DeviceInfo(name="CP01-ABCD", address="AA:BB:CC:DD:EE:01", transport=DeviceTransport.CLASSIC)
        ble = DeviceInfo(name="CP01-ABCD", address="UUID-1", transport=DeviceTransport.BLE)
        auto_printer = self.catalog.resolve("CP01-ABCD")
        manual_printer = self.resolver.resolve_printer("CP01-ABCD", profile_key="x6h", address=classic.address)

        self.assertIsNotNone(auto_printer)
        self.assertFalse(auto_printer.profile.use_spp)
        self.assertTrue(manual_printer.profile.use_spp)

        resolved = ResolvedBluetoothDevice(
            "CP01-ABCD",
            auto_printer,
            classic,
            ble,
            classic.address,
            "[classic+ble]",
        )

        attempts = self.resolver.build_connection_attempts(
            resolved,
            manual_printer.protocol_family,
            manual_printer.profile,
        )

        self.assertEqual([item.transport for item in attempts], [DeviceTransport.CLASSIC, DeviceTransport.BLE])


def _run(coro):
    import asyncio

    return asyncio.run(coro)


if __name__ == "__main__":
    unittest.main()
