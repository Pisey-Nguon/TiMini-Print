from __future__ import annotations

<<<<<<< HEAD
from dataclasses import dataclass
from typing import List


@dataclass(frozen=True)
class Raster:
    """Row-major 0/1 pixel buffer used by the protocol encoder."""

    pixels: List[int]
    width: int

    def validate(self) -> None:
        """Validate dimensions for protocol encoding."""
        if self.width <= 0:
            raise ValueError("Width must be greater than zero")
        if len(self.pixels) % self.width != 0:
            raise ValueError("Pixels length must be a multiple of width")

    @property
    def height(self) -> int:
        """Return raster height computed from width and pixel count."""
        self.validate()
        return len(self.pixels) // self.width
=======
from enum import Enum
from dataclasses import dataclass

from ..raster import PixelFormat


class ImageEncoding(str, Enum):
    LEGACY_RAW = "legacy_raw"
    LEGACY_RLE = "legacy_rle"
    V5G_DOT = "v5g_dot"
    V5G_GRAY = "v5g_gray"
    V5X_DOT = "v5x_dot"
    V5X_GRAY = "v5x_gray"
    V5C_A4 = "v5c_a4"
    V5C_A5 = "v5c_a5"
    DCK_DEFAULT = "dck_default"


@dataclass(frozen=True)
class ImagePipelineConfig:
    formats: tuple[PixelFormat, ...]
    encoding: ImageEncoding

    def __post_init__(self) -> None:
        if not self.formats:
            raise ValueError("Image pipeline formats must not be empty")
        normalized = tuple(
            value if isinstance(value, PixelFormat) else PixelFormat(str(value))
            for value in self.formats
        )
        if len(set(normalized)) != len(normalized):
            raise ValueError("Image pipeline formats must be unique")
        object.__setattr__(self, "formats", normalized)

    @property
    def default_format(self) -> PixelFormat:
        return self.formats[0]

    def supports(self, pixel_format: PixelFormat) -> bool:
        return pixel_format in self.formats

    def with_default_format(self, pixel_format: PixelFormat) -> "ImagePipelineConfig":
        if pixel_format not in self.formats:
            raise ValueError(
                f"Image pipeline does not support raster format {pixel_format.value}"
            )
        if pixel_format == self.formats[0]:
            return self
        reordered = (pixel_format,) + tuple(
            value for value in self.formats if value != pixel_format
        )
        return ImagePipelineConfig(formats=reordered, encoding=self.encoding)
>>>>>>> 43c232936fb59e4ddab986334ca73b1fb5bab45f
