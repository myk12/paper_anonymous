#!/usr/bin/env python3
"""Build a global Sync-DCN AI experiment from MoE-style model parameters.

This helper is intentionally research-oriented and SimAI-inspired rather than a
full simulator.  The input is a compact model-level description:

- processor model metadata
- network topology metadata
- MoE model parameters
- one simple routing envelope model

The output is a global co-compiler input JSON that matches the current
prototype backend:

- processor_model
- topology
- timing
- policy
- workloads = [{type: "moe_phase_sequence", ...}]

The builder exists to make the end-to-end walkthrough start from a richer
application description than a hand-written traffic matrix while still
remaining deterministic and easy to reason about.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any, Dict, List

UTILS_ROOT = Path(__file__).resolve().parents[1]
if str(UTILS_ROOT) not in sys.path:
    sys.path.append(str(UTILS_ROOT))

from schedule_timing import estimate_epoch_duration_ns, parse_float as shared_parse_float, resolve_ai_plane_timing
from system_input.sync_dcn_load_system_input import load_system_input_spec


def parse_int(value: Any, field_name: str = "value") -> int:
    """Parse an integer from either a numeric literal or a string."""

    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return int(value, 0)
    raise TypeError(f"{field_name} must be int-compatible, got {type(value)!r}")


def parse_float(value: Any, field_name: str = "value") -> float:
    """Parse a floating-point number from either numeric or string input."""

    return shared_parse_float(value, field_name)


def load_spec(path: Path) -> Dict[str, Any]:
    """Load a compact MoE model spec or split-input bundle."""

    return load_system_input_spec(path)


def normalize_weights(raw_weights: Any, node_count: int, field_name: str) -> List[float]:
    """Normalize destination weights used to distribute remote expert traffic."""

    if raw_weights is None:
        return [1.0] * node_count

    if not isinstance(raw_weights, list) or len(raw_weights) != node_count:
        raise ValueError(f"{field_name} must be a list with one entry per node")

    weights = [parse_float(value, f"{field_name}[]") for value in raw_weights]
    if any(weight < 0.0 for weight in weights):
        raise ValueError(f"{field_name} entries must be non-negative")

    return weights


def normalize_matrix(raw_matrix: Any) -> List[List[int]]:
    """Normalize a matrix into a square list of integer cells."""

    if not isinstance(raw_matrix, list) or not raw_matrix:
        raise ValueError("matrix must be a non-empty 2D list")

    matrix: List[List[int]] = []
    width = None
    for row_index, row in enumerate(raw_matrix):
        if not isinstance(row, list) or not row:
            raise ValueError(f"matrix row {row_index} must be a non-empty list")
        normalized_row = [parse_int(cell, f"matrix[{row_index}][]") for cell in row]
        if width is None:
            width = len(normalized_row)
        elif len(normalized_row) != width:
            raise ValueError("all matrix rows must have the same width")
        matrix.append(normalized_row)

    if len(matrix) != width:
        raise ValueError("matrix must be square because the system assumes one row/column per node")

    return matrix


def build_compiled_matrix(
    baseline_matrix: List[List[int]],
    *,
    matrix_mode: str,
    capacity_factor: float,
    padding_packets: int,
) -> List[List[int]]:
    """Construct the compiled matrix used for deterministic schedule emission."""

    if matrix_mode == "exact":
        return [row[:] for row in baseline_matrix]

    if matrix_mode != "envelope":
        raise ValueError("matrix_mode must be either 'exact' or 'envelope'")

    compiled: List[List[int]] = []
    for row_index, row in enumerate(baseline_matrix):
        compiled_row: List[int] = []
        for col_index, cell in enumerate(row):
            if row_index == col_index:
                compiled_row.append(0)
            elif cell <= 0:
                compiled_row.append(0)
            else:
                compiled_row.append(int(math.ceil(cell * capacity_factor)) + padding_packets)
        compiled.append(compiled_row)
    return compiled


def normalize_cluster(cluster_spec: Dict[str, Any]) -> tuple[int, List[int], List[Dict[str, Any]]]:
    """Normalize cluster inventory metadata.

    The builder accepts either:

    - a compact form with `node_count` and optional `local_nodes`, or
    - a richer `nodes` inventory with `id`, `hostname`, and arbitrary metadata.
    """

    raw_nodes = cluster_spec.get("nodes")
    if isinstance(raw_nodes, list) and raw_nodes:
        nodes: List[Dict[str, Any]] = []
        node_ids: List[int] = []
        for index, raw_node in enumerate(raw_nodes):
            if not isinstance(raw_node, dict):
                raise ValueError(f"cluster.nodes[{index}] must be an object")
            node_id = parse_int(raw_node["id"], f"cluster.nodes[{index}].id")
            hostname = str(raw_node.get("hostname", f"node-{node_id}"))
            node = dict(raw_node)
            node["id"] = node_id
            node["hostname"] = hostname
            nodes.append(node)
            node_ids.append(node_id)

        if sorted(node_ids) != list(range(len(node_ids))):
            raise ValueError("cluster.nodes ids must form a dense range starting at 0")

        return len(nodes), node_ids, nodes

    node_count = parse_int(cluster_spec["node_count"], "cluster.node_count")
    local_nodes = [parse_int(node, "cluster.local_nodes[]") for node in cluster_spec.get("local_nodes", list(range(node_count)))]
    nodes = [{"id": node_id, "hostname": f"node-{node_id}"} for node_id in local_nodes]
    return node_count, local_nodes, nodes


def allocate_by_weights(total_packets: int, shares: List[float]) -> List[int]:
    """Allocate an integer packet budget across weighted destinations."""

    if total_packets <= 0:
        return [0] * len(shares)

    total_share = sum(shares)
    if total_share <= 0.0:
        raise ValueError("destination weights must contain at least one positive entry")

    exact = [total_packets * share / total_share for share in shares]
    base = [int(math.floor(value)) for value in exact]
    remainder = total_packets - sum(base)

    if remainder > 0:
        ranked = sorted(
            range(len(shares)),
            key=lambda idx: (exact[idx] - base[idx], shares[idx], -idx),
            reverse=True,
        )
        for idx in ranked[:remainder]:
            base[idx] += 1

    return base


def build_remote_matrix(
    *,
    node_count: int,
    total_remote_packets_per_node: int,
    destination_weights: List[float],
) -> List[List[int]]:
    """Build a square remote-traffic matrix from a uniform node-local budget."""

    matrix: List[List[int]] = []
    for src in range(node_count):
        remote_nodes = [dst for dst in range(node_count) if dst != src]
        remote_weights = [destination_weights[dst] for dst in remote_nodes]
        remote_packets = allocate_by_weights(total_remote_packets_per_node, remote_weights)

        row = [0] * node_count
        for dst, packets in zip(remote_nodes, remote_packets):
            row[dst] = packets
        matrix.append(row)

    return matrix


def transpose_matrix(matrix: List[List[int]]) -> List[List[int]]:
    """Return the transpose of a square matrix."""

    return [[matrix[src][dst] for src in range(len(matrix))] for dst in range(len(matrix))]


def matrix_has_positive_work(remaining: List[List[int]], active_nodes: List[int]) -> bool:
    """Return True if any active source-destination cell remains positive."""

    for src in active_nodes:
        for dst in active_nodes:
            if src != dst and remaining[src][dst] > 0:
                return True
    return False


def greedy_matching_step(remaining: List[List[int]], active_nodes: List[int]) -> List[tuple[int, int]]:
    """Replicate the compiler's one-node-per-epoch greedy matching policy."""

    candidates: List[tuple[int, int, int]] = []
    for src in active_nodes:
        for dst in active_nodes:
            if src == dst:
                continue
            weight = remaining[src][dst]
            if weight > 0:
                candidates.append((weight, src, dst))

    candidates.sort(key=lambda item: (-item[0], item[1], item[2]))

    used_nodes = set()
    matching: List[tuple[int, int]] = []
    for _, src, dst in candidates:
        if src in used_nodes or dst in used_nodes:
            continue
        matching.append((src, dst))
        used_nodes.add(src)
        used_nodes.add(dst)

    return matching


