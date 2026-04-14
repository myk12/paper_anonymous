#!/usr/bin/env python3
"""Prepare a full Sync-DCN experiment workspace from one global input spec.

This tool is the practical evaluation bridge on top of utopia_global_compile:

- compile one global workload/topology input
- materialize multi-target artifacts under a results directory
- emit one global-plan file for debugging/plotting
- emit manifests that show how to program each FPGA

The goal is to make the research workflow concrete:

global input -> global compile -> processor/NIC/fabric artifacts -> FPGA programming
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.append(str(REPO_ROOT))

from toolchain.compiler.utopia_global_compile import compile_global_spec
from toolchain.inputs.build_moe_model_experiment import build_global_ai_spec
from toolchain.inputs.load_input import load_system_input_spec
from toolchain.experiments.export_schedule import (
    build_flat_rows,
    export_csv,
    export_flat_json,
    export_mermaid,
)


def write_json(path: Path, obj: Dict[str, Any]) -> None:
    """Write one JSON file with stable pretty formatting."""

    path.write_text(json.dumps(obj, indent=2) + "\n")


def normalize_global_input(raw_input: Dict[str, Any]) -> Dict[str, Any]:
    """Accept either a raw system-input spec or a normalized global compiler spec."""

    if "workloads" in raw_input and "node_count" in raw_input:
        return raw_input

    if all(key in raw_input for key in ("cluster", "processor_model", "topology", "model", "workload")):
        return build_global_ai_spec(raw_input)

    raise ValueError(
        "input must be either a normalized global co-compiler spec "
        "or a raw/split MoE system-input spec"
    )


def sorted_node_ids(per_node_programs: Dict[str, Dict[str, Any]]) -> List[str]:
    """Return node ids sorted numerically."""

    return sorted(per_node_programs, key=lambda value: int(value))


def annotate_ai_trace_entries_with_timing(node_program: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Attach expected timing metadata to processor/plugin-side AI trace entries.

    The current hardware trace-table ABI does not carry timestamps.  Time is
    still controlled by execution-table windows.  This helper adds a
    processor-side timing contract that points each trace descriptor at the
    execution windows that are expected to consume it.
    """

    raw_entries = node_program.get("ai_trace_entries", [])
    execution_entries = node_program.get("execution_entries", [])
    annotated_entries: List[Dict[str, Any]] = []

    for trace_index, raw_entry in enumerate(raw_entries):
        matching_windows = [
            {
                "opcode": entry.get("opcode"),
                "start_time_ns": entry.get("start_time_ns"),
                "end_time_ns": entry.get("end_time_ns"),
                "context_id": entry.get("context_id"),
                "phase_role": entry.get("phase_role"),
            }
            for entry in execution_entries
            if str(entry.get("app_id", "")).strip().lower() == "ai_replay"
            and int(entry.get("context_id", -1)) == trace_index
        ]

        expected_start_time_ns = (
            min(window["start_time_ns"] for window in matching_windows)
            if matching_windows
            else None
        )
        expected_end_time_ns = (
            max(window["end_time_ns"] for window in matching_windows)
            if matching_windows
            else None
        )

        annotated_entries.append(
            {
                **raw_entry,
                "trace_index": trace_index,
                "timing_contract": {
                    "expected_start_time_ns": expected_start_time_ns,
                    "expected_end_time_ns": expected_end_time_ns,
                    "expected_windows": matching_windows,
                    "note": (
                        "This timing contract is derived from the compiled NIC "
                        "execution windows. It is descriptive metadata for the "
                        "processor/plugin side and does not override the NIC "
                        "executor's window-based timing control."
                    ),
                },
            }
        )

    return annotated_entries


