from __future__ import annotations

from typing import List

<<<<<<< HEAD
from .commands import feed_paper_cmd, make_packet
=======
from .packet import make_packet
from .family import ProtocolFamily
from .types import ImageEncoding
>>>>>>> 43c232936fb59e4ddab986334ca73b1fb5bab45f


def encode_run(color: int, count: int) -> List[int]:
    """Encode a single RLE run for 1-bit data."""
    out = []
    while count > 127:
        out.append((color << 7) | 127)
        count -= 127
    if count > 0:
        out.append((color << 7) | count)
    return out


def rle_encode_line(line: List[int]) -> List[int]:
    """RLE-encode a 1-bit pixel line (0/1 values)."""
    if not line:
        return []
    runs: List[int] = []
    prev = line[0]
    count = 1
    has_black = 1 if prev else 0
    for pix in line[1:]:
        if pix:
            has_black = 1
        if pix == prev:
            count += 1
        else:
            runs.extend(encode_run(prev, count))
            prev = pix
            count = 1
    if has_black:
        runs.extend(encode_run(prev, count))
    if not runs:
        runs.extend(encode_run(prev, count))
    return runs


def pack_line(line: List[int], lsb_first: bool) -> bytes:
    """Pack a 1-bit line into bytes, with selectable bit order."""
    out = bytearray()
    for i in range(0, len(line), 8):
        chunk = line[i : i + 8]
        if len(chunk) < 8:
            chunk = chunk + [0] * (8 - len(chunk))
        value = 0
        if lsb_first:
            for bit, pix in enumerate(chunk):
                if pix:
                    value |= 1 << bit
        else:
            for bit, pix in enumerate(chunk):
                if pix:
                    value |= 1 << (7 - bit)
        out.append(value)
    return bytes(out)


def build_line_packets(
    pixels: List[int],
    width: int,
    speed: int,
<<<<<<< HEAD
    compress: bool,
    lsb_first: bool,
    new_format: bool,
=======
    image_encoding: ImageEncoding,
    lsb_first: bool,
    protocol_family: ProtocolFamily | str,
>>>>>>> 43c232936fb59e4ddab986334ca73b1fb5bab45f
    line_feed_every: int,
) -> bytes:
    """Build data packets for all lines of a raster image."""
    if width % 8 != 0:
        raise ValueError("Width must be divisible by 8")
    height = len(pixels) // width
    width_bytes = width // 8
    out = bytearray()
    for row in range(height):
        line = pixels[row * width : (row + 1) * width]
<<<<<<< HEAD
        if compress:
            rle = rle_encode_line(line)
            if len(rle) <= width_bytes:
                out += make_packet(0xBF, bytes(rle), new_format)
            else:
                raw = pack_line(line, lsb_first)
                out += make_packet(0xA2, raw, new_format)
        else:
            raw = pack_line(line, lsb_first)
            out += make_packet(0xA2, raw, new_format)
        if line_feed_every and (row + 1) % line_feed_every == 0:
            out += feed_paper_cmd(speed, new_format)
=======
        if image_encoding == ImageEncoding.LEGACY_RLE:
            rle = rle_encode_line(line)
            if len(rle) <= width_bytes:
                out += make_packet(0xBF, bytes(rle), protocol_family)
            else:
                raw = pack_line(line, lsb_first)
                out += make_packet(0xA2, raw, protocol_family)
        elif image_encoding == ImageEncoding.LEGACY_RAW:
            raw = pack_line(line, lsb_first)
            out += make_packet(0xA2, raw, protocol_family)
        else:
            raise ValueError(f"Unsupported legacy image encoding: {image_encoding.value}")
        if line_feed_every and (row + 1) % line_feed_every == 0:
            out += make_packet(0xBD, bytes([speed & 0xFF]), protocol_family)
>>>>>>> 43c232936fb59e4ddab986334ca73b1fb5bab45f
    return bytes(out)