def estimate_ai_window_span_ns(
    *,
    base_matrix: List[List[int]],
    active_nodes: List[int],
    matrix_mode: str,
    capacity_factor: float,
    padding_packets: int,
    packet_len: int,
    gap_cycles: int,
    plane_timing: Dict[str, Any],
) -> int:
    """Estimate the total timeline span of one compiled AI matrix workload."""

    compiled_matrix = build_compiled_matrix(
        base_matrix,
        matrix_mode=matrix_mode,
        capacity_factor=capacity_factor,
        padding_packets=padding_packets,
    )
    remaining = [row[:] for row in compiled_matrix]
    epoch_count = 0
    total_duration_ns = 0

    while matrix_has_positive_work(remaining, active_nodes):
        matching = greedy_matching_step(remaining, active_nodes)
        if not matching:
            raise RuntimeError("greedy matching failed to make progress while estimating workload span")
        matching_with_counts = [(src, dst, remaining[src][dst]) for src, dst in matching]
        total_duration_ns += estimate_epoch_duration_ns(
            matching=matching_with_counts,
            packet_len=packet_len,
            gap_cycles=gap_cycles,
            plane_timing=plane_timing,
        )
        for src, dst in matching:
            remaining[src][dst] = 0
        epoch_count += 1

    if epoch_count == 0:
        return 0

    gap_ns = 2 * parse_int(plane_timing["guard_band_ns"], "guard_band_ns") + parse_int(
        plane_timing["reconfiguration_time_ns"],
        "reconfiguration_time_ns",
    )
    return total_duration_ns + max(0, epoch_count - 1) * gap_ns


