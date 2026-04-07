#!/usr/bin/env python3
"""Prototype global co-compiler for Sync-DCN.

This tool is intentionally simple:

- consensus workloads use periodic EPS control windows
- AI matrix workloads use greedy OCS matching epochs
- local per-node programs are lowered into the existing low-level JSON ABI via
  sync_dcn_compile.compile_spec

The goal is to create a usable research pipeline, not a fully general or fully
optimal scheduler.
"""

from __future__ import annotations

import argparse
import copy
import json
import math
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.append(str(REPO_ROOT))

from toolchain.lowering.schedule_timing import estimate_epoch_duration_ns, resolve_ai_plane_timing
from toolchain.lowering.sync_dcn_compile import compile_spec
from toolchain.system_input.sync_dcn_build_moe_model_experiment import build_compiled_matrix, normalize_matrix

try:
    import yaml  # type: ignore
except ImportError:  # pragma: no cover - optional dependency
    yaml = None


def parse_int(value: Any, field_name: str = "value") -> int:
    """Parse an integer from either a numeric literal or a string."""

    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return int(value, 0)
    raise TypeError(f"{field_name} must be int-compatible, got {type(value)!r}")


def load_spec(path: Path) -> Dict[str, Any]:
    """Load a global co-compiler input spec from JSON or YAML."""

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
        raise ValueError("Top-level co-compiler spec must be an object/mapping")
    return data


def make_consensus_phase(workload: Dict[str, Any]) -> Dict[str, Any]:
    """Convert one consensus workload into the existing local high-level phase."""

    return {
        "type": "consensus_periodic",
        "start_time_ns": parse_int(workload["start_time_ns"], "start_time_ns"),
        "round_period_ns": parse_int(workload["round_period_ns"], "round_period_ns"),
        "round_length_ns": parse_int(workload["round_length_ns"], "round_length_ns"),
        "num_rounds": parse_int(workload["num_rounds"], "num_rounds"),
        "plane": str(workload.get("plane", "eps")),
        "target_port": parse_int(workload.get("target_port", 0), "target_port"),
        "queue_id": parse_int(workload.get("queue_id", 0), "queue_id"),
        "dst_node_id": parse_int(workload.get("dst_node_id", 0), "dst_node_id"),
        "flow_id": parse_int(workload.get("flow_id", 0), "flow_id"),
    }


def make_guard_phase(start_time_ns: int, end_time_ns: int, plane: str, target_port: int, kind: str) -> Dict[str, Any]:
    """Create a high-level guard or reconfiguration phase."""

    return {
        "type": kind,
        "start_time_ns": start_time_ns,
        "end_time_ns": end_time_ns,
        "plane": plane,
        "target_port": target_port,
    }


