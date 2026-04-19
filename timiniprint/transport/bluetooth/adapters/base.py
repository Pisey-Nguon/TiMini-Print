from __future__ import annotations

from typing import List, Optional

<<<<<<< HEAD
from ..types import DeviceInfo, DeviceTransport, SocketLike
=======
from ....protocol.family import ProtocolFamily
from ..types import DeviceInfo, DeviceTransport, SocketLike
from .... import reporting
>>>>>>> 43c232936fb59e4ddab986334ca73b1fb5bab45f


class _BaseBluetoothAdapter:
    transport: DeviceTransport
<<<<<<< HEAD
    single_channel = False
=======
>>>>>>> 43c232936fb59e4ddab986334ca73b1fb5bab45f

    def scan_blocking(self, timeout: float) -> List[DeviceInfo]:
        raise NotImplementedError

<<<<<<< HEAD
    def create_socket(self, pairing_hint: Optional[bool] = None) -> SocketLike:
        raise NotImplementedError

    def resolve_rfcomm_channel(self, address: str) -> Optional[int]:
        return None
=======
    def create_socket(
        self,
        pairing_hint: Optional[bool] = None,
        protocol_family: Optional[ProtocolFamily] = None,
        reporter: reporting.Reporter = reporting.DUMMY_REPORTER,
    ) -> SocketLike:
        raise NotImplementedError

    def resolve_rfcomm_channels(self, address: str) -> List[int]:
        return []
>>>>>>> 43c232936fb59e4ddab986334ca73b1fb5bab45f

    def ensure_paired(self, address: str, pairing_hint: Optional[bool] = None) -> None:
        return None


class _ClassicBluetoothAdapter(_BaseBluetoothAdapter):
    transport = DeviceTransport.CLASSIC


class _BleBluetoothAdapter(_BaseBluetoothAdapter):
    transport = DeviceTransport.BLE
<<<<<<< HEAD
    single_channel = True

    def resolve_rfcomm_channel(self, address: str) -> Optional[int]:
        return 1
=======

    def resolve_rfcomm_channels(self, address: str) -> List[int]:
        return [1]
>>>>>>> 43c232936fb59e4ddab986334ca73b1fb5bab45f
