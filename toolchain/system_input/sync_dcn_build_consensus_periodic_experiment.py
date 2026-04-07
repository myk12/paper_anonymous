#!/usr/bin/env python3
"""Build a compact periodic-consensus experiment into a high-level phase spec.

This helper exists to keep the research workflow compact: instead of hand-
writing many repeated consensus windows, the user provides one small periodic
configuration and the script emits the high-level experiment spec consumed by
sync_dcn_compile.py.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

try:
    import yaml  # type: ignore
except ImportError:  # pragma: no cover - optional dependency
    yaml = None


def parse_int(value: Any, field_name: str = "value") -> int:
    """Parse an integer field from either a numeric or string literal."""

    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return int(value, 0)
    raise TypeError(f"{field_name} must be int-compatible, got {type(value)!r}")


def load_spec(path: Path) -> Dict[str, Any]:
    """Load a compact consensus experiment spec from JSON or YAML."""

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
        raise ValueError("Top-level consensus spec must be an object/mapping")

    return data


def build_experiment(spec: Dict[str, Any]) -> Dict[str, Any]:
    """Emit one high-level experiment spec with one periodic consensus phase."""

    start_time_ns = parse_int(spec.get("start_time_ns", 0), "start_time_ns")
    round_bound_ns = parse_int(spec["round_bound_ns"], "round_bound_ns")
    num_rounds = parse_int(spec["num_rounds"], "num_rounds")

    if round_bound_ns <= 0:
        raise ValueError("round_bound_ns must be positive")
    if num_rounds <= 0:
        raise ValueError("num_rounds must be positive")

    return {
        "admin_bank": parse_int(spec.get("admin_bank", 1), "admin_bank"),
        "activate_time_ns": parse_int(spec.get("activate_time_ns", 0), "activate_time_ns"),
        "enable_subsystem": bool(spec.get("enable_subsystem", True)),
        "metadata": {
            "source": "consensus_periodic_builder",
            "replica_count": parse_int(spec.get("replica_count", 3), "replica_count"),
            "leaderless": bool(spec.get("leaderless", True)),
        },
        "phases": [
            {
                "type": "consensus_periodic",
                "start_time_ns": start_time_ns,
                "round_period_ns": parse_int(spec.get("round_period_ns", round_bound_ns), "round_period_ns"),
                "round_length_ns": round_bound_ns,
                "num_rounds": num_rounds,
                "plane": str(spec.get("plane", "eps")),
                "target_port": parse_int(spec.get("target_port", 0), "target_port"),
                "queue_id": parse_int(spec.get("queue_id", 0), "queue_id"),
                "dst_node_id": parse_int(spec.get("dst_node_id", 0), "dst_node_id"),
                "flow_id": parse_int(spec.get("flow_id", 0), "flow_id"),
            }
        ],
    }


def build_arg_parser() -> argparse.ArgumentParser:
    """Build the CLI parser."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="Compact periodic consensus spec")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="Write the generated high-level experiment spec to this path (default: stdout)",
    )
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output")
    return parser


def main(argv: List[str] | None = None) -> int:
    """CLI entry point."""

    args = build_arg_parser().parse_args(argv)
    result = build_experiment(load_spec(args.input))
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
