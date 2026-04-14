#!/usr/bin/env python3
"""Export a Sync-DCN global plan into visualization-friendly artifacts.

The exporter keeps formats intentionally simple so they are easy to use in:

- paper figure generation
- spreadsheet inspection
- quick timeline debugging
- downstream plotting scripts
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any, Dict, List


def load_global_plan(path: Path) -> Dict[str, Any]:
    """Load either a bare global_plan.json or a full compiled_global.json."""

    data = json.loads(path.read_text())
    if not isinstance(data, dict):
        raise ValueError("Top-level JSON object must be a mapping")

    if "global_plan" in data:
        global_plan = data["global_plan"]
        if not isinstance(global_plan, dict):
            raise ValueError("'global_plan' must be an object")
        return global_plan

    if "windows" in data:
        return data

    raise ValueError("Input JSON must be either a global_plan object or a compiled_global object")


def flatten_window(window: Dict[str, Any]) -> Dict[str, Any]:
    """Convert one global window into a flat row for CSV/JSON export."""

    metadata = window.get("metadata", {})
    matching = window.get("matching", [])
    participants = window.get("participants", [])
    start_time_ns = int(window["start_time_ns"])
    end_time_ns = int(window["end_time_ns"])

    return {
        "window_id": int(window["window_id"]),
        "start_time_ns": start_time_ns,
        "end_time_ns": end_time_ns,
        "duration_ns": end_time_ns - start_time_ns,
        "plane": str(window.get("plane", "")),
        "kind": str(window.get("kind", "")),
        "participant_count": len(participants),
        "participants": ",".join(str(node) for node in participants),
        "matching_pair_count": len(matching),
        "matching": ";".join(f"{src}->{dst}" for src, dst in matching),
        "source_workload": metadata.get("source_workload", ""),
        "epoch_index": metadata.get("epoch_index", ""),
        "round_index": metadata.get("round_index", ""),
    }


def build_flat_rows(global_plan: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Flatten all windows into rows suitable for CSV and plotting."""

    windows = global_plan.get("windows")
    if not isinstance(windows, list):
        raise ValueError("'windows' must be a list")
    return [flatten_window(window) for window in windows]


def export_csv(rows: List[Dict[str, Any]], path: Path) -> None:
    """Write a CSV timeline table."""

    fieldnames = [
        "window_id",
        "start_time_ns",
        "end_time_ns",
        "duration_ns",
        "plane",
        "kind",
        "participant_count",
        "participants",
        "matching_pair_count",
        "matching",
        "source_workload",
        "epoch_index",
        "round_index",
    ]

    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def export_flat_json(rows: List[Dict[str, Any]], path: Path) -> None:
    """Write a flat JSON timeline useful for plotting scripts."""

    path.write_text(json.dumps(rows, indent=2) + "\n")


def export_mermaid(rows: List[Dict[str, Any]], path: Path) -> None:
    """Write a Mermaid Gantt chart for quick schedule visualization.

    Mermaid Gantt is not perfect for very large schedules, but it is useful for
    quick inspection and for embedding schedule sketches in notes.
    """

    lines = []
    lines.append("gantt")
    lines.append("  title Sync-DCN Global Schedule")
    lines.append("  dateFormat X")
    lines.append("  axisFormat %L")
    current_section = None
    for row in rows:
        section = row["plane"].upper() or "UNKNOWN"
        if section != current_section:
            lines.append(f"  section {section}")
            current_section = section
        label = f"W{row['window_id']} {row['kind']}"
        start = row["start_time_ns"]
        duration = row["duration_ns"]
        lines.append(f"  {label} : {start}, {duration}ms")

    # Mermaid's gantt parser needs a time unit suffix; the absolute values are
    # nanoseconds in our system, but for a quick sketch we keep them as raw
    # scalar values and annotate the file header instead of converting units.
    lines.insert(0, "%% Raw values are Sync-DCN schedule nanoseconds, not wall-clock dates.")
    path.write_text("\n".join(lines) + "\n")


def build_arg_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="global_plan.json or compiled_global.json")
    parser.add_argument(
        "-o",
        "--output-prefix",
        type=Path,
        required=True,
        help="Output prefix for .csv, .json, and .mmd files",
    )
    return parser


def main(argv: List[str] | None = None) -> int:
    """CLI entry point."""

    args = build_arg_parser().parse_args(argv)
    global_plan = load_global_plan(args.input)
    rows = build_flat_rows(global_plan)

    export_csv(rows, args.output_prefix.with_suffix(".csv"))
    export_flat_json(rows, args.output_prefix.with_suffix(".json"))
    export_mermaid(rows, args.output_prefix.with_suffix(".mmd"))

    print(f"Exported {len(rows)} windows with prefix {args.output_prefix}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
