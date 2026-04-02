#!/usr/bin/env python3
"""Compile a high-level Sync-DCN experiment spec into low-level schedule JSON.

This tool is intentionally research-oriented and deliberately conservative.
It does not try to be a general online scheduler or a sophisticated optimizer.
Instead, it expands a small high-level experiment description into the exact
low-level JSON ABI already consumed by:

- sync_dcn_program.py
- the focused cocotb subsystem tests
- the current Sync-DCN host-side programming helpers

Supported high-level phase types:

- consensus_periodic
    Expand a round-based, leader-less consensus workload into one compiled
    execution window per round.  This matches the current RTL constraint that
    one logical consensus round maps to one executor window because the on-wire
    protocol currently uses current_window_id as the round identifier.

- ai_window
    Emit one explicit AI TX or AI RX execution window and automatically create
    the referenced AI trace-table entry.

- guard
    Emit a silent guard/reconfiguration hole in the execution table.

The output is a low-level JSON file containing:

- admin_bank
- activate_time_ns
- enable_subsystem
- enable_ai_replay
- execution_entries[]
- ai_trace_entries[]

That output can be fed directly into sync_dcn_program.py.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

UTILS_ROOT = Path(__file__).resolve().parents[1]
if str(UTILS_ROOT) not in sys.path:
    sys.path.append(str(UTILS_ROOT))

from host_control_plane.sync_dcn_host import SyncDcnAppId, SyncDcnFlags, SyncDcnOpcode, SyncDcnPlaneId

try:
    import yaml  # type: ignore
except ImportError:  # pragma: no cover - optional dependency
    yaml = None


APP_ID_TO_NAME = {
    SyncDcnAppId.NONE: "none",
    SyncDcnAppId.CONSENSUS: "consensus",
    SyncDcnAppId.AI_REPLAY: "ai_replay",
}

PLANE_NAME_MAP = {
    "eps": SyncDcnPlaneId.EPS,
    "ocs": SyncDcnPlaneId.OCS,
}

PLANE_ID_TO_NAME = {
    SyncDcnPlaneId.EPS: "eps",
    SyncDcnPlaneId.OCS: "ocs",
}

OPCODE_TO_NAME = {
    SyncDcnOpcode.IDLE: "idle",
    SyncDcnOpcode.GUARD: "guard",
    SyncDcnOpcode.CONS_TX: "cons_tx",
    SyncDcnOpcode.CONS_RX: "cons_rx",
    SyncDcnOpcode.AI_TX: "ai_tx",
    SyncDcnOpcode.AI_RX: "ai_rx",
    SyncDcnOpcode.RECONFIG: "reconfig",
}


@dataclass
class CompileContext:
    """Mutable compile-time state shared across phases.

    The compiler assigns AI trace contexts incrementally so that the generated
    execution entries can reference a stable trace-table index.
    """

    next_ai_context_id: int = 0


def parse_int(value: Any, field_name: str = "value") -> int:
    """Parse an integer from either a numeric literal or a string."""

    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return int(value, 0)
    raise TypeError(f"{field_name} must be int-compatible, got {type(value)!r}")


def parse_plane(value: Any) -> int:
    """Accept either a symbolic plane name or a raw encoded value."""

    if isinstance(value, str):
        key = value.strip().lower()
        if key not in PLANE_NAME_MAP:
            raise ValueError(f"Unknown plane name {value!r}")
        return PLANE_NAME_MAP[key]
    return parse_int(value, "plane")


def load_spec(path: Path) -> Dict[str, Any]:
    """Load a high-level experiment spec from JSON or YAML."""

    text = path.read_text()
    suffix = path.suffix.lower()

    if suffix == ".json":
        data = json.loads(text)
    elif suffix in (".yaml", ".yml"):
        if yaml is None:
            raise RuntimeError("YAML input requires PyYAML to be installed")
        data = yaml.safe_load(text)
    else:
        raise ValueError(f"Unsupported input extension: {path.suffix}")

    if not isinstance(data, dict):
        raise ValueError("Top-level experiment spec must be an object/mapping")

    return data


def make_execution_entry(
    *,
    start_time_ns: int,
    end_time_ns: int,
    context_id: int,
    opcode: int,
    plane_id: int,
    app_id: int,
    target_port: int = 0,
    queue_id: int = 0,
    flags: int = 0,
    dst_node_id: int = 0,
    flow_id: int = 0,
) -> Dict[str, Any]:
    """Create one low-level execution entry in the JSON ABI format."""

    return {
        "start_time_ns": start_time_ns,
        "end_time_ns": end_time_ns,
        "context_id": context_id,
        "opcode": OPCODE_TO_NAME[opcode],
        "plane_id": PLANE_ID_TO_NAME[plane_id],
        "app_id": APP_ID_TO_NAME[app_id],
        "target_port": target_port,
        "queue_id": queue_id,
        "flags": flags,
        "dst_node_id": dst_node_id,
        "flow_id": flow_id,
    }


def make_ai_trace_entry(
    *,
    packet_count: int,
    packet_len: int,
    gap_cycles: int,
    dst_mac_lo: int,
    dst_mac_hi: int,
    ethertype: int,
    dst_node_id: int,
    flow_id: int,
    payload_seed: int,
) -> Dict[str, Any]:
    """Create one low-level AI trace-table record in the JSON ABI format."""

    return {
        "packet_count": packet_count,
        "packet_len": packet_len,
        "gap_cycles": gap_cycles,
        "dst_mac_lo": dst_mac_lo,
        "dst_mac_hi": dst_mac_hi,
        "ethertype": ethertype,
        "dst_node_id": dst_node_id,
        "flow_id": flow_id,
        "payload_seed": payload_seed,
    }


def compile_consensus_periodic_phase(
    phase: Dict[str, Any],
    ctx: CompileContext,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Expand a periodic consensus workload into one window per round.

    The current consensus RTL expects one logical round to occupy one execution
    window so that TX, RX, and commit all observe the same current_window_id.
    For that reason, every round is emitted as a single CONS_TX window with
    tx_enable, rx_enable, and completion_event set together.
    """

    del ctx  # The current consensus compiler does not allocate extra contexts.

    start_time_ns = parse_int(phase["start_time_ns"], "start_time_ns")
    round_period_ns = parse_int(phase["round_period_ns"], "round_period_ns")
    round_length_ns = parse_int(phase["round_length_ns"], "round_length_ns")
    num_rounds = parse_int(phase["num_rounds"], "num_rounds")
    target_port = parse_int(phase.get("target_port", 0), "target_port")
    queue_id = parse_int(phase.get("queue_id", 0), "queue_id")
    dst_node_id = parse_int(phase.get("dst_node_id", 0), "dst_node_id")
    flow_id = parse_int(phase.get("flow_id", 0), "flow_id")
    plane_id = parse_plane(phase.get("plane", "eps"))

    if num_rounds <= 0:
        raise ValueError("consensus_periodic.num_rounds must be positive")
    if round_period_ns <= 0:
        raise ValueError("consensus_periodic.round_period_ns must be positive")
    if round_length_ns <= 0:
        raise ValueError("consensus_periodic.round_length_ns must be positive")
    if round_length_ns > round_period_ns:
        raise ValueError("consensus_periodic.round_length_ns must not exceed round_period_ns")

    flags = (
        SyncDcnFlags.VALID
        | SyncDcnFlags.TX_ENABLE
        | SyncDcnFlags.RX_ENABLE
        | SyncDcnFlags.COMPLETION_EVENT
    )

    entries: List[Dict[str, Any]] = []
    for round_index in range(num_rounds):
        window_start = start_time_ns + round_index * round_period_ns
        window_end = window_start + round_length_ns
        entries.append(
            make_execution_entry(
                start_time_ns=window_start,
                end_time_ns=window_end,
                context_id=round_index,
                opcode=SyncDcnOpcode.CONS_TX,
                plane_id=plane_id,
                app_id=SyncDcnAppId.CONSENSUS,
                target_port=target_port,
                queue_id=queue_id,
                flags=flags,
                dst_node_id=dst_node_id,
                flow_id=flow_id,
            )
        )

    return entries, []


