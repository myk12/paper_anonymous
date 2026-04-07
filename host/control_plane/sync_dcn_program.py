#!/usr/bin/env python3
"""Program the Sync-DCN subsystem from a JSON or YAML schedule file.

This tool is intentionally small and backend-lightweight.  It exists to bridge
the current gap between:

- the offline schedule compiler output format
- the low-level `SyncDcnHost` MMIO helper
- practical bring-up on a real BAR/resource mapping

Usage modes:

- `--dry-run`: validate and print the writes without touching hardware
- `--resource PATH`: mmap a resource/BAR file and program the subsystem

JSON is always supported.  YAML is supported when `PyYAML` is available.
"""

from __future__ import annotations

import argparse
import json
import mmap
import os
import struct
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.append(str(REPO_ROOT))

from host.control_plane.sync_dcn_host import (
    AI_TRACE_VISIBLE_ENTRY_COUNT,
    AiTraceEntry,
    ExecutionEntry,
    RX_EXEC_VISIBLE_ENTRY_COUNT,
    SyncDcnAppId,
    SyncDcnFlags,
    SyncDcnHost,
    SyncDcnOpcode,
    SyncDcnPlaneId,
    TX_EXEC_VISIBLE_ENTRY_COUNT,
)

try:
    import yaml  # type: ignore
except ImportError:  # pragma: no cover - optional dependency
    yaml = None


APP_NAME_MAP = {
    "none": SyncDcnAppId.NONE,
    "consensus": SyncDcnAppId.CONSENSUS,
    "ai_replay": SyncDcnAppId.AI_REPLAY,
    "ai": SyncDcnAppId.AI_REPLAY,
}

PLANE_NAME_MAP = {
    "eps": SyncDcnPlaneId.EPS,
    "ocs": SyncDcnPlaneId.OCS,
}

OPCODE_NAME_MAP = {
    "idle": SyncDcnOpcode.IDLE,
    "guard": SyncDcnOpcode.GUARD,
    "cons_tx": SyncDcnOpcode.CONS_TX,
    "cons_rx": SyncDcnOpcode.CONS_RX,
    "ai_tx": SyncDcnOpcode.AI_TX,
    "ai_rx": SyncDcnOpcode.AI_RX,
    "reconfig": SyncDcnOpcode.RECONFIG,
}

FLAG_NAME_MAP = {
    "valid": SyncDcnFlags.VALID,
    "tx_enable": SyncDcnFlags.TX_ENABLE,
    "rx_enable": SyncDcnFlags.RX_ENABLE,
    "drop_nonmatching": SyncDcnFlags.DROP_NONMATCHING,
    "expect_packet": SyncDcnFlags.EXPECT_PACKET,
    "completion_event": SyncDcnFlags.COMPLETION_EVENT,
}

