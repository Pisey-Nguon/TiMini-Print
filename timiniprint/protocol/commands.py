from __future__ import annotations

<<<<<<< HEAD
import crc8


def crc8_value(data: bytes) -> int:
    """Return CRC8 checksum byte for the payload."""
    hasher = crc8.crc8()
    hasher.update(data)
    return hasher.digest()[0]


def make_packet(cmd: int, payload: bytes, new_format: bool) -> bytes:
    """Wrap a payload in the printer command packet format."""
    length = len(payload)
    header = bytes(
        [
            0x51,
            0x78,
            cmd & 0xFF,
            0x00,
            length & 0xFF,
            (length >> 8) & 0xFF,
        ]
    )
    checksum = crc8_value(payload)
    packet = header + payload + bytes([checksum, 0xFF])
    if new_format:
        return bytes([0x12]) + packet
    return packet


def blackening_cmd(level: int, new_format: bool) -> bytes:
    """Build the blackening (density) command packet."""
    level = max(1, min(5, level))
    payload = bytes([0x30 + level])
    return make_packet(0xA4, payload, new_format)


def energy_cmd(energy: int, new_format: bool) -> bytes:
=======
from .families import get_protocol_behavior
from .family import ProtocolFamily
from .packet import crc8_value, make_packet


def blackening_cmd(level: int, protocol_family: ProtocolFamily | str) -> bytes:
    """Build the blackening (density) command packet."""
    level = max(1, min(5, level))
    payload = bytes([0x30 + level])
    return make_packet(0xA4, payload, protocol_family)


def energy_cmd(energy: int, protocol_family: ProtocolFamily | str) -> bytes:
>>>>>>> 43c232936fb59e4ddab986334ca73b1fb5bab45f
    """Build the energy command packet (empty if energy <= 0)."""
    if energy <= 0:
        return b""
    payload = energy.to_bytes(2, "little", signed=False)
<<<<<<< HEAD
    return make_packet(0xAF, payload, new_format)


def print_mode_cmd(is_text: bool, new_format: bool) -> bytes:
    """Build the print mode command packet (text vs image)."""
    payload = bytes([1 if is_text else 0])
    return make_packet(0xBE, payload, new_format)


def feed_paper_cmd(speed: int, new_format: bool) -> bytes:
    """Build the feed paper command packet."""
    payload = bytes([speed & 0xFF])
    return make_packet(0xBD, payload, new_format)
=======
    return make_packet(0xAF, payload, protocol_family)


def print_mode_cmd(is_text: bool, protocol_family: ProtocolFamily | str) -> bytes:
    """Build the print mode command packet (text vs image)."""
    payload = bytes([1 if is_text else 0])
    return make_packet(0xBE, payload, protocol_family)


def feed_paper_cmd(speed: int, protocol_family: ProtocolFamily | str) -> bytes:
    """Build the feed paper command packet."""
    payload = bytes([speed & 0xFF])
    return make_packet(0xBD, payload, protocol_family)
>>>>>>> 43c232936fb59e4ddab986334ca73b1fb5bab45f


def _paper_payload(dpi: int) -> bytes:
    if dpi == 300:
        return bytes([0x48, 0x00])
    return bytes([0x30, 0x00])


<<<<<<< HEAD
def paper_cmd(dpi: int, new_format: bool) -> bytes:
    """Build the paper size/DPI command packet."""
    return make_packet(0xA1, _paper_payload(dpi), new_format)


def advance_paper_cmd(dpi: int, new_format: bool) -> bytes:
    """Build the manual feed command (matches iPrintUtility)."""
    return make_packet(0xA1, _paper_payload(dpi), new_format)


def retract_paper_cmd(dpi: int, new_format: bool) -> bytes:
    """Build the manual retract command (matches iPrintUtility)."""
    return make_packet(0xA0, _paper_payload(dpi), new_format)


def dev_state_cmd(new_format: bool) -> bytes:
    """Build the device state query command packet."""
    return make_packet(0xA3, bytes([0x00]), new_format)
=======
def paper_cmd(dpi: int, protocol_family: ProtocolFamily | str) -> bytes:
    """Build the paper size/DPI command packet."""
    return make_packet(0xA1, _paper_payload(dpi), protocol_family)


def advance_paper_cmd(dpi: int, protocol_family: ProtocolFamily | str) -> bytes:
    """Build the manual feed command packet."""
    family = ProtocolFamily.from_value(protocol_family)
    builder = get_protocol_behavior(family).advance_paper_builder
    if builder is not None:
        return builder(dpi, family)
    return make_packet(0xA1, _paper_payload(dpi), family)


def retract_paper_cmd(dpi: int, protocol_family: ProtocolFamily | str) -> bytes:
    """Build the manual retract command packet."""
    family = ProtocolFamily.from_value(protocol_family)
    builder = get_protocol_behavior(family).retract_paper_builder
    if builder is not None:
        return builder(dpi, family)
    return make_packet(0xA0, _paper_payload(dpi), family)


def dev_state_cmd(protocol_family: ProtocolFamily | str) -> bytes:
    """Build the device state query command packet."""
    return make_packet(0xA3, bytes([0x00]), protocol_family)
>>>>>>> 43c232936fb59e4ddab986334ca73b1fb5bab45f