def build_global_ai_spec(spec: Dict[str, Any]) -> Dict[str, Any]:
    """Convert MoE-style model parameters into a global AI compiler input."""

    cluster = spec["cluster"]
    model = spec["model"]
    workload = spec["workload"]

    node_count, local_nodes, cluster_nodes = normalize_cluster(cluster)

    hidden_size = parse_int(model["hidden_size"], "model.hidden_size")
    bytes_per_element = parse_int(model.get("bytes_per_element", 2), "model.bytes_per_element")
    top_k = parse_int(model["top_k"], "model.top_k")
    experts_per_node = parse_int(model.get("experts_per_node", 1), "model.experts_per_node")
    num_experts_total = parse_int(
        model.get("num_experts_total", node_count * experts_per_node),
        "model.num_experts_total",
    )
    num_layers = parse_int(model.get("num_layers", 0), "model.num_layers")
    num_attention_heads = parse_int(model.get("num_attention_heads", 0), "model.num_attention_heads")
    ffn_hidden_size_per_expert = parse_int(
        model.get("ffn_hidden_size_per_expert", 0),
        "model.ffn_hidden_size_per_expert",
    )

    tokens_per_node = parse_int(workload["tokens_per_node"], "workload.tokens_per_node")
    inferred_remote_fraction = 1.0 - (experts_per_node / float(num_experts_total))
    remote_expert_fraction = parse_float(
        workload.get("remote_expert_fraction", inferred_remote_fraction),
        "workload.remote_expert_fraction",
    )
    realized_tokens_per_node = parse_int(workload.get("realized_tokens_per_node", tokens_per_node), "workload.realized_tokens_per_node")
    realized_remote_expert_fraction = parse_float(
        workload.get("realized_remote_expert_fraction", remote_expert_fraction),
        "workload.realized_remote_expert_fraction",
    )
    packet_len = parse_int(workload.get("packet_len", 64), "workload.packet_len")

    if node_count <= 1:
        raise ValueError("cluster.node_count must be greater than 1")
    if top_k <= 0:
        raise ValueError("model.top_k must be positive")
    if hidden_size <= 0:
        raise ValueError("model.hidden_size must be positive")
    if packet_len <= 0:
        raise ValueError("workload.packet_len must be positive")
    if not (0.0 <= remote_expert_fraction <= 1.0):
        raise ValueError("workload.remote_expert_fraction must be in [0, 1]")
    if not (0.0 <= realized_remote_expert_fraction <= 1.0):
        raise ValueError("workload.realized_remote_expert_fraction must be in [0, 1]")

    bytes_per_token_dispatch = hidden_size * bytes_per_element
    baseline_remote_bytes_per_node = int(math.ceil(tokens_per_node * top_k * bytes_per_token_dispatch * remote_expert_fraction))
    realized_remote_bytes_per_node = int(
        math.ceil(realized_tokens_per_node * top_k * bytes_per_token_dispatch * realized_remote_expert_fraction)
    )
    baseline_remote_packets_per_node = int(math.ceil(baseline_remote_bytes_per_node / packet_len))
    realized_remote_packets_per_node = int(math.ceil(realized_remote_bytes_per_node / packet_len))

    base_weights = normalize_weights(workload.get("destination_weights"), node_count, "workload.destination_weights")
    realized_weights = normalize_weights(
        workload.get("realized_destination_weights", workload.get("destination_weights")),
        node_count,
        "workload.realized_destination_weights",
    )

    base_matrix = build_remote_matrix(
        node_count=node_count,
        total_remote_packets_per_node=baseline_remote_packets_per_node,
        destination_weights=base_weights,
    )
    realized_matrix = build_remote_matrix(
        node_count=node_count,
        total_remote_packets_per_node=realized_remote_packets_per_node,
        destination_weights=realized_weights,
    )
    combine_base_matrix = transpose_matrix(base_matrix)
    combine_realized_matrix = transpose_matrix(realized_matrix)

    timing_model = spec["processor_model"].get("timing_model", {})
    dispatch_prepare_ns = parse_int(timing_model.get("dispatch_prepare_ns", 0), "processor_model.timing_model.dispatch_prepare_ns")
    expert_compute_ns = parse_int(timing_model.get("expert_compute_ns", 0), "processor_model.timing_model.expert_compute_ns")
    combine_prepare_ns = parse_int(timing_model.get("combine_prepare_ns", 0), "processor_model.timing_model.combine_prepare_ns")
    completion_slack_ns = parse_int(timing_model.get("completion_slack_ns", 0), "processor_model.timing_model.completion_slack_ns")

    matrix_mode = str(workload.get("matrix_mode", "envelope"))
    capacity_factor = parse_float(workload.get("capacity_factor", 1.25), "workload.capacity_factor")
    padding_packets = parse_int(workload.get("padding_packets", 1), "workload.padding_packets")
    gap_cycles = parse_int(workload.get("gap_cycles", 1), "workload.gap_cycles")
    plane = str(workload.get("plane", "ocs"))
    target_port = parse_int(workload.get("target_port", 3), "workload.target_port")
    dst_mac_lo = parse_int(workload.get("dst_mac_lo", 0xAABBCCDD), "workload.dst_mac_lo")
    dst_mac_hi = parse_int(workload.get("dst_mac_hi", 0x1234), "workload.dst_mac_hi")
    ethertype = parse_int(workload.get("ethertype", 0x88B6), "workload.ethertype")
    plane_timing = resolve_ai_plane_timing(workload=workload, topology=spec.get("topology"), plane=plane)

    dispatch_template = {
        "epoch_duration_model": plane_timing["mode"],
        "window_duration_ns": plane_timing["fixed_window_duration_ns"],
        "reconfiguration_time_ns": plane_timing["reconfiguration_time_ns"],
        "guard_band_ns": plane_timing["guard_band_ns"],
        "matrix_mode": matrix_mode,
        "capacity_factor": capacity_factor,
        "padding_packets": padding_packets,
        "base_matrix": base_matrix,
        "realized_matrix": realized_matrix,
        "packet_len": packet_len,
        "gap_cycles": gap_cycles,
        "plane": plane,
        "target_port": target_port,
        "dst_mac_lo": dst_mac_lo,
        "dst_mac_hi": dst_mac_hi,
        "ethertype": ethertype,
    }
    combine_template = {
        "epoch_duration_model": plane_timing["mode"],
        "window_duration_ns": plane_timing["fixed_window_duration_ns"],
        "reconfiguration_time_ns": plane_timing["reconfiguration_time_ns"],
        "guard_band_ns": plane_timing["guard_band_ns"],
        "matrix_mode": "exact",
        "capacity_factor": 1.0,
        "padding_packets": 0,
        "base_matrix": combine_base_matrix,
        "realized_matrix": combine_realized_matrix,
        "packet_len": packet_len,
        "gap_cycles": gap_cycles,
        "plane": plane,
        "target_port": target_port,
        "dst_mac_lo": dst_mac_lo,
        "dst_mac_hi": dst_mac_hi,
        "ethertype": ethertype,
    }

    layer_repeat_count = parse_int(
        workload.get(
            "layer_repeat_count",
            model.get("num_layers", 1) if bool(workload.get("full_inference", False)) else 1,
        ),
        "workload.layer_repeat_count",
    )
    if layer_repeat_count <= 0:
        raise ValueError("workload.layer_repeat_count must be positive")

    dispatch_span_ns = estimate_ai_window_span_ns(
        base_matrix=base_matrix,
        active_nodes=local_nodes,
        matrix_mode=matrix_mode,
        capacity_factor=capacity_factor,
        padding_packets=padding_packets,
        packet_len=packet_len,
        gap_cycles=gap_cycles,
        plane_timing=plane_timing,
    )
    combine_span_ns = estimate_ai_window_span_ns(
        base_matrix=combine_base_matrix,
        active_nodes=local_nodes,
        matrix_mode="exact",
        capacity_factor=1.0,
        padding_packets=0,
        packet_len=packet_len,
        gap_cycles=gap_cycles,
        plane_timing=plane_timing,
    )

    base_start_ns = parse_int(workload["start_time_ns"], "workload.start_time_ns")
    workloads_out: List[Dict[str, Any]] = []
    layer_start_ns = base_start_ns

    consensus_spec = spec.get("consensus")
    consensus_rounds_total = 0
    if consensus_spec is not None and not isinstance(consensus_spec, dict):
        raise ValueError("consensus must be an object/mapping when provided")

    for layer_index in range(layer_repeat_count):
        workloads_out.append(
            {
                "type": "moe_phase_sequence",
                "phase_name": f"mixtral_moe_layer_{layer_index}",
                "active_nodes": local_nodes,
                "start_time_ns": layer_start_ns,
                "dispatch_prepare_ns": dispatch_prepare_ns,
                "expert_compute_ns": expert_compute_ns,
                "combine_prepare_ns": combine_prepare_ns,
                "completion_slack_ns": completion_slack_ns,
                "dispatch": dict(dispatch_template),
                "combine": dict(combine_template),
            }
        )

        dispatch_end_ns = layer_start_ns + dispatch_prepare_ns + dispatch_span_ns
        combine_start_ns = dispatch_end_ns + expert_compute_ns + combine_prepare_ns
        layer_end_ns = combine_start_ns + combine_span_ns + completion_slack_ns

        if isinstance(consensus_spec, dict) and bool(consensus_spec.get("enabled", True)):
            placement = str(consensus_spec.get("placement", "expert_compute_gap")).strip().lower()
            if placement != "expert_compute_gap":
                raise ValueError("consensus.placement must currently be 'expert_compute_gap'")

            rounds_per_layer = parse_int(consensus_spec.get("rounds_per_layer", 0), "consensus.rounds_per_layer")
            if rounds_per_layer > 0:
                round_length_ns = parse_int(consensus_spec["round_length_ns"], "consensus.round_length_ns")
                round_period_ns = parse_int(
                    consensus_spec.get("round_period_ns", round_length_ns),
                    "consensus.round_period_ns",
                )
                consensus_start_ns = dispatch_end_ns + parse_int(
                    consensus_spec.get("gap_offset_ns", 0),
                    "consensus.gap_offset_ns",
                )
                last_round_end_ns = consensus_start_ns + (rounds_per_layer - 1) * round_period_ns + round_length_ns
                if last_round_end_ns > dispatch_end_ns + expert_compute_ns:
                    raise ValueError(
                        "consensus rounds do not fit inside the MoE expert-compute gap; "
                        "reduce rounds_per_layer or shorten round timing"
                    )

                workloads_out.append(
                    {
                        "type": "consensus_periodic",
                        "replica_nodes": [
                            parse_int(node, "consensus.replica_nodes[]")
                            for node in consensus_spec.get("replica_nodes", [0, 1, 2])
                        ],
                        "start_time_ns": consensus_start_ns,
                        "round_period_ns": round_period_ns,
                        "round_length_ns": round_length_ns,
                        "num_rounds": rounds_per_layer,
                        "plane": str(consensus_spec.get("plane", "eps")),
                        "target_port": parse_int(consensus_spec.get("target_port", 0), "consensus.target_port"),
                    }
                )
                consensus_rounds_total += rounds_per_layer

        layer_start_ns = layer_end_ns

    global_spec = {
        "experiment_name": spec.get("experiment_name", "moe_model_walkthrough"),
        "cluster": {
            "node_count": node_count,
            "local_nodes": local_nodes,
            "nodes": cluster_nodes,
        },
        "node_count": node_count,
        "local_nodes": local_nodes,
        "admin_bank": parse_int(spec.get("admin_bank", 1), "admin_bank"),
        "activate_time_ns": parse_int(spec.get("activate_time_ns", 0), "activate_time_ns"),
        "enable_subsystem": bool(spec.get("enable_subsystem", True)),
        "processor_model": spec["processor_model"],
        "topology": spec["topology"],
        "timing": spec.get(
            "timing",
            {
                "epoch_start_ns": 0,
                "default_ai_window_ns": plane_timing["fixed_window_duration_ns"],
                "processor_ready_slack_ns": 0,
            },
        ),
        "policy": spec.get(
            "policy",
            {
                "eps_mode": "periodic_control_windows",
                "ocs_mode": "matching_epochs",
                "spill_mode": "record_only",
                "ai_matrix_mode": "envelope",
            },
        ),
        "workloads": workloads_out,
        "metadata": {
            **spec.get("metadata", {}),
            "source": "sync_dcn_build_moe_model_experiment.py",
            "cluster_nodes": cluster_nodes,
            "model_summary": {
                "hidden_size": hidden_size,
                "bytes_per_element": bytes_per_element,
                "top_k": top_k,
                "experts_per_node": experts_per_node,
                "num_experts_total": num_experts_total,
                "num_layers": num_layers,
                "num_attention_heads": num_attention_heads,
                "ffn_hidden_size_per_expert": ffn_hidden_size_per_expert,
                "tokens_per_node": tokens_per_node,
                "remote_expert_fraction": remote_expert_fraction,
                "baseline_remote_bytes_per_node": baseline_remote_bytes_per_node,
                "baseline_remote_packets_per_node": baseline_remote_packets_per_node,
                "realized_remote_packets_per_node": realized_remote_packets_per_node,
                "dispatch_prepare_ns": dispatch_prepare_ns,
                "expert_compute_ns": expert_compute_ns,
                "combine_prepare_ns": combine_prepare_ns,
                "completion_slack_ns": completion_slack_ns,
                "layer_repeat_count": layer_repeat_count,
                "dispatch_span_ns": dispatch_span_ns,
                "combine_span_ns": combine_span_ns,
                "consensus_rounds_total": consensus_rounds_total,
                "ai_epoch_duration_model": plane_timing["mode"],
                "fixed_ai_window_duration_ns": plane_timing["fixed_window_duration_ns"],
            },
        },
    }

    return global_spec


def make_parser() -> argparse.ArgumentParser:
    """Build the CLI parser."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("spec", type=Path, help="Input JSON/YAML MoE model description")
    parser.add_argument("-o", "--output", type=Path, help="Output JSON path (default: stdout)")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output")
    return parser


def main() -> int:
    """CLI entry point."""

    parser = make_parser()
    args = parser.parse_args()

    spec = load_spec(args.spec)
    compiled = build_global_ai_spec(spec)

    if args.pretty:
        output_text = json.dumps(compiled, indent=2, sort_keys=False) + "\n"
    else:
        output_text = json.dumps(compiled, separators=(",", ":"))

    if args.output:
        args.output.write_text(output_text)
    else:
        print(output_text, end="")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