def make_ai_phase(
    *,
    mode: str,
    start_time_ns: int,
    end_time_ns: int,
    plane: str,
    target_port: int,
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
    """Create a high-level local AI phase compatible with sync_dcn_compile."""

    return {
        "type": "ai_window",
        "mode": mode,
        "start_time_ns": start_time_ns,
        "end_time_ns": end_time_ns,
        "plane": plane,
        "target_port": target_port,
        "trace": {
            "packet_count": packet_count,
            "packet_len": packet_len,
            "gap_cycles": gap_cycles,
            "dst_mac_lo": dst_mac_lo,
            "dst_mac_hi": dst_mac_hi,
            "ethertype": ethertype,
            "dst_node_id": dst_node_id,
            "flow_id": flow_id,
            "payload_seed": payload_seed,
        },
    }


def greedy_matching(remaining: List[List[int]], active_nodes: List[int]) -> List[Tuple[int, int, int]]:
    """Compute one greedy directed matching from the remaining matrix.

    Important current-prototype constraint:
    one node may appear at most once in a single epoch, regardless of whether
    it would be a sender or a receiver.  This is stricter than a general
    bipartite matching, but it matches the current local executor and app path,
    which can only consume one local action window at a time.

    The selected edge weight is the entire remaining packet budget for that
    source-destination pair.  This is intentionally simple and deterministic.
    """

    candidates: List[Tuple[int, int, int]] = []
    for src in active_nodes:
        for dst in active_nodes:
            if src == dst:
                continue
            weight = remaining[src][dst]
            if weight > 0:
                candidates.append((weight, src, dst))

    candidates.sort(key=lambda item: (-item[0], item[1], item[2]))

    used_nodes = set()
    matching: List[Tuple[int, int, int]] = []

    for weight, src, dst in candidates:
        if src in used_nodes or dst in used_nodes:
            continue
        matching.append((src, dst, weight))
        used_nodes.add(src)
        used_nodes.add(dst)

    return matching


def matrix_has_work(remaining: List[List[int]], active_nodes: List[int]) -> bool:
    """Return True if any active source-destination entry remains positive."""

    for src in active_nodes:
        for dst in active_nodes:
            if src != dst and remaining[src][dst] > 0:
                return True
    return False


def append_compute_gap_window(
    *,
    global_windows: List[Dict[str, Any]],
    start_time_ns: int,
    end_time_ns: int,
    active_nodes: List[int],
    kind: str,
    metadata: Dict[str, Any],
) -> None:
    """Append a processor-side compute or preparation gap to the global plan."""

    if end_time_ns <= start_time_ns:
        return

    global_windows.append(
        {
            "window_id": len(global_windows),
            "start_time_ns": start_time_ns,
            "end_time_ns": end_time_ns,
            "plane": "processor",
            "kind": kind,
            "matching": [],
            "participants": active_nodes[:],
            "metadata": metadata,
        }
    )


def compile_ai_matrix_workload(
    workload: Dict[str, Any],
    local_specs: Dict[int, Dict[str, Any]],
    global_windows: List[Dict[str, Any]],
    global_metadata: Dict[str, Any],
    topology: Dict[str, Any] | None = None,
    *,
    start_time_override_ns: int | None = None,
    source_workload_name: str = "ai_matrix",
    phase_role: str | None = None,
) -> int:
    """Schedule one AI matrix workload using greedy OCS matching epochs."""

    active_nodes = [parse_int(node, "active_node") for node in workload["active_nodes"]]
    base_matrix = normalize_matrix(workload["base_matrix"])
    realized_matrix_raw = workload.get("realized_matrix")
    realized_matrix = normalize_matrix(realized_matrix_raw) if realized_matrix_raw is not None else None
    matrix_mode = str(workload.get("matrix_mode", "exact")).strip().lower()
    capacity_factor = float(workload.get("capacity_factor", 1.0))
    padding_packets = parse_int(workload.get("padding_packets", 0), "padding_packets")
    plane = str(workload.get("plane", "ocs"))
    target_port = parse_int(workload.get("target_port", 0), "target_port")
    packet_len = parse_int(workload.get("packet_len", 64), "packet_len")
    gap_cycles = parse_int(workload.get("gap_cycles", 0), "gap_cycles")
    dst_mac_lo = parse_int(workload.get("dst_mac_lo", 0xAABBCCDD), "dst_mac_lo")
    dst_mac_hi = parse_int(workload.get("dst_mac_hi", 0x1234), "dst_mac_hi")
    ethertype = parse_int(workload.get("ethertype", 0x88B6), "ethertype")
    start_time_ns = (
        start_time_override_ns
        if start_time_override_ns is not None
        else parse_int(workload["start_time_ns"], "start_time_ns")
    )
    plane_timing = resolve_ai_plane_timing(workload=workload, topology=topology, plane=plane)
    reconfiguration_time_ns = parse_int(plane_timing["reconfiguration_time_ns"], "reconfiguration_time_ns")
    guard_band_ns = parse_int(plane_timing["guard_band_ns"], "guard_band_ns")

    compiled_matrix = build_compiled_matrix(
        base_matrix,
        matrix_mode=matrix_mode,
        capacity_factor=capacity_factor,
        padding_packets=padding_packets,
    )
    remaining = [row[:] for row in compiled_matrix]

    if realized_matrix is not None:
        spill_total = 0
        for src in active_nodes:
            for dst in active_nodes:
                if src != dst:
                    spill_total += max(0, realized_matrix[src][dst] - compiled_matrix[src][dst])
        global_metadata.setdefault("ai_workloads", []).append(
            {
                "type": source_workload_name,
                "matrix_mode": matrix_mode,
                "spill_budget_packets_total": spill_total,
                "phase_role": phase_role,
            }
        )

    cursor_ns = start_time_ns
    epoch_index = 0

    while matrix_has_work(remaining, active_nodes):
        matching = greedy_matching(remaining, active_nodes)
        if not matching:
            raise RuntimeError("greedy matching failed to make progress on non-empty matrix")

        epoch_start = cursor_ns
        epoch_duration_ns = estimate_epoch_duration_ns(
            matching=matching,
            packet_len=packet_len,
            gap_cycles=gap_cycles,
            plane_timing=plane_timing,
        )
        epoch_end = epoch_start + epoch_duration_ns
        global_windows.append(
            {
                "window_id": len(global_windows),
                "start_time_ns": epoch_start,
                "end_time_ns": epoch_end,
                "plane": plane,
                "kind": "ai_bulk_epoch",
                "matching": [[src, dst] for src, dst, _ in matching],
                "participants": sorted({node for edge in matching for node in edge[:2]}),
                "metadata": {
                    "epoch_index": epoch_index,
                    "source_workload": source_workload_name,
                    "phase_role": phase_role,
                    "epoch_duration_ns": epoch_duration_ns,
                    "epoch_duration_model": plane_timing["mode"],
                },
            }
        )

        for src, dst, packet_count in matching:
            remaining[src][dst] = 0
            flow_id = epoch_index * 256 + src * 16 + dst
            payload_seed = ((src & 0xFF) << 24) | ((dst & 0xFF) << 16) | (flow_id & 0xFFFF)

            local_specs[src]["phases"].append(
                make_ai_phase(
                    mode="tx",
                    start_time_ns=epoch_start,
                    end_time_ns=epoch_end,
                    plane=plane,
                    target_port=target_port,
                    packet_count=packet_count,
                    packet_len=packet_len,
                    gap_cycles=gap_cycles,
                    dst_mac_lo=dst_mac_lo,
                    dst_mac_hi=dst_mac_hi,
                    ethertype=ethertype,
                    dst_node_id=dst,
                    flow_id=flow_id,
                    payload_seed=payload_seed,
                )
            )

            local_specs[dst]["phases"].append(
                make_ai_phase(
                    mode="rx",
                    start_time_ns=epoch_start,
                    end_time_ns=epoch_end,
                    plane=plane,
                    target_port=target_port,
                    packet_count=packet_count,
                    packet_len=packet_len,
                    gap_cycles=0,
                    dst_mac_lo=dst_mac_lo,
                    dst_mac_hi=dst_mac_hi,
                    ethertype=ethertype,
                    dst_node_id=dst,
                    flow_id=flow_id,
                    payload_seed=payload_seed,
                )
            )

        cursor_ns = epoch_end
        if matrix_has_work(remaining, active_nodes):
            guard_before_end = cursor_ns + guard_band_ns
            global_windows.append(
                {
                    "window_id": len(global_windows),
                    "start_time_ns": cursor_ns,
                    "end_time_ns": guard_before_end,
                    "plane": plane,
                    "kind": "guard",
                    "matching": [],
                    "participants": active_nodes[:],
                    "metadata": {
                        "epoch_index": epoch_index,
                        "source_workload": source_workload_name,
                        "phase_role": phase_role,
                    },
                }
            )
            for node in active_nodes:
                local_specs[node]["phases"].append(
                    make_guard_phase(cursor_ns, guard_before_end, plane, target_port, "guard")
                )
            cursor_ns = guard_before_end

            reconfig_end = cursor_ns + reconfiguration_time_ns
            global_windows.append(
                {
                    "window_id": len(global_windows),
                    "start_time_ns": cursor_ns,
                    "end_time_ns": reconfig_end,
                    "plane": plane,
                    "kind": "reconfig",
                    "matching": [],
                    "participants": active_nodes[:],
                    "metadata": {
                        "epoch_index": epoch_index,
                        "source_workload": source_workload_name,
                        "phase_role": phase_role,
                    },
                }
            )
            for node in active_nodes:
                local_specs[node]["phases"].append(
                    make_guard_phase(cursor_ns, reconfig_end, plane, target_port, "reconfig")
                )
            cursor_ns = reconfig_end

            guard_after_end = cursor_ns + guard_band_ns
            global_windows.append(
                {
                    "window_id": len(global_windows),
                    "start_time_ns": cursor_ns,
                    "end_time_ns": guard_after_end,
                    "plane": plane,
                    "kind": "guard",
                    "matching": [],
                    "participants": active_nodes[:],
                    "metadata": {
                        "epoch_index": epoch_index,
                        "source_workload": source_workload_name,
                        "phase_role": phase_role,
                    },
                }
            )
            for node in active_nodes:
                local_specs[node]["phases"].append(
                    make_guard_phase(cursor_ns, guard_after_end, plane, target_port, "guard")
                )
            cursor_ns = guard_after_end

        epoch_index += 1

    return cursor_ns


def compile_moe_phase_sequence(
    workload: Dict[str, Any],
    local_specs: Dict[int, Dict[str, Any]],
    global_windows: List[Dict[str, Any]],
    global_metadata: Dict[str, Any],
    topology: Dict[str, Any] | None = None,
) -> None:
    """Expand one MoE step into dispatch -> compute gap -> combine."""

    active_nodes = [parse_int(node, "active_node") for node in workload["active_nodes"]]
    phase_name = str(workload.get("phase_name", "moe_phase_sequence"))
    base_start_ns = parse_int(workload["start_time_ns"], "start_time_ns")
    dispatch_prepare_ns = parse_int(workload.get("dispatch_prepare_ns", 0), "dispatch_prepare_ns")
    expert_compute_ns = parse_int(workload.get("expert_compute_ns", 0), "expert_compute_ns")
    combine_prepare_ns = parse_int(workload.get("combine_prepare_ns", 0), "combine_prepare_ns")
    completion_slack_ns = parse_int(workload.get("completion_slack_ns", 0), "completion_slack_ns")

    dispatch_start_ns = base_start_ns + dispatch_prepare_ns
    append_compute_gap_window(
        global_windows=global_windows,
        start_time_ns=base_start_ns,
        end_time_ns=dispatch_start_ns,
        active_nodes=active_nodes,
        kind="processor_prepare_dispatch",
        metadata={"source_workload": phase_name, "phase_role": "dispatch_prepare"},
    )

    dispatch_workload = dict(workload["dispatch"])
    dispatch_workload["active_nodes"] = active_nodes
    dispatch_end_ns = compile_ai_matrix_workload(
        dispatch_workload,
        local_specs,
        global_windows,
        global_metadata,
        topology,
        start_time_override_ns=dispatch_start_ns,
        source_workload_name=phase_name,
        phase_role="dispatch",
    )

    compute_end_ns = dispatch_end_ns + expert_compute_ns
    append_compute_gap_window(
        global_windows=global_windows,
        start_time_ns=dispatch_end_ns,
        end_time_ns=compute_end_ns,
        active_nodes=active_nodes,
        kind="processor_expert_compute",
        metadata={"source_workload": phase_name, "phase_role": "expert_compute"},
    )

    combine_start_ns = compute_end_ns + combine_prepare_ns
    append_compute_gap_window(
        global_windows=global_windows,
        start_time_ns=compute_end_ns,
        end_time_ns=combine_start_ns,
        active_nodes=active_nodes,
        kind="processor_prepare_combine",
        metadata={"source_workload": phase_name, "phase_role": "combine_prepare"},
    )

    combine_workload = dict(workload["combine"])
    combine_workload["active_nodes"] = active_nodes
    combine_end_ns = compile_ai_matrix_workload(
        combine_workload,
        local_specs,
        global_windows,
        global_metadata,
        topology,
        start_time_override_ns=combine_start_ns,
        source_workload_name=phase_name,
        phase_role="combine",
    )

    completion_end_ns = combine_end_ns + completion_slack_ns
    append_compute_gap_window(
        global_windows=global_windows,
        start_time_ns=combine_end_ns,
        end_time_ns=completion_end_ns,
        active_nodes=active_nodes,
        kind="processor_completion_slack",
        metadata={"source_workload": phase_name, "phase_role": "completion_slack"},
    )


def compile_global_spec(spec: Dict[str, Any]) -> Dict[str, Any]:
    """Compile the global input into a global plan plus per-node low-level programs."""

    cluster_spec = spec.get("cluster", {})
    topology = spec.get("topology", {})
    cluster_nodes = cluster_spec.get("nodes", []) if isinstance(cluster_spec, dict) else []
    node_count = parse_int(spec["node_count"], "node_count")
    local_nodes = [parse_int(node, "local_node") for node in spec.get("local_nodes", list(range(node_count)))]
    node_metadata_map = {
        parse_int(node["id"], "cluster.nodes[].id"): node
        for node in cluster_nodes
        if isinstance(node, dict) and "id" in node
    }
    workloads = spec.get("workloads")
    if not isinstance(workloads, list) or not workloads:
        raise ValueError("global co-compiler input must contain a non-empty workloads list")

    local_specs: Dict[int, Dict[str, Any]] = {
        node_id: {
            "admin_bank": parse_int(spec.get("admin_bank", 1), "admin_bank"),
            "activate_time_ns": parse_int(spec.get("activate_time_ns", 0), "activate_time_ns"),
            "enable_subsystem": bool(spec.get("enable_subsystem", True)),
            "enable_ai_replay": False,
            "metadata": {
                "node_id": node_id,
                "hostname": str(node_metadata_map.get(node_id, {}).get("hostname", f"node-{node_id}")),
                "source": "sync_dcn_global_compile",
                "source_workloads": [],
            },
            "phases": [],
        }
        for node_id in local_nodes
    }

    global_windows: List[Dict[str, Any]] = []
    global_metadata: Dict[str, Any] = {
        "experiment_name": spec.get("experiment_name", "unnamed_experiment"),
        "node_count": node_count,
        "local_nodes": local_nodes,
        "cluster_nodes": cluster_nodes,
    }
    if isinstance(spec.get("metadata"), dict):
        global_metadata["input_metadata"] = copy.deepcopy(spec["metadata"])

    for workload in workloads:
        workload_type = str(workload["type"]).strip().lower()
        if workload_type == "consensus_periodic":
            phase = make_consensus_phase(workload)
            replica_nodes = [parse_int(node, "replica_node") for node in workload["replica_nodes"]]
            start_time_ns = parse_int(workload["start_time_ns"], "start_time_ns")
            round_period_ns = parse_int(workload["round_period_ns"], "round_period_ns")
            round_length_ns = parse_int(workload["round_length_ns"], "round_length_ns")
            num_rounds = parse_int(workload["num_rounds"], "num_rounds")
            for round_index in range(num_rounds):
                global_windows.append(
                    {
                        "window_id": len(global_windows),
                        "start_time_ns": start_time_ns + round_index * round_period_ns,
                        "end_time_ns": start_time_ns + round_index * round_period_ns + round_length_ns,
                        "plane": str(workload.get("plane", "eps")),
                        "kind": "consensus_round_window",
                        "matching": [],
                        "participants": replica_nodes,
                        "metadata": {
                            "round_index": round_index,
                            "source_workload": "consensus_periodic",
                        },
                    }
                )
            for node_id in replica_nodes:
                if node_id in local_specs:
                    local_specs[node_id]["phases"].append(copy.deepcopy(phase))
                    local_specs[node_id]["metadata"]["source_workloads"].append("consensus_periodic")
        elif workload_type == "ai_matrix":
            compile_ai_matrix_workload(workload, local_specs, global_windows, global_metadata, topology)
            for node_id, local_spec in local_specs.items():
                if local_spec["phases"]:
                    local_spec["enable_ai_replay"] = True
                    if "ai_matrix" not in local_spec["metadata"]["source_workloads"]:
                        local_spec["metadata"]["source_workloads"].append("ai_matrix")
        elif workload_type == "moe_phase_sequence":
            compile_moe_phase_sequence(workload, local_specs, global_windows, global_metadata, topology)
            for node_id, local_spec in local_specs.items():
                if local_spec["phases"]:
                    local_spec["enable_ai_replay"] = True
                    phase_name = str(workload.get("phase_name", "moe_phase_sequence"))
                    if phase_name not in local_spec["metadata"]["source_workloads"]:
                        local_spec["metadata"]["source_workloads"].append(phase_name)
        else:
            raise ValueError(f"Unsupported workload type: {workload_type!r}")

    compiled_nodes: Dict[str, Dict[str, Any]] = {}
    local_high_level_specs: Dict[str, Dict[str, Any]] = {}
    for node_id, local_spec in local_specs.items():
        compiled = compile_spec(local_spec)
        compiled["metadata"] = local_spec["metadata"]
        compiled_nodes[str(node_id)] = compiled
        local_high_level_specs[str(node_id)] = local_spec

    global_windows.sort(key=lambda window: (window["start_time_ns"], window["end_time_ns"], window["window_id"]))
    for window_id, window in enumerate(global_windows):
        window["window_id"] = window_id

    return {
        "experiment_name": global_metadata["experiment_name"],
        "global_plan": {
            "windows": global_windows,
            "metadata": global_metadata,
        },
        "per_node_programs": compiled_nodes,
        "per_node_high_level_specs": local_high_level_specs,
    }


def build_arg_parser() -> argparse.ArgumentParser:
    """Build the CLI parser."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="Global co-compiler input JSON/YAML")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="Write the compiled global result to this path (default: stdout)",
    )
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output")
    return parser


def main(argv: List[str] | None = None) -> int:
    """CLI entry point."""

    args = build_arg_parser().parse_args(argv)
    result = compile_global_spec(load_spec(args.input))
    text = json.dumps(result, indent=2 if args.pretty else None)
    if args.pretty:
        text += "\n"
    if args.output is None:
        sys.stdout.write(text)
    else:
        args.output.write_text(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
