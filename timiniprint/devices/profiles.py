from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Tuple

from ..protocol.family import ProtocolFamily
from ..protocol.families import get_protocol_definition
from ..protocol.dynamic_helpers import V5GDynamicHelper
from ..protocol.types import ImageEncoding, ImagePipelineConfig, PixelFormat

PROFILE_DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "printer_profiles.json"
RULE_DATA_PATH = PROFILE_DATA_PATH.with_name("printer_detection_rules.json")


class DetectionNormalizer:
    _whitespace_re = re.compile(r"\s+")
    _non_hex_re = re.compile(r"[^0-9A-F]")
    _mac_like_re = re.compile(r"^([0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}$")

    @classmethod
    def normalize_name(cls, value: str) -> str:
        return cls._whitespace_re.sub("", value)

    @classmethod
    def fold_name(cls, value: str) -> str:
        return cls.normalize_name(value).upper()

    @classmethod
    def normalize_mac_candidate(cls, value: str) -> str:
        return cls._non_hex_re.sub("", value.upper())

    @classmethod
    def is_mac_like_address(cls, value: str) -> bool:
        return bool(cls._mac_like_re.match(value.strip()))


def _parse_image_pipeline(entry: Mapping[str, object]) -> ImagePipelineConfig:
    formats_value = entry.get("formats")
    encoding_value = entry.get("encoding")
    if not isinstance(formats_value, list) or not formats_value:
        raise ValueError("Image pipeline formats must be a non-empty JSON array")
    if not encoding_value:
        raise ValueError("Image pipeline encoding is required")
    return ImagePipelineConfig(
        formats=tuple(PixelFormat(str(value)) for value in formats_value),
        encoding=ImageEncoding(str(encoding_value)),
    )


def _family_default_image_pipeline(protocol_family: ProtocolFamily) -> ImagePipelineConfig:
    return get_protocol_definition(protocol_family).behavior.default_image_pipeline


@dataclass(frozen=True)
class LevelProfile:
    low: int
    middle: int
    high: int

    def select(self, blackening: int) -> int:
        level = max(1, min(5, blackening))
        if level <= 2:
            return self.low
        if level >= 4:
            return self.high
        return self.middle


@dataclass(frozen=True)
class ModeLevelProfile:
    image: LevelProfile
    text: LevelProfile

    def select(self, *, is_text: bool, blackening: int) -> int:
        target = self.text if is_text else self.image
        return target.select(blackening)


@dataclass(frozen=True)
class SpeedProfile:
    image: int
    text: int

    def select(self, *, is_text: bool) -> int:
        return self.text if is_text else self.image


@dataclass(frozen=True)
class StreamProfile:
    chunk_size: int
    delay_ms: int


@dataclass(frozen=True)
class PrinterProfile:
    profile_key: str
    size: int
    paper_size: int
    print_size: int
    one_length: int
    dev_dpi: int
    can_change_mtu: bool
    has_id: bool
    use_spp: bool
    can_print_label: bool
    label_value: str
    back_paper_num: int
    default_protocol_family: ProtocolFamily
    default_image_pipeline: ImagePipelineConfig
    stream: StreamProfile
    speed: SpeedProfile
    energy: ModeLevelProfile
    post_print_feed_count: int = 2
    density: ModeLevelProfile | None = None
    a4xii: bool = False
    add_mor_pix: Optional[bool] = None

    @property
    def width(self) -> int:
        return self.print_size

    def select_speed(self, *, is_text: bool) -> int:
        return self.speed.select(is_text=is_text)

    def select_energy(self, *, is_text: bool, blackening: int) -> int:
        return self.energy.select(is_text=is_text, blackening=blackening)

    def select_density(self, *, is_text: bool, blackening: int) -> int | None:
        if self.density is None:
            return None
        return self.density.select(is_text=is_text, blackening=blackening)


@dataclass(frozen=True)
class DetectionRule:
    rule_key: str
    prefixes: tuple[str, ...]
    exact_names: tuple[str, ...]
    profile_key: str
    protocol_family: ProtocolFamily
    mac_suffixes: tuple[str, ...] = ()
    image_pipeline: ImagePipelineConfig | None = None
    v5g_dynamic_helper: V5GDynamicHelper | None = None
    testing: bool = False
    testing_note: Optional[str] = None
    _folded_prefixes: tuple[str, ...] = field(init=False, repr=False)
    _folded_exact_names: tuple[str, ...] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "_folded_prefixes",
            tuple(DetectionNormalizer.fold_name(prefix) for prefix in self.prefixes),
        )
        object.__setattr__(
            self,
            "_folded_exact_names",
            tuple(DetectionNormalizer.fold_name(name) for name in self.exact_names),
        )

    def matches(
        self,
        device_name: str,
        address: Optional[str],
        *,
        case_sensitive: bool = True,
    ) -> bool:
        normalized_name = DetectionNormalizer.normalize_name(device_name)
        if case_sensitive:
            matches_name = normalized_name in self.exact_names or any(
                normalized_name.startswith(prefix) for prefix in self.prefixes
            )
        else:
            folded_name = DetectionNormalizer.fold_name(device_name)
            matches_name = folded_name in self._folded_exact_names or any(
                folded_name.startswith(prefix) for prefix in self._folded_prefixes
            )
        if not matches_name:
            return False
        if not self.mac_suffixes:
            return True
        if not address or not DetectionNormalizer.is_mac_like_address(address):
            return False
        normalized = DetectionNormalizer.normalize_mac_candidate(address)
        return any(normalized.endswith(suffix) for suffix in self.mac_suffixes)


