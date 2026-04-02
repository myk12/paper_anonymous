#!/usr/bin/env python3
"""Shared timing helpers for Sync-DCN schedule construction."""

from __future__ import annotations

import math
from typing import Any, Dict, Iterable, List, Tuple


def parse_int(value: Any, field_name: str = "value") -> int:
    """Parse an integer from either a numeric literal or a string."""

    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return int(value, 0)
    raise TypeError(f"{field_name} must be int-compatible, got {type(value)!r}")


def parse_float(value: Any, field_name: str = "value") -> float:
    """Parse a floating-point number from either numeric or string input."""

    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        return float(value)
    raise TypeError(f"{field_name} must be float-compatible, got {type(value)!r}")


def resolve_ai_plane_timing(
    *,
    workload: Dict[str, Any],
    topology: Dict[str, Any] | None,
    plane: str,
) -> Dict[str, Any]:
    """Resolve the timing model for one AI plane.

    The timing model currently supports two modes:

    - fixed:
      use an explicit window duration from the workload or topology
    - derived:
      derive the epoch duration from traffic volume and topology properties
    """

    plane_spec = {}
    if isinstance(topology, dict):
        raw_plane_spec = topology.get(plane, {})
        if isinstance(raw_plane_spec, dict):
            plane_spec = raw_plane_spec

    explicit_mode = workload.get("epoch_duration_model", plane_spec.get("epoch_duration_model"))
    if explicit_mode is None:
        mode = "derived" if "port_rate_gbps" in plane_spec else "fixed"
    else:
        mode = str(explicit_mode).strip().lower()

    fixed_window_duration_ns = workload.get(
        "window_duration_ns",
        plane_spec.get("default_window_duration_ns", plane_spec.get("window_duration_ns", 0)),
    )

    return {
        "mode": mode,
        "fixed_window_duration_ns": (
            parse_int(fixed_window_duration_ns, "window_duration_ns")
            if fixed_window_duration_ns not in (None, "")
            else 0
        ),
        "reconfiguration_time_ns": parse_int(
            workload.get(
                "reconfiguration_time_ns",
                plane_spec.get("reconfiguration_time_ns", 0),
            ),
            "reconfiguration_time_ns",
        ),
        "guard_band_ns": parse_int(
            workload.get(
                "guard_band_ns",
                plane_spec.get("guard_band_ns", 0),
            ),
            "guard_band_ns",
        ),
        "port_rate_gbps": parse_float(
            plane_spec.get("port_rate_gbps", 0.0),
            "port_rate_gbps",
        ),
        "tx_pipeline_ns": parse_int(
            plane_spec.get("tx_pipeline_ns", plane_spec.get("nic_pipeline_ns", 0)),
            "tx_pipeline_ns",
        ),
        "rx_pipeline_ns": parse_int(
            plane_spec.get("rx_pipeline_ns", plane_spec.get("nic_pipeline_ns", 0)),
            "rx_pipeline_ns",
        ),
        "fabric_latency_ns": parse_int(
            plane_spec.get("fabric_latency_ns", plane_spec.get("hop_delay_ns", 0)),
            "fabric_latency_ns",
        ),
        "nic_cycle_ns": parse_int(
            plane_spec.get("nic_cycle_ns", 0),
            "nic_cycle_ns",
        ),
    }


def estimate_edge_transfer_time_ns(
    *,
    packet_count: int,
    packet_len: int,
    gap_cycles: int,
    plane_timing: Dict[str, Any],
) -> int:
    """Estimate one edge's end-to-end transfer time.

    The model is intentionally simple and conservative:

    - packet_count * packet_len / port_rate gives the serialization budget
    - per-packet inter-send gaps use gap_cycles * nic_cycle_ns
    - tx/rx/fabric pipeline latencies are added once per burst
    """

    if packet_count <= 0:
        return 0

    mode = str(plane_timing.get("mode", "fixed")).strip().lower()
    if mode == "fixed":
        return parse_int(plane_timing["fixed_window_duration_ns"], "fixed_window_duration_ns")

    port_rate_gbps = float(plane_timing.get("port_rate_gbps", 0.0))
    if port_rate_gbps <= 0.0:
        raise ValueError("derived epoch duration model requires topology.<plane>.port_rate_gbps > 0")

    bits_total = packet_count * packet_len * 8
    serialization_time_ns = int(math.ceil(bits_total / port_rate_gbps))
    gap_time_ns = max(0, packet_count - 1) * gap_cycles * parse_int(
        plane_timing.get("nic_cycle_ns", 0),
        "nic_cycle_ns",
    )

    fixed_latency_ns = (
        parse_int(plane_timing.get("tx_pipeline_ns", 0), "tx_pipeline_ns")
        + parse_int(plane_timing.get("fabric_latency_ns", 0), "fabric_latency_ns")
        + parse_int(plane_timing.get("rx_pipeline_ns", 0), "rx_pipeline_ns")
    )

    return serialization_time_ns + gap_time_ns + fixed_latency_ns


def estimate_epoch_duration_ns(
    *,
    matching: Iterable[Tuple[int, int, int]],
    packet_len: int,
    gap_cycles: int,
    plane_timing: Dict[str, Any],
) -> int:
    """Estimate the duration of one matching epoch."""

    durations: List[int] = [
        estimate_edge_transfer_time_ns(
            packet_count=packet_count,
            packet_len=packet_len,
            gap_cycles=gap_cycles,
            plane_timing=plane_timing,
        )
        for _, _, packet_count in matching
    ]

    if not durations:
        return 0

    if str(plane_timing.get("mode", "fixed")).strip().lower() == "fixed":
        return parse_int(plane_timing["fixed_window_duration_ns"], "fixed_window_duration_ns")

    return max(durations)