def compile_ai_window_phase(
    phase: Dict[str, Any],
    ctx: CompileContext,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Compile one explicit AI TX or AI RX window plus its trace record."""

    start_time_ns = parse_int(phase["start_time_ns"], "start_time_ns")
    end_time_ns = parse_int(phase["end_time_ns"], "end_time_ns")
    mode = str(phase.get("mode", "tx")).strip().lower()
    plane_id = parse_plane(phase.get("plane", "ocs"))
    target_port = parse_int(phase.get("target_port", 0), "target_port")
    queue_id = parse_int(phase.get("queue_id", 0), "queue_id")

    if end_time_ns <= start_time_ns:
        raise ValueError("ai_window end_time_ns must be greater than start_time_ns")

    trace_spec = phase.get("trace")
    if not isinstance(trace_spec, dict):
        raise ValueError("ai_window.trace must be an object/mapping")

    context_id = parse_int(phase.get("context_id", ctx.next_ai_context_id), "context_id")
    ctx.next_ai_context_id = max(ctx.next_ai_context_id, context_id + 1)

    opcode: int
    flags = SyncDcnFlags.VALID
    if mode == "tx":
        opcode = SyncDcnOpcode.AI_TX
        flags |= SyncDcnFlags.TX_ENABLE
    elif mode == "rx":
        opcode = SyncDcnOpcode.AI_RX
        flags |= SyncDcnFlags.RX_ENABLE
        if bool(phase.get("drop_nonmatching", True)):
            flags |= SyncDcnFlags.DROP_NONMATCHING
        if bool(phase.get("expect_packet", True)):
            flags |= SyncDcnFlags.EXPECT_PACKET
    else:
        raise ValueError("ai_window.mode must be either 'tx' or 'rx'")

    trace_entry = make_ai_trace_entry(
        packet_count=parse_int(trace_spec["packet_count"], "trace.packet_count"),
        packet_len=parse_int(trace_spec["packet_len"], "trace.packet_len"),
        gap_cycles=parse_int(trace_spec.get("gap_cycles", 0), "trace.gap_cycles"),
        dst_mac_lo=parse_int(trace_spec["dst_mac_lo"], "trace.dst_mac_lo"),
        dst_mac_hi=parse_int(trace_spec["dst_mac_hi"], "trace.dst_mac_hi"),
        ethertype=parse_int(trace_spec.get("ethertype", 0x88B6), "trace.ethertype"),
        dst_node_id=parse_int(trace_spec.get("dst_node_id", 0), "trace.dst_node_id"),
        flow_id=parse_int(trace_spec.get("flow_id", 0), "trace.flow_id"),
        payload_seed=parse_int(trace_spec.get("payload_seed", 0), "trace.payload_seed"),
    )

    entry = make_execution_entry(
        start_time_ns=start_time_ns,
        end_time_ns=end_time_ns,
        context_id=context_id,
        opcode=opcode,
        plane_id=plane_id,
        app_id=SyncDcnAppId.AI_REPLAY,
        target_port=target_port,
        queue_id=queue_id,
        flags=flags,
        dst_node_id=parse_int(trace_entry["dst_node_id"], "trace.dst_node_id"),
        flow_id=parse_int(trace_entry["flow_id"], "trace.flow_id"),
    )

    return [entry], [(context_id, trace_entry)]


def compile_guard_phase(
    phase: Dict[str, Any],
    ctx: CompileContext,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Compile one explicit silent guard or reconfiguration window."""

    del ctx

    start_time_ns = parse_int(phase["start_time_ns"], "start_time_ns")
    end_time_ns = parse_int(phase["end_time_ns"], "end_time_ns")
    kind = str(phase.get("kind", "guard")).strip().lower()

    if end_time_ns <= start_time_ns:
        raise ValueError("guard end_time_ns must be greater than start_time_ns")

    opcode = SyncDcnOpcode.RECONFIG if kind == "reconfig" else SyncDcnOpcode.GUARD

    return [
        make_execution_entry(
            start_time_ns=start_time_ns,
            end_time_ns=end_time_ns,
            context_id=0,
            opcode=opcode,
            plane_id=parse_plane(phase.get("plane", "ocs")),
            app_id=SyncDcnAppId.NONE,
            target_port=parse_int(phase.get("target_port", 0), "target_port"),
            queue_id=parse_int(phase.get("queue_id", 0), "queue_id"),
            flags=SyncDcnFlags.VALID,
            dst_node_id=parse_int(phase.get("dst_node_id", 0), "dst_node_id"),
            flow_id=parse_int(phase.get("flow_id", 0), "flow_id"),
        )
    ], []


def compile_phases(phases: Iterable[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Compile all high-level phases into flat low-level tables."""

    ctx = CompileContext()
    execution_entries: List[Dict[str, Any]] = []
    ai_trace_pairs: List[Tuple[int, Dict[str, Any]]] = []

    for phase_index, phase in enumerate(phases):
        if not isinstance(phase, dict):
            raise TypeError(f"phase {phase_index} must be an object/mapping")

        phase_type = str(phase.get("type", "")).strip().lower()
        if phase_type == "consensus_periodic":
            new_exec, new_ai = compile_consensus_periodic_phase(phase, ctx)
        elif phase_type == "ai_window":
            new_exec, new_ai = compile_ai_window_phase(phase, ctx)
        elif phase_type in ("guard", "reconfig"):
            phase = dict(phase)
            phase["kind"] = phase_type
            new_exec, new_ai = compile_guard_phase(phase, ctx)
        else:
            raise ValueError(f"Unsupported phase type: {phase_type!r}")

        execution_entries.extend(new_exec)
        ai_trace_pairs.extend(new_ai)

    execution_entries.sort(key=lambda entry: (parse_int(entry["start_time_ns"]), parse_int(entry["end_time_ns"])))

    # Reject overlaps explicitly.  The current hardware executor is deterministic
    # and intentionally simple, so the compiler should surface any overlap
    # instead of silently picking one ordering.
    for prev, cur in zip(execution_entries, execution_entries[1:]):
        prev_end = parse_int(prev["end_time_ns"], "end_time_ns")
        cur_start = parse_int(cur["start_time_ns"], "start_time_ns")
        if cur_start < prev_end:
            raise ValueError(
                "Compiled execution windows overlap: "
                f"[{prev['start_time_ns']}, {prev['end_time_ns']}) and "
                f"[{cur['start_time_ns']}, {cur['end_time_ns']})"
            )

    # Allocate a dense AI trace table so the existing runtime can keep using the
    # context id as the direct trace-table index.
    ai_trace_entries: List[Dict[str, Any]] = []
    if ai_trace_pairs:
        max_context = max(context_id for context_id, _ in ai_trace_pairs)
        ai_trace_entries = [
            make_ai_trace_entry(
                packet_count=0,
                packet_len=0,
                gap_cycles=0,
                dst_mac_lo=0,
                dst_mac_hi=0,
                ethertype=0x88B6,
                dst_node_id=0,
                flow_id=0,
                payload_seed=0,
            )
            for _ in range(max_context + 1)
        ]
        for context_id, trace_entry in ai_trace_pairs:
            ai_trace_entries[context_id] = trace_entry

    return execution_entries, ai_trace_entries


def compile_spec(spec: Dict[str, Any]) -> Dict[str, Any]:
    """Compile a high-level experiment object into low-level schedule JSON."""

    phases = spec.get("phases")
    if not isinstance(phases, list) or not phases:
        raise ValueError("Experiment spec must contain a non-empty phases[] list")

    execution_entries, ai_trace_entries = compile_phases(phases)

    return {
        "admin_bank": parse_int(spec.get("admin_bank", 1), "admin_bank"),
        "activate_time_ns": parse_int(spec.get("activate_time_ns", 0), "activate_time_ns"),
        "enable_ai_replay": bool(spec.get("enable_ai_replay", bool(ai_trace_entries))),
        "enable_subsystem": bool(spec.get("enable_subsystem", True)),
        "execution_entries": execution_entries,
        "ai_trace_entries": ai_trace_entries,
    }


def build_arg_parser() -> argparse.ArgumentParser:
    """Build the CLI parser for the compiler."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="High-level experiment spec (JSON/YAML)")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="Write compiled low-level schedule JSON to this path (default: stdout)",
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty-print the compiled JSON output",
    )
    return parser


def main(argv: List[str] | None = None) -> int:
    """CLI entry point."""

    args = build_arg_parser().parse_args(argv)
    compiled = compile_spec(load_spec(args.input))

    text = json.dumps(compiled, indent=2 if args.pretty else None, sort_keys=False)
    if args.pretty:
        text += "\n"

    if args.output is None:
        sys.stdout.write(text)
    else:
        args.output.write_text(text)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