@dataclass(frozen=True)
class ResolvedPrinter:
    device_name: str
    profile_key: str
    profile: PrinterProfile
    protocol_family: ProtocolFamily
    image_pipeline: ImagePipelineConfig
    matched_rule_key: str
    v5g_dynamic_helper: V5GDynamicHelper | None = None
    testing: bool = False
    testing_note: Optional[str] = None

    @property
    def experimental_label(self) -> str:
        return " [experimental]" if self.testing else ""


class PrinterCatalog:
    _cache: Dict[Tuple[Path, Path], "PrinterCatalog"] = {}

    def __init__(self, profiles: Iterable[PrinterProfile], rules: Iterable[DetectionRule]) -> None:
        self._profiles = list(profiles)
        self._rules = list(rules)
        self._profile_by_key = {profile.profile_key: profile for profile in self._profiles}
        self._rule_by_key = {rule.rule_key: rule for rule in self._rules}

    @classmethod
    def load(
        cls,
        profile_path: Path = PROFILE_DATA_PATH,
        rule_path: Path = RULE_DATA_PATH,
    ) -> "PrinterCatalog":
        cache_key = (profile_path, rule_path)
        cached = cls._cache.get(cache_key)
        if cached is not None:
            return cached
        profiles_raw = json.loads(profile_path.read_text(encoding="utf-8"))
        rules_raw = json.loads(rule_path.read_text(encoding="utf-8"))
        if not isinstance(profiles_raw, list):
            raise ValueError("Profile file must contain a JSON list")
        if not isinstance(rules_raw, list):
            raise ValueError("Detection rule file must contain a JSON list")
        profiles = [cls._parse_profile(entry) for entry in profiles_raw]
        rules = [cls._parse_rule(entry) for entry in rules_raw]
        catalog = cls(profiles, rules)
        cls._cache[cache_key] = catalog
        return catalog

    @staticmethod
    def _parse_level_profile(payload: Mapping[str, object]) -> LevelProfile:
        return LevelProfile(
            low=int(payload["low"]),
            middle=int(payload["middle"]),
            high=int(payload["high"]),
        )

    @classmethod
    def _parse_mode_profile(cls, payload: Mapping[str, object]) -> ModeLevelProfile:
        return ModeLevelProfile(
            image=cls._parse_level_profile(payload["image"]),
            text=cls._parse_level_profile(payload["text"]),
        )

    @classmethod
    def _parse_profile(cls, entry: Mapping[str, object]) -> PrinterProfile:
        stream_payload = entry["stream"]
        tuning_payload = entry["tuning"]
        density_payload = tuning_payload.get("density")
        profile = PrinterProfile(
            profile_key=str(entry["profile_key"]),
            size=int(entry["size"]),
            paper_size=int(entry["paper_size"]),
            print_size=int(entry["print_size"]),
            one_length=int(entry["one_length"]),
            dev_dpi=int(entry["dev_dpi"]),
            can_change_mtu=bool(entry["can_change_mtu"]),
            has_id=bool(entry["has_id"]),
            use_spp=bool(entry["use_spp"]),
            can_print_label=bool(entry["can_print_label"]),
            label_value=str(entry["label_value"]),
            back_paper_num=int(entry["back_paper_num"]),
            default_protocol_family=ProtocolFamily.from_value(entry["default_protocol_family"]),
            default_image_pipeline=_parse_image_pipeline(entry["default_image_pipeline"]),
            stream=StreamProfile(
                chunk_size=int(stream_payload["chunk_size"]),
                delay_ms=int(stream_payload["delay_ms"]),
            ),
            speed=SpeedProfile(
                image=int(tuning_payload["speed"]["image"]),
                text=int(tuning_payload["speed"]["text"]),
            ),
            energy=cls._parse_mode_profile(tuning_payload["energy"]),
            density=None if density_payload is None else cls._parse_mode_profile(density_payload),
            post_print_feed_count=int(entry.get("post_print_feed_count", 2)),
            a4xii=bool(entry.get("a4xii", False)),
            add_mor_pix=None if entry.get("add_mor_pix") is None else bool(entry.get("add_mor_pix")),
        )
        if profile.stream.chunk_size <= 0:
            raise ValueError(f"Profile {profile.profile_key} has invalid stream.chunk_size")
        if profile.stream.delay_ms < 0:
            raise ValueError(f"Profile {profile.profile_key} has invalid stream.delay_ms")
        return profile

    @staticmethod
    def _parse_rule(entry: Mapping[str, object]) -> DetectionRule:
        prefixes_value = entry.get("prefixes")
        exact_names_value = entry.get("exact_names", [])
        if prefixes_value is None:
            prefixes_value = []
        if not isinstance(prefixes_value, list):
            raise ValueError("Detection rule prefixes must be a JSON array")
        if not isinstance(exact_names_value, list):
            raise ValueError("Detection rule exact_names must be a JSON array")
        if not prefixes_value and not exact_names_value:
            raise ValueError("Detection rule requires at least one prefix or exact_name")
        image_pipeline_value = entry.get("image_pipeline")
        helper_payload = entry.get("v5g_dynamic_helper")
        return DetectionRule(
            rule_key=str(entry["rule_key"]),
            prefixes=tuple(DetectionNormalizer.normalize_name(str(value)) for value in prefixes_value),
            exact_names=tuple(DetectionNormalizer.normalize_name(str(value)) for value in exact_names_value),
            profile_key=str(entry["profile_key"]),
            protocol_family=ProtocolFamily.from_value(entry["protocol_family"]),
            mac_suffixes=tuple(str(value).upper() for value in entry.get("mac_suffixes", [])),
            image_pipeline=None
            if image_pipeline_value is None
            else _parse_image_pipeline(image_pipeline_value),
            v5g_dynamic_helper=None
            if helper_payload is None
            else V5GDynamicHelper(
                helper_kind=str(helper_payload["helper_kind"]),
                density_profile_key=None
                if helper_payload.get("density_profile_key") is None
                else str(helper_payload["density_profile_key"]),
            ),
            testing=bool(entry.get("testing", False)),
            testing_note=None if entry.get("testing_note") is None else str(entry.get("testing_note")),
        )

    @property
    def profiles(self) -> List[PrinterProfile]:
        return list(sorted(self._profiles, key=lambda profile: profile.profile_key))

    @property
    def rules(self) -> List[DetectionRule]:
        return list(self._rules)

    def get_profile(self, profile_key: str) -> Optional[PrinterProfile]:
        return self._profile_by_key.get(profile_key)

    def require_profile(self, profile_key: str) -> PrinterProfile:
        profile = self.get_profile(profile_key)
        if profile is None:
            raise RuntimeError(f"Unknown printer profile '{profile_key}'")
        return profile

    def resolve(self, device_name: str, address: Optional[str] = None) -> Optional[ResolvedPrinter]:
        for case_sensitive in (True, False):
            for rule in self._rules:
                if not rule.matches(device_name, address, case_sensitive=case_sensitive):
                    continue
                profile = self._profile_by_key.get(rule.profile_key)
                if profile is None:
                    raise ValueError(
                        f"Detection rule {rule.rule_key} references unknown profile {rule.profile_key}"
                    )
                image_pipeline = self._resolve_image_pipeline(profile, rule)
                return ResolvedPrinter(
                    device_name=device_name,
                    profile_key=profile.profile_key,
                    profile=profile,
                    protocol_family=rule.protocol_family,
                    image_pipeline=image_pipeline,
                    matched_rule_key=rule.rule_key,
                    v5g_dynamic_helper=rule.v5g_dynamic_helper,
                    testing=rule.testing,
                    testing_note=rule.testing_note,
                )
        return None

    def resolve_manual_profile(
        self,
        device_name: str,
        profile_key: str,
        address: Optional[str] = None,
    ) -> ResolvedPrinter:
        profile = self.require_profile(profile_key)
        detected = self.resolve(device_name, address)
        if detected is not None:
            rule = self._rule_by_key.get(detected.matched_rule_key)
            if rule is not None:
                image_pipeline = self._resolve_image_pipeline(profile, rule)
            elif detected.protocol_family == profile.default_protocol_family:
                image_pipeline = profile.default_image_pipeline
            else:
                image_pipeline = _family_default_image_pipeline(detected.protocol_family)
            return ResolvedPrinter(
                device_name=device_name,
                profile_key=profile.profile_key,
                profile=profile,
                protocol_family=detected.protocol_family,
                image_pipeline=image_pipeline,
                matched_rule_key=f"{detected.matched_rule_key}+manual:{profile.profile_key}",
                v5g_dynamic_helper=detected.v5g_dynamic_helper,
                testing=detected.testing,
                testing_note=detected.testing_note,
            )
        return ResolvedPrinter(
            device_name=device_name,
            profile_key=profile.profile_key,
            profile=profile,
            protocol_family=profile.default_protocol_family,
            image_pipeline=profile.default_image_pipeline,
            matched_rule_key=f"manual:{profile.profile_key}",
            v5g_dynamic_helper=None,
            testing=False,
            testing_note=None,
        )

    @staticmethod
    def _resolve_image_pipeline(
        profile: PrinterProfile,
        rule: DetectionRule,
    ) -> ImagePipelineConfig:
        if rule.image_pipeline is not None:
            return rule.image_pipeline
        if rule.protocol_family == profile.default_protocol_family:
            return profile.default_image_pipeline
        return _family_default_image_pipeline(rule.protocol_family)


__all__ = [
    "DetectionNormalizer",
    "DetectionRule",
    "PrinterCatalog",
    "PrinterProfile",
    "ResolvedPrinter",
]