APP_ID_TO_NAME = {
    SyncDcnAppId.NONE: "none",
    SyncDcnAppId.CONSENSUS: "consensus",
    SyncDcnAppId.AI_REPLAY: "ai_replay",
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
class DryRunBackend:
    """Simple fake MMIO backend that records writes for review."""

    regs: Dict[int, int]

    def read32(self, addr: int) -> int:
        return self.regs.get(addr, 0)

    def write32(self, addr: int, value: int) -> None:
        self.regs[addr] = value & 0xFFFFFFFF
        print(f"WRITE 0x{addr:04X} = 0x{value & 0xFFFFFFFF:08X}")


class MmapBackend:
    """Minimal little-endian BAR/resource mapper for MMIO register access."""

    def __init__(self, path: Path, map_size: int):
        self._fd = os.open(path, os.O_RDWR | getattr(os, "O_SYNC", 0))
        self._mmap = mmap.mmap(self._fd, map_size, access=mmap.ACCESS_WRITE)

    def close(self) -> None:
        self._mmap.flush()
        self._mmap.close()
        os.close(self._fd)

    def read32(self, addr: int) -> int:
        return struct.unpack_from("<I", self._mmap, addr)[0]

    def write32(self, addr: int, value: int) -> None:
        struct.pack_into("<I", self._mmap, addr, value & 0xFFFFFFFF)


def parse_int(value: Any) -> int:
    """Parse an integer from either a numeric or string field."""

    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return int(value, 0)
    raise TypeError(f"Expected int-compatible value, got {type(value)!r}")


def parse_named_or_int(value: Any, mapping: Dict[str, int], field_name: str) -> int:
    """Accept either a symbolic string or an explicit integer encoding."""

    if isinstance(value, str):
        key = value.strip().lower()
        if key not in mapping:
            raise ValueError(f"Unknown {field_name} name: {value!r}")
        return mapping[key]
    return parse_int(value)


def parse_flags(value: Any) -> int:
    """Accept either a raw bitmask or a list of symbolic flag names."""

    if isinstance(value, list):
        mask = 0
        for item in value:
            if not isinstance(item, str):
                raise TypeError(f"Flag list entries must be strings, got {type(item)!r}")
            key = item.strip().lower()
            if key not in FLAG_NAME_MAP:
                raise ValueError(f"Unknown flag name: {item!r}")
            mask |= FLAG_NAME_MAP[key]
        return mask
    return parse_int(value)


def load_schedule_file(path: Path) -> Dict[str, Any]:
    """Load a JSON or YAML schedule description from disk."""

    suffix = path.suffix.lower()
    text = path.read_text()

    if suffix == ".json":
        return json.loads(text)

    if suffix in (".yaml", ".yml"):
        if yaml is None:
            raise RuntimeError("YAML input requires PyYAML to be installed")
        data = yaml.safe_load(text)
        if not isinstance(data, dict):
            raise ValueError("Top-level YAML document must be a mapping/object")
        return data

    raise ValueError(f"Unsupported schedule file extension: {path.suffix}")


def resolve_manifest_artifact(
    manifest: Dict[str, Any],
    *,
    target_type: str,
    node_id: Optional[str],
    fabric_plane: Optional[str],
    fabric_component: Optional[str],
) -> Path:
    """Resolve one concrete artifact path from a results manifest."""

    target_key = target_type.strip().lower()

    if target_key in ("processor", "nic", "prototype_runtime"):
        if node_id is None:
            raise ValueError(f"--node-id is required when target type is {target_key!r}")
        nodes = manifest.get("nodes", {})
        if node_id not in nodes:
            raise ValueError(f"node id {node_id!r} not found in manifest")
        artifact_key = f"{target_key}_artifact"
        artifact_path = nodes[node_id].get(artifact_key)
        if not artifact_path:
            raise ValueError(f"manifest does not contain {artifact_key!r} for node {node_id}")
        return Path(artifact_path)

    if target_key == "fabric":
        if fabric_plane is None:
            raise ValueError("--fabric-plane is required when target type is 'fabric'")
        fabric = manifest.get("fabric", {})
        plane_artifacts = fabric.get(fabric_plane, {})
        component_id = fabric_component or "0"
        artifact_path = plane_artifacts.get(component_id)
        if not artifact_path:
            raise ValueError(
                f"manifest does not contain a fabric artifact for plane={fabric_plane!r}, "
                f"component={component_id!r}"
            )
        return Path(artifact_path)

    raise ValueError(f"unsupported manifest target type: {target_type!r}")


def build_execution_entries(raw_entries: Iterable[Dict[str, Any]]) -> List[ExecutionEntry]:
    """Convert parsed schedule objects into strongly-typed execution entries."""

    result: List[ExecutionEntry] = []
    for raw in raw_entries:
        result.append(
            ExecutionEntry(
                start_time_ns=parse_int(raw["start_time_ns"]),
                end_time_ns=parse_int(raw["end_time_ns"]),
                context_id=parse_int(raw.get("context_id", 0)),
                opcode=parse_named_or_int(raw["opcode"], OPCODE_NAME_MAP, "opcode"),
                plane_id=parse_named_or_int(raw.get("plane_id", 0), PLANE_NAME_MAP, "plane"),
                app_id=parse_named_or_int(raw.get("app_id", 0), APP_NAME_MAP, "app"),
                target_port=parse_int(raw.get("target_port", 0)),
                queue_id=parse_int(raw.get("queue_id", 0)),
                flags=parse_flags(raw.get("flags", 0)),
                dst_node_id=parse_int(raw.get("dst_node_id", 0)),
                flow_id=parse_int(raw.get("flow_id", 0)),
                reserved_word7=parse_int(raw.get("reserved_word7", 0)),
            )
        )
    return result


def merge_split_nic_execution_entries(raw: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Merge target-architecture TX/RX schedules into one prototype stream.

    The long-term NIC artifact format separates TX and RX execution entries.
    The current FPGA prototype still consumes one shared local execution table,
    so the host programmer merges both lists into one time-ordered stream when
    programming today's hardware.
    """

    if "execution_entries" in raw:
        return list(raw.get("execution_entries", []))

    tx_entries = list(raw.get("tx_execution_entries", []))
    rx_entries = list(raw.get("rx_execution_entries", []))
    merged = tx_entries + rx_entries
    merged.sort(key=lambda entry: (parse_int(entry["start_time_ns"]), parse_int(entry["end_time_ns"])))
    return merged


def split_execution_entries_for_hw(
    raw_entries: Iterable[Dict[str, Any]],
) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Split a legacy/merged execution stream into TX and RX hardware tables."""

    tx_entries: List[Dict[str, Any]] = []
    rx_entries: List[Dict[str, Any]] = []

    for raw in raw_entries:
        opcode = parse_named_or_int(raw["opcode"], OPCODE_NAME_MAP, "opcode")
        flags = parse_flags(raw.get("flags", 0))
        tx_enabled = bool(flags & SyncDcnFlags.TX_ENABLE) or opcode in (
            SyncDcnOpcode.CONS_TX,
            SyncDcnOpcode.AI_TX,
        )
        rx_enabled = bool(flags & SyncDcnFlags.RX_ENABLE) or opcode in (
            SyncDcnOpcode.CONS_RX,
            SyncDcnOpcode.AI_RX,
        )

        if tx_enabled:
            tx_entry = dict(raw)
            tx_entry["flags"] = flags | SyncDcnFlags.VALID | SyncDcnFlags.TX_ENABLE
            tx_entries.append(tx_entry)

        if rx_enabled:
            rx_entry = dict(raw)
            if opcode == SyncDcnOpcode.CONS_TX:
                rx_entry["opcode"] = "cons_rx"
            rx_entry["flags"] = flags | SyncDcnFlags.VALID | SyncDcnFlags.RX_ENABLE
            rx_entries.append(rx_entry)

    tx_entries.sort(key=lambda entry: (parse_int(entry["start_time_ns"]), parse_int(entry["end_time_ns"])))
    rx_entries.sort(key=lambda entry: (parse_int(entry["start_time_ns"]), parse_int(entry["end_time_ns"])))
    return tx_entries, rx_entries


def build_ai_trace_entries(raw_entries: Iterable[Dict[str, Any]]) -> List[AiTraceEntry]:
    """Convert parsed schedule objects into strongly-typed AI trace records."""

    result: List[AiTraceEntry] = []
    for raw in raw_entries:
        result.append(
            AiTraceEntry(
                packet_count=parse_int(raw["packet_count"]),
                packet_len=parse_int(raw["packet_len"]),
                gap_cycles=parse_int(raw.get("gap_cycles", 0)),
                dst_mac_lo=parse_int(raw["dst_mac_lo"]),
                ethertype=parse_int(raw.get("ethertype", 0x88B6)),
                dst_mac_hi=parse_int(raw["dst_mac_hi"]),
                dst_node_id=parse_int(raw.get("dst_node_id", 0)),
                flow_id=parse_int(raw.get("flow_id", 0)),
                payload_seed=parse_int(raw.get("payload_seed", 0)),
            )
        )
    return result


def print_schedule_summary(
    admin_bank: int,
    activate_time_ns: int,
    tx_execution_entries: List[ExecutionEntry],
    rx_execution_entries: List[ExecutionEntry],
    ai_entries: List[AiTraceEntry],
    enable_ai: bool,
    enable_subsystem: bool,
) -> None:
    """Print a concise summary before programming hardware."""

    print("Sync-DCN schedule summary")
    print(f"  admin_bank       : {admin_bank}")
    print(f"  activate_time_ns : {activate_time_ns}")
    print(f"  tx_exec_entries  : {len(tx_execution_entries)}")
    print(f"  rx_exec_entries  : {len(rx_execution_entries)}")
    print(f"  ai_trace_entries : {len(ai_entries)}")
    print(f"  enable_ai_replay : {enable_ai}")
    print(f"  enable_subsystem : {enable_subsystem}")


def print_nic_artifact_summary(
    raw: Dict[str, Any],
    tx_entries: List[ExecutionEntry],
    rx_entries: List[ExecutionEntry],
) -> None:
    """Print a concise summary for one NIC artifact."""

    tx_count = len(raw.get("tx_execution_entries", []))
    rx_count = len(raw.get("rx_execution_entries", []))
    print("Sync-DCN NIC artifact summary")
    print(f"  tx_exec_entries  : {tx_count}")
    print(f"  rx_exec_entries  : {rx_count}")
    print(f"  programmed_tx    : {len(tx_entries)}")
    print(f"  programmed_rx    : {len(rx_entries)}")


def print_processor_artifact_summary(raw: Dict[str, Any]) -> None:
    """Print a concise summary for one processor/plugin artifact."""

    phases = raw.get("phase_timeline", [])
    traces = raw.get("ai_trace_entries", [])
    print("Sync-DCN processor/plugin artifact summary")
    print(f"  node_id         : {raw.get('node_id')}")
    print(f"  hostname        : {raw.get('hostname')}")
    print(f"  phase_windows   : {len(phases)}")
    print(f"  ai_trace_entries: {len(traces)}")


def print_fabric_artifact_summary(raw: Dict[str, Any]) -> None:
    """Print a concise summary for one fabric artifact."""

    schedule = raw.get("schedule", [])
    print("Sync-DCN fabric artifact summary")
    print(f"  plane           : {raw.get('plane')}")
    print(f"  component_id    : {raw.get('component_id')}")
    print(f"  schedule_windows: {len(schedule)}")


def program_device(
    host: SyncDcnHost,
    admin_bank: int,
    activate_time_ns: int,
    tx_execution_entries: List[ExecutionEntry],
    rx_execution_entries: List[ExecutionEntry],
    ai_entries: List[AiTraceEntry],
    enable_ai: bool,
    enable_subsystem: bool,
) -> None:
    """Apply the standard Sync-DCN programming sequence to the target."""

    for index, entry in enumerate(ai_entries):
        host.write_ai_trace_entry(index, entry)

    if enable_ai:
        host.enable_ai_replay(True)

    host.set_admin_bank(admin_bank)

    for index, entry in enumerate(tx_execution_entries):
        host.write_tx_exec_entry(index, entry)

    for index, entry in enumerate(rx_execution_entries):
        host.write_rx_exec_entry(index, entry)

    host.arm_bank_switch(admin_bank, activate_time_ns)

    if enable_subsystem:
        host.enable_subsystem(True)


def program_processor_artifact(
    host: SyncDcnHost,
    ai_entries: List[AiTraceEntry],
    enable_ai: bool,
) -> None:
    """Program only the processor/plugin-side AI trace table."""

    for index, entry in enumerate(ai_entries):
        host.write_ai_trace_entry(index, entry)

    if enable_ai:
        host.enable_ai_replay(True)


def print_status(host: SyncDcnHost) -> None:
    """Print the minimum bring-up status fields for the live subsystem."""

    status = host.read_status_summary()
    print("Sync-DCN live status")
    print(f"  active_bank   : {status['active_bank']}")
    print(f"  pending_valid : {status['pending_valid']}")
    print(f"  exec_enable   : {status['exec_enable']}")
    print(f"  window_active : {status['window_active']}")
    print(f"  tx_allowed    : {status['tx_allowed']}")
    print(f"  rx_enabled    : {status['rx_enabled']}")
    print(f"  exec_valid    : {status['exec_valid']}")
    print(f"  consensus_en  : {status['consensus_enable']}")
    print(f"  consensus_hlt : {status['consensus_halt']}")
    print(
        f"  plane_id      : {status['plane_id']} "
        f"({PLANE_ID_TO_NAME.get(status['plane_id'], 'unknown')})"
    )
    print(
        f"  app_id        : {status['app_id']} "
        f"({APP_ID_TO_NAME.get(status['app_id'], 'unknown')})"
    )
    print(
        f"  opcode        : 0x{status['opcode']:02X} "
        f"({OPCODE_TO_NAME.get(status['opcode'], 'unknown')})"
    )
    print(f"  context_id    : 0x{status['context_id']:04X}")
    print(f"  entry_ptr     : {status['entry_ptr']}")


def print_active_entry(host: SyncDcnHost) -> None:
    """Print the full currently mirrored active execution entry."""

    entry = host.read_active_entry_summary()
    print("Sync-DCN active entry")
    print(f"  entry_ptr      : {entry['entry_ptr']}")
    print(f"  start_time_ns  : {entry['start_time_ns']}")
    print(f"  end_time_ns    : {entry['end_time_ns']}")
    print(
        f"  plane_id       : {entry['plane_id']} "
        f"({PLANE_ID_TO_NAME.get(entry['plane_id'], 'unknown')})"
    )
    print(
        f"  app_id         : {entry['app_id']} "
        f"({APP_ID_TO_NAME.get(entry['app_id'], 'unknown')})"
    )
    print(
        f"  opcode         : 0x{entry['opcode']:02X} "
        f"({OPCODE_TO_NAME.get(entry['opcode'], 'unknown')})"
    )
    print(f"  context_id     : 0x{entry['context_id']:04X}")
    print(f"  active_target  : 0x{entry['target_raw']:08X}")
    print(f"  active_meta    : 0x{entry['meta_raw']:08X}")


def build_arg_parser() -> argparse.ArgumentParser:
    """Create the CLI argument parser."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "schedule",
        type=Path,
        nargs="?",
        help="Artifact JSON/YAML, or a results manifest JSON",
    )
    parser.add_argument(
        "--resource",
        type=Path,
        help="Path to a BAR/resource file to mmap and program",
    )
    parser.add_argument(
        "--map-size",
        type=lambda x: int(x, 0),
        default=0x10000,
        help="MMIO mapping size in bytes when --resource is used (default: 0x10000)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate and print writes without touching hardware",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Read back and print the current live subsystem status",
    )
    parser.add_argument(
        "--dump-entry",
        action="store_true",
        help="Read back and print the currently mirrored active execution entry",
    )
    parser.add_argument(
        "--target-type",
        choices=["processor", "nic", "prototype_runtime", "fabric"],
        help="When the input is a manifest, select which artifact class to program/inspect",
    )
    parser.add_argument(
        "--node-id",
        help="Node id used when resolving processor/nic/prototype_runtime compatibility artifacts from a manifest",
    )
    parser.add_argument(
        "--fabric-plane",
        choices=["eps", "ocs"],
        help="Fabric plane used when resolving fabric artifacts from a manifest",
    )
    parser.add_argument(
        "--fabric-component",
        help="Fabric component id used when resolving fabric artifacts from a manifest (default: 0)",
    )
    return parser


def main(argv: List[str] | None = None) -> int:
    """CLI entry point."""

    args = build_arg_parser().parse_args(argv)

    if not args.dry_run and args.resource is None:
        print("error: either --dry-run or --resource must be provided", file=sys.stderr)
        return 2

    if args.schedule is None and not (args.status or args.dump_entry):
        print(
            "error: a schedule file is required unless --status or --dump-entry is used",
            file=sys.stderr,
        )
        return 2

    if (args.status or args.dump_entry) and args.resource is None:
        print("error: --status/--dump-entry require --resource", file=sys.stderr)
        return 2

    if args.schedule is None:
        backend = MmapBackend(args.resource, args.map_size)
        try:
            host = SyncDcnHost(backend.read32, backend.write32)
            if args.status:
                print_status(host)
            if args.dump_entry:
                print_active_entry(host)
        finally:
            backend.close()
        return 0

    raw = load_schedule_file(args.schedule)

    if "nodes" in raw and "fabric" in raw and "summary" in raw:
        if args.target_type is None:
            print(
                "error: manifest input requires --target-type "
                "(processor|nic|prototype_runtime|fabric)",
                file=sys.stderr,
            )
            return 2
        artifact_path = resolve_manifest_artifact(
            raw,
            target_type=args.target_type,
            node_id=args.node_id,
            fabric_plane=args.fabric_plane,
            fabric_component=args.fabric_component,
        )
        print(f"Resolved manifest target -> {artifact_path}")
        raw = load_schedule_file(artifact_path)

    target_type = str(raw.get("target_type", "prototype_fpga_runtime")).strip().lower()
    if target_type == "prototype_fpga_runtime":
        # Older compatibility artifacts still use the historical target name.
        # Normalize it so guardrails and programming behavior stay consistent.
        target_type = "prototype_runtime"

    if target_type == "processor":
        ai_entries = build_ai_trace_entries(raw.get("ai_trace_entries", []))
        enable_ai = bool(ai_entries)

        print_processor_artifact_summary(raw)
        print_schedule_summary(
            admin_bank=0,
            activate_time_ns=0,
            tx_execution_entries=[],
            rx_execution_entries=[],
            ai_entries=ai_entries,
            enable_ai=enable_ai,
            enable_subsystem=False,
        )

        if args.dry_run:
            backend = DryRunBackend(regs={})
            host = SyncDcnHost(backend.read32, backend.write32)
            program_processor_artifact(host, ai_entries=ai_entries, enable_ai=enable_ai)
            return 0

        backend = MmapBackend(args.resource, args.map_size)
        try:
            host = SyncDcnHost(backend.read32, backend.write32)
            program_processor_artifact(host, ai_entries=ai_entries, enable_ai=enable_ai)
        finally:
            backend.close()
        return 0

    if target_type == "fabric":
        print_fabric_artifact_summary(raw)
        if not args.dry_run:
            print(
                "error: fabric artifact programming is not implemented yet; "
                "use --dry-run to inspect the artifact",
                file=sys.stderr,
            )
            return 2
        return 0

    admin_bank = parse_int(raw.get("admin_bank", 1))
    activate_time_ns = parse_int(raw.get("activate_time_ns", 0))
    enable_ai = bool(raw.get("enable_ai_replay", False))
    enable_subsystem = bool(raw.get("enable_subsystem", True))

    if "tx_execution_entries" in raw or "rx_execution_entries" in raw:
        raw_tx_entries = list(raw.get("tx_execution_entries", []))
        raw_rx_entries = list(raw.get("rx_execution_entries", []))
    else:
        raw_tx_entries, raw_rx_entries = split_execution_entries_for_hw(raw.get("execution_entries", []))

    tx_execution_entries = build_execution_entries(raw_tx_entries)
    rx_execution_entries = build_execution_entries(raw_rx_entries)
    ai_entries = build_ai_trace_entries(raw.get("ai_trace_entries", []))

    if target_type == "prototype_runtime":
        if (
            len(tx_execution_entries) > TX_EXEC_VISIBLE_ENTRY_COUNT
            or len(rx_execution_entries) > RX_EXEC_VISIBLE_ENTRY_COUNT
            or len(ai_entries) > AI_TRACE_VISIBLE_ENTRY_COUNT
        ):
            print(
                "error: prototype_runtime artifact exceeds the active split-hardware ABI; "
                "program processor and nic artifacts directly instead",
                file=sys.stderr,
            )
            return 2

    if target_type == "nic":
        enable_ai = False
        print_nic_artifact_summary(raw, tx_execution_entries, rx_execution_entries)

    print_schedule_summary(
        admin_bank=admin_bank,
        activate_time_ns=activate_time_ns,
        tx_execution_entries=tx_execution_entries,
        rx_execution_entries=rx_execution_entries,
        ai_entries=ai_entries,
        enable_ai=enable_ai,
        enable_subsystem=enable_subsystem,
    )

    if args.dry_run:
        backend = DryRunBackend(regs={})
        host = SyncDcnHost(backend.read32, backend.write32)
        program_device(
            host,
            admin_bank=admin_bank,
            activate_time_ns=activate_time_ns,
            tx_execution_entries=tx_execution_entries,
            rx_execution_entries=rx_execution_entries,
            ai_entries=ai_entries,
            enable_ai=enable_ai,
            enable_subsystem=enable_subsystem,
        )
        return 0

    backend = MmapBackend(args.resource, args.map_size)
    try:
        host = SyncDcnHost(backend.read32, backend.write32)
        program_device(
            host,
            admin_bank=admin_bank,
            activate_time_ns=activate_time_ns,
            tx_execution_entries=tx_execution_entries,
            rx_execution_entries=rx_execution_entries,
            ai_entries=ai_entries,
            enable_ai=enable_ai,
            enable_subsystem=enable_subsystem,
        )
        if args.status:
            print_status(host)
        if args.dump_entry:
            print_active_entry(host)
    finally:
        backend.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
