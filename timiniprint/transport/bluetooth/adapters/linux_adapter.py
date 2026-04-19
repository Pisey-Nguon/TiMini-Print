from __future__ import annotations

import socket
from typing import List, Optional

from .base import _ClassicBluetoothAdapter
from .linux_cmd import LinuxCommandTools
<<<<<<< HEAD
from ..types import DeviceInfo, SocketLike
=======
from ....protocol.family import ProtocolFamily
from ..types import DeviceInfo, SocketLike
from .... import reporting
>>>>>>> 43c232936fb59e4ddab986334ca73b1fb5bab45f


class _LinuxClassicAdapter(_ClassicBluetoothAdapter):
    def __init__(self) -> None:
        self._commands = LinuxCommandTools()

    def scan_blocking(self, timeout: float) -> List[DeviceInfo]:
        devices, _ = self._commands.scan_devices(timeout)
        return DeviceInfo.dedupe(devices)

<<<<<<< HEAD
    def create_socket(self, pairing_hint: Optional[bool] = None) -> SocketLike:
=======
    def create_socket(
        self,
        pairing_hint: Optional[bool] = None,
        protocol_family: Optional[ProtocolFamily] = None,
        reporter: reporting.Reporter = reporting.DUMMY_REPORTER,
    ) -> SocketLike:
>>>>>>> 43c232936fb59e4ddab986334ca73b1fb5bab45f
        if not hasattr(socket, "AF_BLUETOOTH") or not hasattr(socket, "BTPROTO_RFCOMM"):
            raise RuntimeError(
                "RFCOMM sockets are not supported on this system. Use --serial or run on Linux."
            )
<<<<<<< HEAD
        return socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM)

    def resolve_rfcomm_channel(self, address: str) -> Optional[int]:
        return self._commands.resolve_rfcomm_channel(address)
=======
        _ = protocol_family
        return socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM)

    def resolve_rfcomm_channels(self, address: str) -> List[int]:
        return self._commands.resolve_rfcomm_channels(address)
>>>>>>> 43c232936fb59e4ddab986334ca73b1fb5bab45f

    def ensure_paired(self, address: str, pairing_hint: Optional[bool] = None) -> None:
        self._commands.ensure_paired(address)