def extract_processor_artifacts(compiled: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Build one processor-side artifact per node.

    The current prototype does not yet emit executable processor programs.
    Instead, it emits per-node phase timelines derived from processor-plane
    windows in the global plan.  This is enough to make the co-compiler output
    shape match the intended multi-target system architecture.
    """

    global_windows = compiled["global_plan"]["windows"]
    artifacts: Dict[str, Dict[str, Any]] = {}

    for node_id in sorted_node_ids(compiled["per_node_programs"]):
        node_program = compiled["per_node_programs"][node_id]
        node_num = int(node_id)
        phase_windows = [
            {
                "kind": window["kind"],
                "start_time_ns": window["start_time_ns"],
                "end_time_ns": window["end_time_ns"],
                "metadata": window.get("metadata", {}),
            }
            for window in global_windows
            if window.get("plane") == "processor" and node_num in window.get("participants", [])
        ]
        artifacts[node_id] = {
            "target_type": "processor",
            "node_id": node_num,
            "hostname": node_program.get("metadata", {}).get("hostname", f"node-{node_id}"),
            "phase_timeline": phase_windows,
            "ai_trace_entries": annotate_ai_trace_entries_with_timing(node_program),
            "metadata": {
                "source": "utopia_prepare_experiment",
                "note": (
                    "Processor/plugin-side artifact. In the target architecture, AI trace "
                    "descriptors belong here; the current FPGA prototype still colocates "
                    "their consumer with the NIC-side surrogate plugin."
                ),
            },
        }

    return artifacts


def extract_nic_artifacts(compiled: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Build one NIC-side artifact per node.

    The NIC should only execute active communication windows.  Passive network
    fabric behavior such as OCS reconfiguration or explicit guard holes should
    remain in the fabric artifacts, while processor-side silent periods remain
    implicit as time gaps between NIC entries.
    """

    artifacts: Dict[str, Dict[str, Any]] = {}
    for node_id in sorted_node_ids(compiled["per_node_programs"]):
        source_program = compiled["per_node_programs"][node_id]
        filtered_entries = [
            entry
            for entry in source_program.get("execution_entries", [])
            if str(entry.get("app_id", "none")).strip().lower() != "none"
        ]

        tx_entries = [
            entry
            for entry in filtered_entries
            if "tx" in str(entry.get("opcode", "")).strip().lower()
        ]
        rx_entries = [
            entry
            for entry in filtered_entries
            if "rx" in str(entry.get("opcode", "")).strip().lower()
        ]

        node_program = dict(source_program)
        node_program.pop("execution_entries", None)
        node_program.pop("ai_trace_entries", None)
        node_program.pop("enable_ai_replay", None)
        node_program["target_type"] = "nic"
        node_program["tx_execution_entries"] = tx_entries
        node_program["rx_execution_entries"] = rx_entries
        node_program["metadata"] = {
            **source_program.get("metadata", {}),
            "artifact_semantics": "active_nic_windows_only",
            "dropped_passive_entries": len(source_program.get("execution_entries", [])) - len(filtered_entries),
            "tx_entry_count": len(tx_entries),
            "rx_entry_count": len(rx_entries),
            "note": (
                "NIC schedule-only artifact. Passive guard/reconfig holes are omitted "
                "and remain implicit as time gaps. TX and RX schedules are split at "
                "the artifact boundary even though the current FPGA prototype still "
                "uses one shared local execution table internally. AI trace "
                "descriptors live in the processor/plugin artifact."
            ),
        }
        artifacts[node_id] = node_program
    return artifacts


def extract_prototype_runtime_artifacts(compiled: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Build one prototype-only merged artifact per node.

    The current FPGA prototype still consumes both the NIC execution schedule
    and the AI trace descriptors on the FPGA side.  Keep this merged artifact
    available for bring-up while presenting cleaner target-architecture
    artifacts separately.
    """

    artifacts: Dict[str, Dict[str, Any]] = {}
    for node_id in sorted_node_ids(compiled["per_node_programs"]):
        source_program = dict(compiled["per_node_programs"][node_id])
        source_program["target_type"] = "prototype_fpga_runtime"
        source_program["metadata"] = {
            **source_program.get("metadata", {}),
            "note": (
                "Prototype-only merged artifact for the current FPGA-resident AI "
                "surrogate path. Uses both execution entries and AI trace entries."
            ),
        }
        artifacts[node_id] = source_program
    return artifacts


def normalize_fabric_targets(global_input: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    """Determine fabric component ids from topology metadata.

    If the input does not yet describe individual switch/controller ids, fall
    back to one logical component per plane.
    """

    topology = global_input.get("topology", {})
    result: Dict[str, List[Dict[str, Any]]] = {}

    for plane in ("eps", "ocs"):
        plane_spec = topology.get(plane, {})
        raw_components = None
        if isinstance(plane_spec, dict):
            raw_components = (
                plane_spec.get("components")
                or plane_spec.get("switches")
                or plane_spec.get("fabrics")
                or plane_spec.get("controllers")
            )

        components: List[Dict[str, Any]] = []
        if isinstance(raw_components, list) and raw_components:
            for index, raw_component in enumerate(raw_components):
                if isinstance(raw_component, dict):
                    component_id = raw_component.get("id", index)
                    component = dict(raw_component)
                    component["id"] = str(component_id)
                else:
                    component = {"id": str(raw_component)}
                components.append(component)
        else:
            components.append({"id": "0", "label": f"default_{plane}_component"})

        result[plane] = components

    return result


def extract_fabric_artifacts(compiled: Dict[str, Any], global_input: Dict[str, Any]) -> Dict[str, Dict[str, Dict[str, Any]]]:
    """Build fabric-side artifacts grouped by plane and component id."""

    global_windows = compiled["global_plan"]["windows"]
    fabric_targets = normalize_fabric_targets(global_input)
    artifacts: Dict[str, Dict[str, Dict[str, Any]]] = {"eps": {}, "ocs": {}}

    for plane in ("eps", "ocs"):
        plane_windows = [
            {
                "window_id": window["window_id"],
                "kind": window["kind"],
                "start_time_ns": window["start_time_ns"],
                "end_time_ns": window["end_time_ns"],
                "participants": window.get("participants", []),
                "matching": window.get("matching", []),
                "metadata": window.get("metadata", {}),
            }
            for window in global_windows
            if window.get("plane") == plane
        ]

        for component in fabric_targets[plane]:
            component_id = str(component["id"])
            artifacts[plane][component_id] = {
                "target_type": "fabric",
                "plane": plane,
                "component_id": component_id,
                "component_metadata": component,
                "schedule": plane_windows,
                "metadata": {
                    "source": "utopia_prepare_experiment",
                    "note": "Prototype fabric artifact. Current schedule is replicated to each plane component.",
                },
            }

    return artifacts


def build_manifest_json(
    *,
    experiment_name: str,
    results_dir: Path,
    prototype_runtime_dir: Path,
    resource_template: str,
    compiled: Dict[str, Any],
    processor_artifacts: Dict[str, Dict[str, Any]],
    nic_artifacts: Dict[str, Dict[str, Any]],
    prototype_runtime_artifacts: Dict[str, Dict[str, Any]],
    fabric_artifacts: Dict[str, Dict[str, Dict[str, Any]]],
) -> Dict[str, Any]:
    """Build a machine-readable manifest for all result artifacts."""

    nodes: Dict[str, Any] = {}
    for node_id in sorted_node_ids(compiled["per_node_programs"]):
        hostname = compiled["per_node_programs"][node_id].get("metadata", {}).get("hostname", f"node-{node_id}")
        nodes[node_id] = {
            "hostname": hostname,
            "processor_artifact": str(results_dir / "processor" / f"node_{node_id}.json"),
            "nic_artifact": str(results_dir / "nic" / f"node_{node_id}.json"),
            "prototype_runtime_artifact": str(prototype_runtime_dir / f"node_{node_id}.json"),
            "resource": resource_template.format(node_id=node_id),
            "source_workloads": compiled["per_node_programs"][node_id].get("metadata", {}).get("source_workloads", []),
        }

    fabric: Dict[str, Any] = {}
    for plane, components in fabric_artifacts.items():
        fabric[plane] = {
            component_id: str(results_dir / "fabric" / f"{plane}_{component_id}.json")
            for component_id in sorted(components, key=lambda value: str(value))
        }

    return {
        "experiment_name": experiment_name,
        "results_dir": str(results_dir),
        "compatibility_dir": str(prototype_runtime_dir.parent),
        "prototype_runtime_dir": str(prototype_runtime_dir),
        "global_plan": str(results_dir / "global_plan.json"),
        "compiled_global": str(results_dir / "compiled_global.json"),
        "nodes": nodes,
        "fabric": fabric,
        "summary": {
            "global_windows": len(compiled["global_plan"]["windows"]),
            "processor_artifacts": len(processor_artifacts),
            "nic_artifacts": len(nic_artifacts),
            "compatibility_artifacts": len(prototype_runtime_artifacts),
            "fabric_artifacts": sum(len(components) for components in fabric_artifacts.values()),
        },
    }


def build_manifest(
    *,
    experiment_name: str,
    output_dir: Path,
    results_dir: Path,
    prototype_runtime_dir: Path,
    resource_template: str,
    compiled: Dict[str, Any],
) -> str:
    """Build a human-readable command manifest for the testbed."""

    program_script = REPO_ROOT / "host" / "control_plane" / "sync_dcn_program.py"
    lines = []
    lines.append(f"# Sync-DCN experiment manifest: {experiment_name}")
    lines.append("")
    lines.append("## Artifacts")
    lines.append(f"- results directory: {results_dir}")
    lines.append(f"- global plan: {results_dir / 'global_plan.json'}")
    lines.append(f"- compiler output: {results_dir / 'compiled_global.json'}")
    lines.append(f"- processor artifacts: {results_dir / 'processor'}")
    lines.append(f"- NIC artifacts: {results_dir / 'nic'}")
    lines.append(f"- fabric artifacts: {results_dir / 'fabric'}")
    lines.append(f"- manifest json: {results_dir / 'manifest.json'}")
    lines.append(f"- compatibility artifacts: {prototype_runtime_dir}")
    lines.append("")
    lines.append("## Per-node artifacts")

    for node_id in sorted_node_ids(compiled["per_node_programs"]):
        nic_path = results_dir / "nic" / f"node_{node_id}.json"
        proc_path = results_dir / "processor" / f"node_{node_id}.json"
        proto_path = prototype_runtime_dir / f"node_{node_id}.json"
        hostname = compiled["per_node_programs"][node_id].get("metadata", {}).get("hostname", f"node-{node_id}")
        lines.append(f"- node {node_id} ({hostname}):")
        lines.append(f"  processor: {proc_path}")
        lines.append(f"  nic: {nic_path}")
        lines.append(f"  prototype_runtime_compat: {proto_path}")

    lines.append("")
    lines.append("## Fabric artifacts")
    for fabric_path in sorted((results_dir / "fabric").glob("*.json")):
        lines.append(f"- {fabric_path.name}: {fabric_path}")

    lines.append("")
    lines.append("## Example dry-run commands (current FPGA prototype compatibility path)")
    for node_id in sorted_node_ids(compiled["per_node_programs"]):
        node_path = prototype_runtime_dir / f"node_{node_id}.json"
        lines.append(
            "python3 "
            f"{program_script} "
            f"--dry-run {node_path}"
        )

    lines.append("")
    lines.append("## Example hardware programming commands (current FPGA prototype compatibility path)")
    lines.append(f"# resource template: {resource_template}")
    for node_id in sorted_node_ids(compiled["per_node_programs"]):
        node_path = prototype_runtime_dir / f"node_{node_id}.json"
        resource_path = resource_template.format(node_id=node_id)
        lines.append(
            "python3 "
            f"{program_script} "
            f"--resource {resource_path} --status {node_path}"
        )

    lines.append("")
    lines.append("## Summary")
    lines.append(f"- global windows: {len(compiled['global_plan']['windows'])}")
    for node_id in sorted_node_ids(compiled["per_node_programs"]):
        node_prog = compiled["per_node_programs"][node_id]
        lines.append(
            f"- node {node_id}: "
            f"exec_entries={len(node_prog.get('execution_entries', []))}, "
            f"ai_trace_entries={len(node_prog.get('ai_trace_entries', []))}, "
            f"sources={node_prog.get('metadata', {}).get('source_workloads', [])}"
        )
    lines.append(f"- processor artifacts: {len(list((results_dir / 'processor').glob('*.json')))}")
    lines.append(f"- NIC artifacts: {len(list((results_dir / 'nic').glob('*.json')))}")
    lines.append(f"- fabric artifacts: {len(list((results_dir / 'fabric').glob('*.json')))}")
    lines.append(f"- compatibility artifacts: {len(list(prototype_runtime_dir.glob('*.json')))}")

    lines.append("")
    return "\n".join(lines)


def build_arg_parser() -> argparse.ArgumentParser:
    """Create the CLI argument parser."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="Global co-compiler input JSON/YAML")
    parser.add_argument(
        "-o",
        "--output-dir",
        type=Path,
        required=True,
        help="Directory to populate with experiment artifacts",
    )
    parser.add_argument(
        "--resource-template",
        default="/path/to/node{node_id}/resource0",
        help="Template used in the generated programming manifest",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Replace the output directory if it already exists",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""

    args = build_arg_parser().parse_args(argv)
    output_dir = args.output_dir

    if output_dir.exists():
        if not args.force:
            print(f"error: output directory already exists: {output_dir}", file=sys.stderr)
            return 2
        shutil.rmtree(output_dir)

    output_dir.mkdir(parents=True, exist_ok=True)
    results_dir = output_dir / "results"
    compat_dir = output_dir / "compat"
    prototype_runtime_dir = compat_dir / "prototype_runtime"
    (results_dir / "processor").mkdir(parents=True, exist_ok=True)
    (results_dir / "nic").mkdir(parents=True, exist_ok=True)
    (results_dir / "fabric").mkdir(parents=True, exist_ok=True)
    prototype_runtime_dir.mkdir(parents=True, exist_ok=True)

    global_input = normalize_global_input(load_system_input_spec(args.input))
    compiled = compile_global_spec(global_input)
    experiment_name = compiled.get("experiment_name", "unnamed_experiment")
    processor_artifacts = extract_processor_artifacts(compiled)
    nic_artifacts = extract_nic_artifacts(compiled)
    prototype_runtime_artifacts = extract_prototype_runtime_artifacts(compiled)
    fabric_artifacts = extract_fabric_artifacts(compiled, global_input)

    write_json(results_dir / "compiled_global.json", compiled)
    write_json(results_dir / "global_plan.json", compiled["global_plan"])
    rows = build_flat_rows(compiled["global_plan"])
    export_csv(rows, results_dir / "global_plan_timeline.csv")
    export_flat_json(rows, results_dir / "global_plan_timeline.json")
    export_mermaid(rows, results_dir / "global_plan_timeline.mmd")

    for node_id, artifact in processor_artifacts.items():
        write_json(results_dir / "processor" / f"node_{node_id}.json", artifact)

    for node_id, artifact in nic_artifacts.items():
        write_json(results_dir / "nic" / f"node_{node_id}.json", artifact)

    for node_id, artifact in prototype_runtime_artifacts.items():
        write_json(prototype_runtime_dir / f"node_{node_id}.json", artifact)

    for plane, artifacts in fabric_artifacts.items():
        for component_id, artifact in artifacts.items():
            write_json(results_dir / "fabric" / f"{plane}_{component_id}.json", artifact)

    manifest_json = build_manifest_json(
        experiment_name=experiment_name,
        results_dir=results_dir,
        prototype_runtime_dir=prototype_runtime_dir,
        resource_template=args.resource_template,
        compiled=compiled,
        processor_artifacts=processor_artifacts,
        nic_artifacts=nic_artifacts,
        prototype_runtime_artifacts=prototype_runtime_artifacts,
        fabric_artifacts=fabric_artifacts,
    )
    write_json(results_dir / "manifest.json", manifest_json)

    manifest = build_manifest(
        experiment_name=experiment_name,
        output_dir=output_dir,
        results_dir=results_dir,
        prototype_runtime_dir=prototype_runtime_dir,
        resource_template=args.resource_template,
        compiled=compiled,
    )
    (output_dir / "MANIFEST.md").write_text(manifest)

    print(f"Prepared experiment '{experiment_name}' in {output_dir}")
    print(f"  global windows : {len(compiled['global_plan']['windows'])}")
    print(f"  processor artifacts : {len(processor_artifacts)}")
    print(f"  NIC artifacts       : {len(nic_artifacts)}")
    print(f"  compatibility artifacts : {len(prototype_runtime_artifacts)}")
    print(f"  fabric artifacts    : {sum(len(components) for components in fabric_artifacts.values())}")
    print(f"  manifest       : {output_dir / 'MANIFEST.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
