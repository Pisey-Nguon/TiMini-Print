"""Family-aware BLE transport helpers for the bleak adapter.

This module owns resolved GATT bindings, optional flow-control state and
family-specific write routing. `_BleakSocket` uses it as a thin transport layer
once the BLE connection itself is established.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .... import reporting
from ....protocol.dynamic_helpers import (
    DensityLevels,
    V5GDynamicRuntimeContext,
    V5GContinuousPlan,
    mx06_continuous_plan,
    mx06_single_density_value,
    mx10_continuous_plan,
    mx10_continuous_series,
    mx10_single_density_value,
    pd01_continuous_plan,
    pd01_continuous_series,
    pd01_single_density_value,
    v5g_continuous_series,
)
from ....protocol.families import BleTransportProfile, split_prefixed_bulk_stream
from ....protocol.family import ProtocolFamily
from ....protocol.packet import make_packet, prefixed_packet_length
from ....protocol.families.v5c import V5C_QUERY_STATUS_PACKET
from ....protocol.families.v5x import (
    V5X_GET_SERIAL_PACKET,
    V5X_GRAY_MODE_SUFFIX,
    V5X_STATUS_POLL_PACKET,
)
from .bleak_adapter_endpoint_resolver import _BleWriteEndpointResolver, _WriteSelection


@dataclass
class _BleakBindings:
    """Resolved GATT endpoints and write preferences for one connection."""

    write_char: Any = None
    bulk_write_char: Any = None
    notify_char: Any = None
    write_selection_strategy: str = "unknown"
    write_response_preference: Optional[bool] = None
    write_service_uuid: str = ""
    write_char_uuid: str = ""
    bulk_write_char_uuid: str = ""
    notify_char_uuid: str = ""


@dataclass
class _V5XSessionState:
    """Session-scoped state derived from V5X command and notify traffic."""

    task_state_name: str = "normal"
    last_density_payload: Optional[bytes] = None
    print_head_type: str = "gaoya"
    firmware_version: str = ""
    connect_info_received: bool = False
    device_serial: str = ""
    serial_valid: Optional[bool] = None
    last_a7_payload: bytes = b""
    last_a9_status: Optional[int] = None
    task_state: Optional[int] = None
    battery_level: Optional[int] = None
    temperature_c: Optional[int] = None
    error_group: Optional[int] = None
    error_code: Optional[int] = None
    last_error_signature: Optional[tuple[int, int]] = None
    status_poll_ack_seen: bool = False
    last_ab_status: Optional[int] = None
    mxw_sign_requested: bool = False
    compatibility: "_V5XCompatibilityState" = field(default_factory=lambda: _V5XCompatibilityState())


@dataclass
class _V5XCompatibilityState:
    """Non-blocking compatibility state kept for future auth integration."""

    mode: str = "unknown"
    checked: bool = False
    confirmed: Optional[bool] = None
    last_result_code: Optional[int] = None
    backend_write_cmd: bytes = b""


@dataclass
class _V5CCompatibilityState:
    """Non-blocking compatibility state kept for future auth integration."""

    mode: str = "unknown"
    request_pending: bool = False
    checked: bool = False
    confirmed: Optional[bool] = None
    last_result_code: Optional[int] = None
    backend_write_cmd: bytes = b""
    last_trigger_opcode: Optional[int] = None
    last_trigger_packet: bytes = b""


@dataclass
class _V5CSessionState:
    """Session-scoped state derived from V5C command and notify traffic."""

    status_code: Optional[int] = None
    status_name: str = "unknown"
    is_charging: bool = False
    query_status_in_flight: bool = False
    print_complete_seen: bool = False
    max_print_height: Optional[int] = None
    device_serial: str = ""
    serial_valid: Optional[bool] = None
    last_auth_payload: bytes = b""
    last_error_status: Optional[int] = None
    compatibility: "_V5CCompatibilityState" = field(default_factory=lambda: _V5CCompatibilityState())


@dataclass
class _V5GSessionState:
    """Session-scoped state used by V5G density packet rewriting."""

    temperature_c: int = -1
    d2_status: bool = False
    didian_status: bool = False
    printing: bool = False
    helper_kind: Optional[str] = None
    density_profile_key: Optional[str] = None
    last_complete_time: float = 0.0
    last_density_value: Optional[int] = None
    last_single_density_value: int = 0
    last_print_record_copies: int = 0
    last_print_record_density: Optional[int] = None
    last_print_mode_is_text: bool = False


@dataclass(frozen=True)
class _V5XJobContext:
    """Derived print-job metadata used to tune V5X session behavior."""

    coverage_ratio: float = 0.0
    is_gray: bool = False


class _BleakTransportSession:
    """Encapsulates family-specific endpoint binding and write routing."""

    def __init__(
        self,
        protocol_family: ProtocolFamily,
        transport_profile: BleTransportProfile,
        write_resolver: _BleWriteEndpointResolver,
        reporter: reporting.Reporter,
    ) -> None:
        self._protocol_family = protocol_family
        self._transport_profile = transport_profile
        self._write_resolver = write_resolver
        self._reporter = reporter
        self.bindings = _BleakBindings()
        self.notify_started = False
        self.flow_can_write = True
        self._command_ack_events: Dict[int, asyncio.Event] = {}
        self._start_ready_event: Optional[asyncio.Event] = None
        self._connect_info_event: Optional[asyncio.Event] = None
        self._client: Any = None
        self._pending_get_serial: Optional[asyncio.Task[None]] = None
        self._pending_status_poll: Optional[asyncio.Task[None]] = None
        self._pending_v5g_reset: Optional[asyncio.Task[None]] = None
        self._v5x_state = _V5XSessionState()
        self._v5c_state = _V5CSessionState()
        self._v5g_state = _V5GSessionState()

    def apply_write_selection(self, selection: _WriteSelection) -> None:
        self.bindings.write_char = selection.char
        self.bindings.write_selection_strategy = selection.strategy
        self.bindings.write_response_preference = selection.response_preference
        self.bindings.write_service_uuid = selection.service_uuid
        self.bindings.write_char_uuid = selection.char_uuid
        self._report_debug(
            "selected write characteristic "
            f"service={self.bindings.write_service_uuid} char={self.bindings.write_char_uuid} "
            f"strategy={self.bindings.write_selection_strategy} "
            f"response_preference={self.bindings.write_response_preference}"
        )

    def configure_endpoints(self, services: Iterable[object]) -> None:
        """Bind optional bulk and notify endpoints from the transport profile."""
        transport = self._transport_profile

        self.bindings.bulk_write_char = None
        self.bindings.bulk_write_char_uuid = ""
        if transport.bulk_char_uuid:
            self.bindings.bulk_write_char = self._find_characteristic_by_uuid(
                services,
                transport.bulk_char_uuid,
                preferred_service_uuid=transport.preferred_service_uuid,
            )
            self.bindings.bulk_write_char_uuid = _BleWriteEndpointResolver._normalize_uuid(
                getattr(self.bindings.bulk_write_char, "uuid", "")
            )
            if self.bindings.bulk_write_char:
                self._report_debug(
                    f"selected bulk characteristic char={self.bindings.bulk_write_char_uuid}"
                )
            else:
                self._report_debug("configured bulk characteristic not found")

        self.bindings.notify_char = None
        self.bindings.notify_char_uuid = ""
        if transport.notify_char_uuid:
            self.bindings.notify_char = self._find_characteristic_by_uuid(
                services,
                transport.notify_char_uuid,
                preferred_service_uuid=transport.preferred_service_uuid,
            )
        elif transport.prefer_generic_notify or transport.flow_control is not None:
            # Some families only expose a generic notifier, so fall back to any
            # notify/indicate characteristic when the profile asks for it.
            self.bindings.notify_char = self._find_notify_characteristic(services)

        self.bindings.notify_char_uuid = _BleWriteEndpointResolver._normalize_uuid(
            getattr(self.bindings.notify_char, "uuid", "")
        )
        if self.bindings.notify_char:
            self._report_debug(
                f"selected notify characteristic char={self.bindings.notify_char_uuid}"
            )
        elif transport.flow_control is not None:
            self._report_debug("configured notify characteristic not found")

    async def start_notify_if_available(self, client: Any, callback) -> None:
        if not self.bindings.notify_char or not self.bindings.notify_char_uuid:
            return
        start_notify = getattr(client, "start_notify", None)
        if not callable(start_notify):
            return
        await start_notify(self.bindings.notify_char_uuid, callback)
        self.notify_started = True
        self._report_debug(
            f"subscribed to notify characteristic {self.bindings.notify_char_uuid}"
        )

    async def stop_notify_if_started(self, client: Any) -> None:
        self._cancel_pending_get_serial()
        self._cancel_pending_status_poll()
        self._cancel_pending_v5g_reset()
        if not self.notify_started or not self.bindings.notify_char_uuid:
            return
        stop_notify = getattr(client, "stop_notify", None)
        if not callable(stop_notify):
            return
        try:
            await stop_notify(self.bindings.notify_char_uuid)
        except Exception:
            pass
        self.notify_started = False

    async def initialize_connection(
        self,
        client: Any,
        *,
        mtu_size: int,
        timeout: float,
    ) -> None:
        self._client = client
        if self._protocol_family is ProtocolFamily.V5G:
            self._v5g_state = _V5GSessionState()
        if self._protocol_family is ProtocolFamily.V5X:
            self._connect_info_event = asyncio.Event()
        if not self._transport_profile.connect_packets:
            return
        if not self.bindings.write_char:
            raise RuntimeError("No write characteristic available")
        response = self._resolve_response_mode(
            self.bindings.write_char,
            self.bindings.write_selection_strategy,
            self.bindings.write_response_preference,
        )
        if self._transport_profile.connect_delay_ms > 0:
            await asyncio.sleep(self._transport_profile.connect_delay_ms / 1000.0)
        for packet in self._transport_profile.connect_packets:
            await self._write_chunks(
                client,
                self.bindings.write_char,
                packet,
                response=response,
                chunk_size=min(mtu_size, self._transport_profile.standard_chunk_cap),
                delay_seconds=self._transport_profile.standard_write_delay_ms / 1000.0,
                timeout=timeout,
            )
        if self._protocol_family is ProtocolFamily.V5X and self.notify_started:
            await self._wait_for_connect_info(min(timeout, 0.4))

    async def send(
        self,
        client: Any,
        data: bytes,
        *,
        mtu_size: int,
        timeout: float,
        runtime_context: V5GDynamicRuntimeContext | None = None,
    ) -> None:
        self._client = client
        if not self.bindings.write_char:
            raise RuntimeError("No write characteristic available")

        if self._transport_profile.split_bulk_writes:
            await self._send_split(
                client,
                data,
                mtu_size=mtu_size,
                timeout=timeout,
            )
            return
        await self._send_standard(
            client,
            data,
            mtu_size=mtu_size,
            timeout=timeout,
            runtime_context=runtime_context,
        )

    async def _send_standard(
        self,
        client: Any,
        data: bytes,
        *,
        mtu_size: int,
        timeout: float,
        runtime_context: V5GDynamicRuntimeContext | None = None,
    ) -> None:
        if self._protocol_family is ProtocolFamily.V5G:
            self._v5g_state.printing = True
            if runtime_context is not None:
                self._v5g_state.helper_kind = runtime_context.helper_kind
                self._v5g_state.density_profile_key = runtime_context.density_profile_key
        try:
            if runtime_context is not None:
                data = self._prepare_v5g_standard_payload(data, runtime_context)
            response = self._resolve_response_mode(
                self.bindings.write_char,
                self.bindings.write_selection_strategy,
                self.bindings.write_response_preference,
            )
            self._report_debug(
                f"write mode response={response} strategy={self.bindings.write_selection_strategy} "
                f"char={self.bindings.write_char_uuid}"
            )
            self._track_v5c_outgoing_query_status(data)
            await self._write_chunks(
                client,
                self.bindings.write_char,
                data,
                response=response,
                chunk_size=min(mtu_size, self._transport_profile.standard_chunk_cap),
                delay_seconds=self._transport_profile.standard_write_delay_ms / 1000.0,
                timeout=timeout,
                wait_for_flow=self._transport_profile.wait_for_flow_on_standard_write,
            )
        finally:
            if self._protocol_family is ProtocolFamily.V5G:
                self._v5g_state.printing = False
                self._v5g_state.last_complete_time = time.time()

    def _prepare_v5g_standard_payload(
        self,
        data: bytes,
        runtime_context: V5GDynamicRuntimeContext,
    ) -> bytes:
        if self._protocol_family is not ProtocolFamily.V5G or len(data) <= 50:
            return data
        packets = self._split_prefixed_packets(data)
        if packets is None:
            return data
        density_indexes = [
            index for index, packet in enumerate(packets)
            if self._extract_prefixed_opcode(packet) == 0xF2
        ]
        if not density_indexes:
            return data

        if self._should_use_v5g_continuous_helper(packets, density_indexes, runtime_context):
            rewrite_map = self._build_v5g_continuous_density_map(
                packets,
                density_indexes,
                runtime_context,
            )
        else:
            rewrite_map = self._build_v5g_single_density_map(
                packets,
                density_indexes,
                runtime_context,
            )

        updated = bytearray()
        current_mode_is_text = self._v5g_state.last_print_mode_is_text
        last_density_value = self._v5g_state.last_density_value
        for index, packet in enumerate(packets):
            opcode = self._extract_prefixed_opcode(packet)
            if opcode == 0xBE:
                current_mode_is_text = self._extract_v5g_print_mode(packet)
            if index in rewrite_map:
                packet = make_packet(
                    0xF2,
                    int(rewrite_map[index]).to_bytes(2, "little", signed=False),
                    ProtocolFamily.V5G,
                )
                last_density_value = rewrite_map[index]
            elif opcode == 0xF2:
                current_value = self._extract_v5g_density_value(packet)
                if current_value is not None:
                    last_density_value = current_value
            updated += packet
        self._v5g_state.last_density_value = last_density_value
        self._v5g_state.last_print_mode_is_text = current_mode_is_text
        if not rewrite_map:
            return data
        return bytes(updated)

    def _should_use_v5g_continuous_helper(
        self,
        packets: list[bytes],
        density_indexes: list[int],
        runtime_context: V5GDynamicRuntimeContext,
    ) -> bool:
        if len(density_indexes) <= 4:
            return False
        first_index = density_indexes[0]
        current_mode_is_text = self._mode_before_packet_index(packets, first_index)
        levels = self._select_v5g_levels(runtime_context, current_mode_is_text)
        first_value = self._extract_v5g_density_value(packets[first_index])
        if levels is None or first_value is None:
            return False
        helper_kind = runtime_context.helper_kind
        qualifies = helper_kind in {"mx06", "mx10", "pd01"} or first_value >= levels.middle
        if not qualifies:
            return False
        return runtime_context.applies_d2_status or helper_kind in {"mx10", "pd01"}

    def _build_v5g_single_density_map(
        self,
        packets: list[bytes],
        density_indexes: list[int],
        runtime_context: V5GDynamicRuntimeContext,
    ) -> Dict[int, int]:
        first_index = density_indexes[0]
        current_mode_is_text = self._mode_before_packet_index(packets, first_index)
        levels = self._select_v5g_levels(runtime_context, current_mode_is_text)
        current_value = self._extract_v5g_density_value(packets[first_index])
        if current_value is None or levels is None:
            return {}

        adjusted = current_value
        helper_kind = runtime_context.helper_kind
        recent_completion = (time.time() - self._v5g_state.last_complete_time) < 50
        temperature_c = self._v5g_temperature_for_helper()
        if helper_kind == "mx06" and self._v5g_state.d2_status and recent_completion:
            adjusted = mx06_single_density_value(
                current_value,
                self._v5g_state.last_single_density_value,
            )
        elif helper_kind == "pd01" and temperature_c >= 50:
            adjusted = pd01_single_density_value(temperature_c, levels, current_value)
        elif helper_kind == "mx10" and temperature_c >= 50:
            adjusted = mx10_single_density_value(temperature_c, levels, current_value)

        self._v5g_state.last_single_density_value = adjusted
        if adjusted == current_value:
            return {}

        self._report_debug(
            f"V5G single density adjusted mode={'text' if current_mode_is_text else 'image'} "
            f"user={current_value} target={adjusted} temp={self._v5g_state.temperature_c}"
        )
        return {density_index: adjusted for density_index in density_indexes}

    def _build_v5g_continuous_density_map(
        self,
        packets: list[bytes],
        density_indexes: list[int],
        runtime_context: V5GDynamicRuntimeContext,
    ) -> Dict[int, int]:
        first_index = density_indexes[0]
        current_mode_is_text = self._mode_before_packet_index(packets, first_index)
        levels = self._select_v5g_levels(runtime_context, current_mode_is_text)
        first_value = self._extract_v5g_density_value(packets[first_index])
        if levels is None or first_value is None:
            return {}
        helper_kind = runtime_context.helper_kind
        temperature_c = self._v5g_temperature_for_helper()
        if helper_kind == "mx06":
            plan = mx06_continuous_plan(
                levels,
                first_value,
                last_record_density=self._v5g_state.last_print_record_density,
                recent_completion=(time.time() - self._v5g_state.last_complete_time) < 50,
            )
        elif helper_kind == "pd01":
            plan = pd01_continuous_plan(temperature_c, levels, first_value)
        elif helper_kind == "mx10":
            plan = mx10_continuous_plan(temperature_c, levels, first_value)
        else:
            plan = V5GContinuousPlan(
                begin_density_value=min(levels.middle, first_value),
                unchanged_packet_count=4,
                minimum_density_value=95,
                update_first_packet=min(levels.middle, first_value) != first_value,
            )

        rewrite_map: Dict[int, int] = {}
        leading_value = plan.begin_density_value if plan.update_first_packet else first_value
        leading_count = min(len(density_indexes), plan.unchanged_packet_count)
        for density_index in density_indexes[:leading_count]:
            current_value = self._extract_v5g_density_value(packets[density_index])
            if current_value != leading_value:
                rewrite_map[density_index] = leading_value

        remaining = max(0, len(density_indexes) - plan.unchanged_packet_count)
        sequence: list[int] = []
        if remaining > 0:
            if helper_kind == "pd01":
                sequence = pd01_continuous_series(leading_value, remaining)
            elif helper_kind == "mx10":
                sequence = mx10_continuous_series(
                    leading_value,
                    remaining,
                    minimum_value=plan.minimum_density_value,
                )
            else:
                sequence = v5g_continuous_series(
                    leading_value,
                    remaining,
                    clamp_low_70=plan.clamp_low_70,
                )

        for offset, density_index in enumerate(density_indexes[plan.unchanged_packet_count:]):
            if offset >= len(sequence):
                break
            current_value = self._extract_v5g_density_value(packets[density_index])
            if current_value != sequence[offset]:
                rewrite_map[density_index] = sequence[offset]

        final_density = sequence[-1] if sequence else leading_value
        self._v5g_state.last_print_record_copies = len(density_indexes)
        self._v5g_state.last_print_record_density = final_density
        self._report_debug(
            f"V5G continuous density helper kind={runtime_context.helper_kind} "
            f"count={len(density_indexes)} first={leading_value} "
            f"temp={self._v5g_state.temperature_c}"
        )
        return rewrite_map

    def _v5g_temperature_for_helper(self) -> int:
        return self._v5g_state.temperature_c

    @staticmethod
    def _select_v5g_levels(
        runtime_context: V5GDynamicRuntimeContext,
        is_text: bool,
    ) -> DensityLevels | None:
        return runtime_context.text_levels if is_text else runtime_context.image_levels

    def _mode_before_packet_index(self, packets: list[bytes], packet_index: int) -> bool:
        is_text = self._v5g_state.last_print_mode_is_text
        for packet in packets[:packet_index]:
            if self._extract_prefixed_opcode(packet) == 0xBE:
                is_text = self._extract_v5g_print_mode(packet)
        return is_text

    def _split_prefixed_packets(self, data: bytes) -> list[bytes] | None:
        packets: list[bytes] = []
        offset = 0
        while offset < len(data):
            packet_len = prefixed_packet_length(data, offset, self._protocol_family)
            if packet_len is None:
                return None
            packets.append(data[offset : offset + packet_len])
            offset += packet_len
        return packets

    def _extract_v5g_density_value(self, packet: bytes) -> int | None:
        payload = self._extract_prefixed_payload(packet)
        if payload is None or len(payload) != 2:
            return None
        return payload[0] | (payload[1] << 8)

    def _extract_v5g_print_mode(self, packet: bytes) -> bool:
        payload = self._extract_prefixed_payload(packet)
        if not payload:
            return False
        return payload[0] == 0x01

    def _track_v5c_outgoing_query_status(self, data: bytes) -> None:
        if self._protocol_family is not ProtocolFamily.V5C:
            return
        query_seen = V5C_QUERY_STATUS_PACKET in data
        self._v5c_state.query_status_in_flight = query_seen
        if query_seen:
            self._report_debug("V5C query status armed")

    async def _send_split(
        self,
        client: Any,
        data: bytes,
        *,
        mtu_size: int,
        timeout: float,
    ) -> None:
        if not self.bindings.bulk_write_char:
            raise RuntimeError("Bulk write characteristic not found")

        split = split_prefixed_bulk_stream(
            data,
            self._protocol_family,
            self._transport_profile.split_tail_packets,
        )
        v5x_context = self._build_v5x_job_context(split)
        density_updated_for_job = False
        # Split-bulk families send framed control packets on one endpoint and
        # stream the raster payload over another.
        cmd_response = self._resolve_response_mode(
            self.bindings.write_char,
            self.bindings.write_selection_strategy,
            self.bindings.write_response_preference,
        )
        self._report_debug(
            f"split write response={cmd_response} cmd_char={self.bindings.write_char_uuid} "
            f"bulk_char={self.bindings.bulk_write_char_uuid or '<missing>'} "
            f"notify_char={self.bindings.notify_char_uuid or '<missing>'}"
        )

        for packet in split.commands:
            packet, density_updated = self._prepare_command_packet(packet, v5x_context)
            if packet is None:
                continue
            density_updated_for_job = density_updated_for_job or density_updated
            opcode = self._extract_prefixed_opcode(packet)
            if self._protocol_family is ProtocolFamily.V5X and opcode in (0xA2, 0xA9):
                await self._wait_for_start_ready(timeout)
            if self._protocol_family is ProtocolFamily.V5X and opcode == 0xA9:
                delay_ms = self._compute_v5x_start_delay_ms(
                    v5x_context,
                    density_updated=density_updated_for_job,
                )
                if delay_ms > 0:
                    await asyncio.sleep(delay_ms / 1000.0)
            ack_event = self._arm_command_ack(packet)
            try:
                await self._write_chunks(
                    client,
                    self.bindings.write_char,
                    packet,
                    response=cmd_response,
                    chunk_size=min(mtu_size, self._transport_profile.standard_chunk_cap),
                    delay_seconds=self._transport_profile.standard_write_delay_ms / 1000.0,
                    timeout=timeout,
                )
                if ack_event is not None:
                    await self._wait_for_command_ack(opcode, ack_event, timeout)
            except Exception:
                self._clear_command_ack(opcode)
                raise

        if split.bulk_payload:
            bulk_response = self._resolve_response_mode(
                self.bindings.bulk_write_char,
                "preferred_uuid",
                False,
            )
            await self._write_chunks(
                client,
                self.bindings.bulk_write_char,
                split.bulk_payload,
                response=bulk_response,
                chunk_size=min(mtu_size, self._transport_profile.bulk_chunk_cap),
                delay_seconds=self._transport_profile.bulk_write_delay_ms / 1000.0,
                timeout=timeout,
                wait_for_flow=self._transport_profile.flow_control is not None,
            )

        for packet in split.trailing_commands:
            await self._write_chunks(
                client,
                self.bindings.write_char,
                packet,
                response=cmd_response,
                chunk_size=min(mtu_size, self._transport_profile.standard_chunk_cap),
                delay_seconds=self._transport_profile.standard_write_delay_ms / 1000.0,
                timeout=timeout,
            )

    async def _write_chunks(
        self,
        client: Any,
        char: Any,
        data: bytes,
        *,
        response: bool,
        chunk_size: int,
        delay_seconds: float,
        timeout: float,
        wait_for_flow: bool = False,
    ) -> None:
        for offset in range(0, len(data), chunk_size):
            if wait_for_flow:
                await self._wait_for_flow(timeout)
            chunk = data[offset : offset + chunk_size]
            await client.write_gatt_char(char, chunk, response=response)
            if delay_seconds:
                await asyncio.sleep(delay_seconds)

    async def _wait_for_flow(self, timeout: float) -> None:
        deadline = asyncio.get_running_loop().time() + timeout
        while not self.flow_can_write:
            if asyncio.get_running_loop().time() > deadline:
                raise TimeoutError("Timed out waiting for BLE flow-control resume")
            await asyncio.sleep(0.01)

    async def _wait_for_command_ack(
        self,
        opcode: Optional[int],
        event: asyncio.Event,
        timeout: float,
    ) -> None:
        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
            self._validate_command_ack(opcode)
        finally:
            if opcode is not None and self._command_ack_events.get(opcode) is event:
                self._command_ack_events.pop(opcode, None)
                if opcode == 0xA7 and self._start_ready_event is not None and not self._start_ready_event.is_set():
                    self._start_ready_event = None

    async def _wait_for_start_ready(self, timeout: float) -> None:
        if self._start_ready_event is None:
            return
        event = self._start_ready_event
        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
        finally:
            if self._start_ready_event is event:
                self._start_ready_event = None

    async def _wait_for_connect_info(self, timeout: float) -> None:
        if self._connect_info_event is None:
            return
        event = self._connect_info_event
        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
        except TimeoutError:
            self._report_debug("V5X connect info was not received during the initial settle window")
        finally:
            if self._connect_info_event is event:
                self._connect_info_event = None

    def handle_notification(self, payload: bytes) -> None:
        flow_control = self._transport_profile.flow_control
        if flow_control is not None:
            # Pause/resume packets gate writes for families that require
            # application-level flow control.
            if payload in flow_control.pause_packets:
                self.flow_can_write = False
                self._report_debug(f"flow pause: {payload.hex()}")
                return
            if payload in flow_control.resume_packets:
                self.flow_can_write = True
                self._report_debug(f"flow resume: {payload.hex()}")
                return

        if self._protocol_family is ProtocolFamily.V5X:
            opcode = self._extract_prefixed_opcode(payload)
            if opcode == 0xA7:
                self._update_v5x_info_from_a7(payload)
                self._release_command_ack(0xA7)
            elif opcode == 0xA1:
                self._update_v5x_status(payload)
            elif opcode == 0xA3:
                self._mark_v5x_status_poll_ack()
            elif opcode == 0xA6:
                self._schedule_get_serial()
            elif opcode == 0xAA:
                self._release_start_ready()
            elif opcode == 0xA9:
                status = self._extract_v5x_status_byte(payload)
                self._v5x_state.last_a9_status = status
                self._release_command_ack(0xA9)
            elif opcode == 0xAB:
                self._update_v5x_ab_status(payload)
            elif opcode == 0xB0:
                self._update_v5x_head_type_from_b0(payload)
            elif opcode == 0xB1:
                self._update_v5x_info_from_b1(payload)
                self._release_connect_info()
            elif opcode == 0xB2:
                self._schedule_status_poll()
            elif opcode == 0xB3:
                self._mark_v5x_sign_request()
        elif self._protocol_family is ProtocolFamily.V5C:
            opcode = self._extract_prefixed_opcode(payload)
            if opcode == 0xA1:
                self._update_v5c_status(payload)
            elif opcode == 0xAA:
                self._update_v5c_max_print_height(payload)
            elif opcode in (0xA8, 0xA9):
                self._update_v5c_compatibility(payload, opcode)
        elif self._protocol_family is ProtocolFamily.V5G:
            opcode = self._extract_prefixed_opcode(payload)
            if opcode == 0xA3:
                self._update_v5g_status(payload)
            elif opcode == 0xD2:
                self._update_v5g_d2_status(payload)
            elif opcode == 0xD3:
                self._update_v5g_temperature(payload)

        self._report_debug(f"BLE notify: {payload.hex()}")

    def _arm_command_ack(self, packet: bytes) -> Optional[asyncio.Event]:
        if self._protocol_family is not ProtocolFamily.V5X:
            return None
        opcode = self._extract_prefixed_opcode(packet)
        if opcode not in (0xA7, 0xA9):
            return None
        if opcode == 0xA7:
            self._start_ready_event = asyncio.Event()
        event = asyncio.Event()
        self._command_ack_events[opcode] = event
        return event

    def _release_command_ack(self, opcode: int) -> None:
        event = self._command_ack_events.pop(opcode, None)
        if event is not None and not event.is_set():
            event.set()
            self._report_debug(f"command ack: 0x{opcode:02x}")

    def _clear_command_ack(self, opcode: Optional[int]) -> None:
        if opcode is None:
            return
        self._command_ack_events.pop(opcode, None)
        if opcode == 0xA7 and self._start_ready_event is not None and not self._start_ready_event.is_set():
            self._start_ready_event = None

    def _release_start_ready(self) -> None:
        if self._start_ready_event is None or self._start_ready_event.is_set():
            return
        self._start_ready_event.set()
        self._report_debug("start ready: 0xaa")

    def _release_connect_info(self) -> None:
        if self._connect_info_event is None or self._connect_info_event.is_set():
            return
        self._connect_info_event.set()
        self._report_debug("connect info ready: 0xb1")

    def _schedule_status_poll(self) -> None:
        if self._pending_status_poll is not None and not self._pending_status_poll.done():
            return
        if not self._client or not self.bindings.write_char:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._pending_status_poll = loop.create_task(self._send_v5x_status_poll())
        self._pending_status_poll.add_done_callback(lambda _task: setattr(self, "_pending_status_poll", None))

    def _schedule_v5g_reset_density(self, value: int) -> None:
        if self._pending_v5g_reset is not None and not self._pending_v5g_reset.done():
            return
        if not self._client or not self.bindings.write_char:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._pending_v5g_reset = loop.create_task(self._send_v5g_density_reset(value))
        self._pending_v5g_reset.add_done_callback(
            lambda _task: setattr(self, "_pending_v5g_reset", None)
        )

    def _schedule_get_serial(self) -> None:
        if self._pending_get_serial is not None and not self._pending_get_serial.done():
            return
        if not self._client or not self.bindings.write_char:
            return
        if 0xA7 in self._command_ack_events or self._start_ready_event is not None:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._pending_get_serial = loop.create_task(
            self._send_v5x_command(V5X_GET_SERIAL_PACKET)
        )
        self._pending_get_serial.add_done_callback(lambda _task: setattr(self, "_pending_get_serial", None))

    async def _send_v5x_status_poll(self) -> None:
        await asyncio.sleep(0.7)
        await self._send_v5x_command(V5X_STATUS_POLL_PACKET)
        self._report_debug("scheduled status poll: 0xa3")

    async def _send_v5x_command(self, packet: bytes) -> None:
        if not self._client or not self.bindings.write_char:
            return
        response = self._resolve_response_mode(
            self.bindings.write_char,
            self.bindings.write_selection_strategy,
            self.bindings.write_response_preference,
        )
        await self._write_chunks(
            self._client,
            self.bindings.write_char,
            packet,
            response=response,
            chunk_size=20,
            delay_seconds=0.0,
            timeout=1.0,
        )

    def _cancel_pending_get_serial(self) -> None:
        if self._pending_get_serial is None:
            return
        self._pending_get_serial.cancel()
        self._pending_get_serial = None

    def _cancel_pending_status_poll(self) -> None:
        if self._pending_status_poll is None:
            return
        self._pending_status_poll.cancel()
        self._pending_status_poll = None

    def _cancel_pending_v5g_reset(self) -> None:
        if self._pending_v5g_reset is None:
            return
        self._pending_v5g_reset.cancel()
        self._pending_v5g_reset = None

    async def _send_v5g_density_reset(self, value: int) -> None:
        if not self._client or not self.bindings.write_char:
            return
        response = self._resolve_response_mode(
            self.bindings.write_char,
            self.bindings.write_selection_strategy,
            self.bindings.write_response_preference,
        )
        packet = make_packet(
            0xF2,
            int(value).to_bytes(2, "little", signed=False),
            ProtocolFamily.V5G,
        )
        await self._write_chunks(
            self._client,
            self.bindings.write_char,
            packet,
            response=response,
            chunk_size=min(180, self._transport_profile.standard_chunk_cap),
            delay_seconds=self._transport_profile.standard_write_delay_ms / 1000.0,
            timeout=0.2,
        )
        self._v5g_state.last_density_value = value

    def _validate_command_ack(self, opcode: Optional[int]) -> None:
        if self._protocol_family is not ProtocolFamily.V5X or opcode != 0xA9:
            return
        status = self._v5x_state.last_a9_status
        if status is None:
            raise RuntimeError("V5X start print response did not include a status byte")
        if status != 0x00:
            raise RuntimeError(f"V5X start print was rejected (status=0x{status:02x})")

    def _prepare_command_packet(
        self,
        packet: bytes,
        v5x_context: Optional[_V5XJobContext],
    ) -> Tuple[Optional[bytes], bool]:
        if self._protocol_family is not ProtocolFamily.V5X:
            return packet, False
        opcode = self._extract_prefixed_opcode(packet)
        if opcode != 0xA2:
            return packet, False

        payload = self._extract_prefixed_payload(packet)
        if payload is None:
            return packet, False

        adjusted_payload = self._adjust_v5x_density_payload(payload, v5x_context)
        if adjusted_payload != payload:
            packet = make_packet(0xA2, adjusted_payload, self._protocol_family)
            payload = adjusted_payload

        # V5X keeps printer concentration as connection state and only reapplies it
        # when the selected value actually changes.
        if self._v5x_state.last_density_payload == payload:
            self._report_debug(f"skipping unchanged V5X density packet: {payload.hex()}")
            return None, False

        self._v5x_state.last_density_payload = payload
        return packet, True

    def _build_v5x_job_context(self, split) -> Optional[_V5XJobContext]:
        if self._protocol_family is not ProtocolFamily.V5X:
            return None
        is_gray = False
        for packet in split.commands:
            if self._extract_prefixed_opcode(packet) != 0xA9:
                continue
            payload = self._extract_prefixed_payload(packet)
            if payload is None:
                continue
            if len(payload) == 2:
                is_gray = True
            elif len(payload) >= 6:
                is_gray = payload[2:6] == V5X_GRAY_MODE_SUFFIX
            break
        coverage_ratio = 0.0
        if split.bulk_payload and not is_gray:
            total_bits = len(split.bulk_payload) * 8
            if total_bits > 0:
                black_bits = sum(chunk.bit_count() for chunk in split.bulk_payload)
                coverage_ratio = black_bits / total_bits
        return _V5XJobContext(coverage_ratio=coverage_ratio, is_gray=is_gray)

    def _adjust_v5x_density_payload(
        self,
        payload: bytes,
        v5x_context: Optional[_V5XJobContext],
    ) -> bytes:
        if len(payload) != 1 or v5x_context is None:
            return payload
        user_density = payload[0]
        temperature_c = self._v5x_state.temperature_c or 0
        coverage_ratio = v5x_context.coverage_ratio
        head_type = self._v5x_state.print_head_type
        is_gray = v5x_context.is_gray

        if is_gray:
            target_density = self._v5x_gray_density_target(
                temperature_c,
                user_density,
                head_type,
            )
        else:
            target_density = self._v5x_dot_density_target(
                temperature_c,
                user_density,
                head_type,
                coverage_ratio,
            )
        target_density = max(0, min(user_density, target_density))
        if target_density != user_density:
            self._report_debug(
                "V5X density adjusted "
                f"head_type={head_type} temp={temperature_c} "
                f"coverage={coverage_ratio:.3f} user={user_density} target={target_density}"
            )
        return bytes([target_density])

    def _compute_v5x_start_delay_ms(
        self,
        v5x_context: Optional[_V5XJobContext],
        *,
        density_updated: bool,
    ) -> int:
        if self._protocol_family is not ProtocolFamily.V5X or v5x_context is None:
            return 0
        if self._v5x_state.print_head_type == "gaoya" and v5x_context.coverage_ratio > 0.4:
            return 200
        if density_updated:
            return 60
        return 0

    @staticmethod
    def _v5x_coverage_band(coverage_ratio: float) -> int:
        if coverage_ratio <= 0.4:
            return 1
        if coverage_ratio < 0.5:
            return 2
        if coverage_ratio < 0.7:
            return 3
        return 4

    def _v5x_gray_density_target(
        self,
        temperature_c: int,
        user_density: int,
        head_type: str,
    ) -> int:
        if head_type == "gaoya":
            thresholds = (
                (70, 56),
                (65, 65),
                (60, 75),
                (55, 80),
                (50, 85),
            )
        else:
            thresholds = (
                (70, 56),
                (65, 60),
                (60, 65),
                (55, 75),
                (50, 80),
            )
        for threshold, value in thresholds:
            if temperature_c >= threshold:
                return min(user_density, value)
        return user_density

    def _v5x_dot_density_target(
        self,
        temperature_c: int,
        user_density: int,
        head_type: str,
        coverage_ratio: float,
    ) -> int:
        # TiMini Print always sends one logical print job at a time, so keep the
        # higher single-job threshold that avoids over-adjusting at lower temperatures.
        if temperature_c <= 60:
            return user_density
        band = self._v5x_coverage_band(coverage_ratio)
        if head_type == "gaoya":
            if temperature_c < 65:
                values = (48, 15, 15, 10)
            elif temperature_c < 70:
                values = (36, 9, 5, 5)
            else:
                values = (22, 5, 3, 3)
        else:
            if temperature_c <= 65:
                values = (60, 50, 50, 30)
            elif temperature_c <= 70:
                values = (50, 40, 40, 20)
            else:
                values = (40, 30, 30, 10)
        return min(user_density, values[band - 1])

    def _update_v5x_info_from_a7(self, payload: bytes) -> None:
        raw = self._extract_prefixed_payload(payload)
        if raw is None:
            return
        self._v5x_state.last_a7_payload = raw
        serial_hex = raw[:6].hex()
        self._v5x_state.device_serial = serial_hex
        if serial_hex:
            self._v5x_state.serial_valid = serial_hex not in {"000000000000", "ffffffffffff"}
        else:
            self._v5x_state.serial_valid = False
        self._refresh_v5x_compatibility_mode()
        self._report_debug(
            "V5X serial "
            f"serial={self._v5x_state.device_serial or '<empty>'} "
            f"valid={self._v5x_state.serial_valid}"
        )

    def _refresh_v5x_compatibility_mode(self) -> None:
        compat = self._v5x_state.compatibility
        compat.checked = False
        compat.confirmed = None
        compat.last_result_code = None
        compat.backend_write_cmd = b""
        if self._v5x_state.serial_valid is False:
            compat.mode = "get_sn"
        elif self._v5x_state.serial_valid is True:
            compat.mode = "auth"
        else:
            compat.mode = "unknown"
        self._report_debug(f"V5X compatibility mode: {compat.mode}")

    def build_v5x_compat_request(
        self,
        *,
        ble_name: str,
        ble_address: str,
        ble_model: str = "V5X",
    ) -> Optional[Dict[str, str]]:
        if self._protocol_family is not ProtocolFamily.V5X:
            return None
        mode = self._v5x_state.compatibility.mode
        if mode not in {"get_sn", "auth"}:
            return None
        serial = self._v5x_state.device_serial or "0"
        return {
            "mode": mode,
            "ble_name": ble_name,
            "ble_address": ble_address,
            "ble_sn": serial,
            "ble_model": ble_model,
        }

    def apply_v5x_compat_result(
        self,
        *,
        mode: str,
        result_code: Optional[int],
        write_cmd: bytes | None = None,
    ) -> None:
        if self._protocol_family is not ProtocolFamily.V5X:
            return
        compat = self._v5x_state.compatibility
        compat.mode = mode
        compat.checked = True
        compat.last_result_code = result_code
        compat.backend_write_cmd = write_cmd or b""
        compat.confirmed = None if result_code is None else result_code != -2
        if result_code == -2:
            self._reporter.warning(
                short="V5X compatibility check failed",
                detail="Continuing without server confirmation for this device session.",
            )
            return
        if write_cmd:
            self._report_debug(
                "V5X compatibility write command captured "
                f"length={len(write_cmd)} mode={mode}"
            )

    def _update_v5g_status(self, payload: bytes) -> None:
        raw = self._extract_prefixed_payload(payload)
        if not raw:
            return
        status = raw[0]
        if status == 0x00:
            self._v5g_state.didian_status = False
        elif status == 0x08:
            self._v5g_state.didian_status = True
        elif status == 0x04:
            self._v5g_state.d2_status = True
        self._report_debug(
            f"V5G status status=0x{status:02x} didian={self._v5g_state.didian_status} "
            f"d2={self._v5g_state.d2_status}"
        )

    def _update_v5g_d2_status(self, payload: bytes) -> None:
        raw = self._extract_prefixed_payload(payload)
        if raw is None:
            return
        self._v5g_state.d2_status = True
        self._report_debug("V5G D2 status received")

    def _update_v5g_temperature(self, payload: bytes) -> None:
        raw = self._extract_prefixed_payload(payload)
        if not raw:
            return
        previous = self._v5g_state.temperature_c
        self._v5g_state.temperature_c = -1 if raw[0] == 0xFF else raw[0]
        if (
            self._v5g_state.helper_kind == "pd01"
            and not self._v5g_state.printing
            and (
                self._v5g_state.temperature_c == -1
                or (
                    previous >= 0
                    and self._v5g_state.temperature_c < previous
                    and self._v5g_state.temperature_c <= 60
                )
            )
        ):
            self._schedule_v5g_reset_density(120)
        self._report_debug(f"V5G temperature={self._v5g_state.temperature_c}")

    def _extract_prefixed_opcode(self, payload: bytes) -> Optional[int]:
        prefix = self._protocol_family.packet_prefix
        if len(payload) < len(prefix) + 1 or payload[: len(prefix)] != prefix:
            return None
        return payload[len(prefix)]

    def _extract_prefixed_payload(self, packet: bytes) -> Optional[bytes]:
        prefix = self._protocol_family.packet_prefix
        if len(packet) < len(prefix) + 6 or packet[: len(prefix)] != prefix:
            return None
        payload_length = packet[len(prefix) + 2] | (packet[len(prefix) + 3] << 8)
        payload_start = len(prefix) + 4
        payload_end = payload_start + payload_length
        if payload_end + 2 > len(packet):
            return None
        return packet[payload_start:payload_end]

    def _extract_v5x_status_byte(self, payload: bytes) -> Optional[int]:
        if self._protocol_family is not ProtocolFamily.V5X:
            return None
        raw = self._extract_prefixed_payload(payload)
        if raw:
            return raw[0]
        prefix = self._protocol_family.packet_prefix
        if len(payload) < len(prefix) + 2 or payload[: len(prefix)] != prefix:
            return None
        return payload[len(prefix) + 1]

    def _update_v5x_status(self, payload: bytes) -> None:
        raw = self._extract_prefixed_payload(payload)
        if raw is None or len(raw) < 8:
            return
        self._v5x_state.task_state = raw[0]
        self._v5x_state.task_state_name = self._v5x_task_state_name(raw[0])
        self._v5x_state.battery_level = raw[3]
        self._v5x_state.temperature_c = raw[4]
        self._v5x_state.error_group = raw[6]
        self._v5x_state.error_code = raw[7]
        self._handle_v5x_error_state(raw[6], raw[7])
        self._report_debug(
            "V5X status "
            f"task=0x{raw[0]:02x} ({self._v5x_state.task_state_name}) "
            f"battery={raw[3]} temp={raw[4]} "
            f"error_group=0x{raw[6]:02x} error_code=0x{raw[7]:02x}"
        )

    @staticmethod
    def _v5x_task_state_name(task_state: int) -> str:
        if task_state == 0x00:
            return "normal"
        if task_state == 0x01:
            return "printing"
        if task_state == 0x02:
            return "feeding"
        if task_state == 0x03:
            return "retracting"
        return f"0x{task_state:02x}"

    def _handle_v5x_error_state(self, error_group: int, error_code: int) -> None:
        signature = (error_group, error_code)
        if signature == (0x00, 0x00):
            if self._v5x_state.last_error_signature not in (None, (0x00, 0x00)):
                self._report_debug("V5X printer error state cleared")
            self._v5x_state.last_error_signature = signature
            return
        if self._v5x_state.last_error_signature == signature:
            return
        self._v5x_state.last_error_signature = signature
        self._reporter.warning(
            short="V5X printer reported an error status",
            detail=(
                f"Task={self._v5x_state.task_state_name}, "
                f"error_group=0x{error_group:02x}, error_code=0x{error_code:02x}."
            ),
        )

    def _mark_v5x_status_poll_ack(self) -> None:
        self._v5x_state.status_poll_ack_seen = True
        self._report_debug("V5X status poll acknowledged: 0xa3")

    def _update_v5x_ab_status(self, payload: bytes) -> None:
        raw = self._extract_prefixed_payload(payload)
        if not raw:
            return
        self._v5x_state.last_ab_status = raw[-1]
        self._report_debug(f"V5X auxiliary status: 0x{raw[-1]:02x}")

    def _mark_v5x_sign_request(self) -> None:
        if self._v5x_state.mxw_sign_requested:
            return
        self._v5x_state.mxw_sign_requested = True
        self._reporter.warning(
            short="V5X printer requested an additional signing step",
            detail="Continuing without the optional signing command for this session.",
        )

    def _update_v5x_head_type_from_b0(self, payload: bytes) -> None:
        raw = self._extract_prefixed_payload(payload)
        if not raw:
            return
        value = raw[0]
        if value == 0x01:
            head_type = "gaoya"
        elif value == 0xFF:
            head_type = "weishibie"
        else:
            head_type = "diya"
        self._v5x_state.print_head_type = head_type
        self._report_debug(f"V5X print head type: {head_type}")

    def _update_v5x_info_from_b1(self, payload: bytes) -> None:
        raw = self._extract_prefixed_payload(payload)
        if not raw:
            return
        self._v5x_state.connect_info_received = True
        firmware = raw.decode("ascii", errors="ignore").rstrip("\x00")
        if firmware:
            self._v5x_state.firmware_version = firmware
            marker = firmware[-1]
            if marker == "2":
                self._v5x_state.print_head_type = "gaoya"
            elif marker == "1":
                self._v5x_state.print_head_type = "diya"
            else:
                self._v5x_state.print_head_type = "weishibie"
            self._report_debug(
                f"V5X firmware={self._v5x_state.firmware_version} "
                f"head_type={self._v5x_state.print_head_type}"
            )

    def _update_v5c_status(self, payload: bytes) -> None:
        raw = self._extract_prefixed_payload(payload)
        if not raw:
            return
        previous_status = self._v5c_state.status_code
        status = raw[0]
        self._v5c_state.status_code = status
        self._v5c_state.status_name = self._v5c_status_name(status)
        self._v5c_state.is_charging = status in (0x10, 0x11)
        if status == 0x80:
            self._v5c_state.print_complete_seen = False
        elif status == 0x00:
            if self._v5c_state.query_status_in_flight:
                self._v5c_state.query_status_in_flight = False
                self._report_debug("V5C query status acknowledged")
            elif previous_status == 0x80:
                self._v5c_state.print_complete_seen = True
                self._report_debug("V5C print complete")
        self._handle_v5c_status(status)
        self._report_debug(
            f"V5C status status=0x{status:02x} ({self._v5c_state.status_name})"
        )

    @staticmethod
    def _v5c_status_name(status: int) -> str:
        if status == 0x00:
            return "normal"
        if status == 0x80:
            return "printing"
        if status in (0x10, 0x11):
            return "charging"
        if status in (0x01, 0x02, 0x03):
            return "attention"
        if status == 0x04:
            return "overheat"
        if status == 0x08:
            return "low_power"
        return f"0x{status:02x}"

    def _handle_v5c_status(self, status: int) -> None:
        if status in (0x00, 0x80, 0x10, 0x11):
            if self._v5c_state.last_error_status is not None:
                self._report_debug("V5C printer error state cleared")
            self._v5c_state.last_error_status = None
            return
        if self._v5c_state.last_error_status == status:
            return
        self._v5c_state.last_error_status = status
        if status in (0x01, 0x02, 0x03):
            short = "V5C printer reported an attention state"
        elif status == 0x04:
            short = "V5C printer reported an overheat state"
        elif status == 0x08:
            short = "V5C printer reported a low-power state"
        else:
            short = "V5C printer reported an error status"
        self._reporter.warning(
            short=short,
            detail=f"status=0x{status:02x} ({self._v5c_state.status_name}).",
        )

    def _update_v5c_max_print_height(self, payload: bytes) -> None:
        raw = self._extract_prefixed_payload(payload)
        if raw is None or len(raw) < 2:
            return
        height = int.from_bytes(raw[:2], "little")
        self._v5c_state.max_print_height = height
        self._report_debug(f"V5C max print height: {height}")

    def _update_v5c_compatibility(self, payload: bytes, opcode: int) -> None:
        raw = self._extract_prefixed_payload(payload)
        if raw is None:
            return
        self._v5c_state.last_auth_payload = raw
        compat = self._v5c_state.compatibility
        compat.last_trigger_opcode = opcode
        compat.last_trigger_packet = payload
        if opcode == 0xA8:
            self._v5c_state.device_serial = ""
            self._v5c_state.serial_valid = None
            self._set_v5c_compatibility_mode("to_auth")
            compat.request_pending = True
            self._report_debug("V5C compatibility trigger opcode=0xa8 mode=to_auth")
            return

        serial_hex = raw[:8].hex()
        self._v5c_state.device_serial = serial_hex
        if serial_hex:
            self._v5c_state.serial_valid = int(serial_hex, 16) != 0
        else:
            self._v5c_state.serial_valid = False
        self._refresh_v5c_compatibility_mode()
        compat.request_pending = True
        self._report_debug(
            "V5C compatibility trigger "
            f"opcode=0x{opcode:02x} serial={serial_hex or '<empty>'} "
            f"valid={self._v5c_state.serial_valid}"
        )

    def _refresh_v5c_compatibility_mode(self) -> None:
        compat = self._v5c_state.compatibility
        if self._v5c_state.serial_valid is False:
            mode = "get_sn"
        elif self._v5c_state.serial_valid is True:
            mode = "auth"
        else:
            mode = "unknown"
        self._set_v5c_compatibility_mode(mode)

    def _set_v5c_compatibility_mode(self, mode: str) -> None:
        compat = self._v5c_state.compatibility
        compat.mode = mode
        compat.request_pending = False
        compat.checked = False
        compat.confirmed = None
        compat.last_result_code = None
        compat.backend_write_cmd = b""
        self._report_debug(f"V5C compatibility mode: {compat.mode}")

    def build_v5c_compat_request(
        self,
        *,
        ble_name: str,
        ble_address: str,
        ble_model: str = "V5C",
    ) -> Optional[Dict[str, str]]:
        if self._protocol_family is not ProtocolFamily.V5C:
            return None
        compat = self._v5c_state.compatibility
        if not compat.request_pending:
            return None
        mode = compat.mode
        if mode == "to_auth":
            packet = compat.last_trigger_packet
            if not packet:
                return None
            return {
                "mode": mode,
                "ble_name": ble_name,
                "ble_address": ble_address,
                "ble_sn": packet.hex(),
                "ble_model": ble_model,
            }
        if mode in {"get_sn", "auth"}:
            serial = self._v5c_state.device_serial or "0"
            return {
                "mode": mode,
                "ble_name": ble_name,
                "ble_address": ble_address,
                "ble_sn": serial,
                "ble_model": ble_model,
            }
        return None

    def apply_v5c_compat_result(
        self,
        *,
        mode: str,
        result_code: Optional[int],
        write_cmd: bytes | None = None,
    ) -> None:
        if self._protocol_family is not ProtocolFamily.V5C:
            return
        compat = self._v5c_state.compatibility
        compat.mode = mode
        compat.request_pending = False
        compat.checked = True
        compat.last_result_code = result_code
        compat.backend_write_cmd = write_cmd or b""
        compat.confirmed = None if result_code is None else result_code != -2
        if result_code == -2:
            self._reporter.warning(
                short="V5C compatibility check failed",
                detail="Continuing without server confirmation for this device session.",
            )
            return
        if write_cmd:
            self._report_debug(
                "V5C compatibility write command captured "
                f"length={len(write_cmd)} mode={mode}"
            )

    @staticmethod
    def _find_characteristic_by_uuid(
        services: Iterable[object],
        char_uuid: str,
        *,
        preferred_service_uuid: str = "",
    ) -> Optional[Any]:
        """Resolve a known characteristic UUID, preferring one service when requested."""
        target = _BleWriteEndpointResolver._normalize_uuid(char_uuid)
        preferred_service = _BleWriteEndpointResolver._normalize_uuid(preferred_service_uuid)
        if preferred_service:
            for service in services:
                service_uuid = _BleWriteEndpointResolver._normalize_uuid(getattr(service, "uuid", ""))
                if service_uuid != preferred_service:
                    continue
                for characteristic in getattr(service, "characteristics", []):
                    if (
                        _BleWriteEndpointResolver._normalize_uuid(
                            getattr(characteristic, "uuid", "")
                        )
                        == target
                    ):
                        return characteristic
        for service in services:
            for characteristic in getattr(service, "characteristics", []):
                if (
                    _BleWriteEndpointResolver._normalize_uuid(getattr(characteristic, "uuid", ""))
                    == target
                ):
                    return characteristic
        return None

    @classmethod
    def find_notify_characteristic(cls, services: Iterable[object]) -> Optional[Any]:
        preferred: List[Tuple[str, str, Any]] = []
        generic: List[Tuple[str, str, Any]] = []
        for service in services:
            service_uuid = _BleWriteEndpointResolver._normalize_uuid(getattr(service, "uuid", ""))
            for characteristic in getattr(service, "characteristics", []):
                props = {
                    str(item).strip().lower() for item in getattr(characteristic, "properties", [])
                }
                if "notify" not in props and "indicate" not in props:
                    continue
                char_uuid = _BleWriteEndpointResolver._normalize_uuid(
                    getattr(characteristic, "uuid", "")
                )
                candidate = (service_uuid, char_uuid, characteristic)
                if _BleWriteEndpointResolver._uuid_is_preferred(
                    char_uuid,
                    _BleWriteEndpointResolver._PREFERRED_NOTIFY_UUIDS,
                    _BleWriteEndpointResolver._PREFERRED_NOTIFY_SHORT,
                ):
                    preferred.append(candidate)
                else:
                    generic.append(candidate)
        candidates = sorted(preferred or generic, key=lambda item: (item[0], item[1]))
        return candidates[0][2] if candidates else None

    def _resolve_response_mode(
        self,
        characteristic: Any,
        strategy: str,
        response_preference: Optional[bool],
    ) -> bool:
        return self._write_resolver.resolve_response_mode(
            getattr(characteristic, "properties", []),
            strategy,
            response_preference,
        )

    def _report_debug(self, message: str) -> None:
        self._reporter.debug(short="BLE", detail=message)
