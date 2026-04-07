#!/usr/bin/env python3
"""Helpers for loading split Sync-DCN system-input specifications.

The target system architecture presents three primary input classes:

- workload specification
- processor timing model
- topology & fabric model

This module allows those inputs to live in separate files, while still
supporting the older monolithic JSON/YAML format for convenience.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

try:
    import yaml  # type: ignore
except ImportError:  # pragma: no cover - optional dependency
    yaml = None


def load_document(path: Path) -> Dict[str, Any]:
    """Load one JSON or YAML mapping."""

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
        raise ValueError(f"Top-level document must be an object/mapping: {path}")

    return data


def _resolve_part_path(bundle_path: Path, raw_ref: Any, field_name: str) -> Path:
    """Resolve one bundle-relative part path."""

    if not isinstance(raw_ref, str) or not raw_ref.strip():
        raise ValueError(f"{field_name} must be a non-empty path string")

    candidate = Path(raw_ref)
    if not candidate.is_absolute():
        candidate = (bundle_path.parent / candidate).resolve()

    return candidate


def _looks_like_split_bundle(spec: Dict[str, Any]) -> bool:
    """Return True if the document is a split-input bundle manifest."""

    parts = spec.get("input_parts")
    if not isinstance(parts, dict):
        return False

    required = {
        "workload_specification",
        "processor_timing_model",
        "topology_fabric_model",
    }
    return required.issubset(parts)


def merge_split_system_input(bundle: Dict[str, Any], *, bundle_path: Path) -> Dict[str, Any]:
    """Merge a split-input manifest into one raw system-input object."""

    parts = bundle["input_parts"]
    workload_path = _resolve_part_path(
        bundle_path,
        parts["workload_specification"],
        "input_parts.workload_specification",
    )
    processor_path = _resolve_part_path(
        bundle_path,
        parts["processor_timing_model"],
        "input_parts.processor_timing_model",
    )
    topology_path = _resolve_part_path(
        bundle_path,
        parts["topology_fabric_model"],
        "input_parts.topology_fabric_model",
    )

    workload_spec = load_document(workload_path)
    processor_spec = load_document(processor_path)
    topology_spec = load_document(topology_path)

    merged = dict(workload_spec)

    if "processor_model" in merged or "topology" in merged:
        raise ValueError(
            "split workload specification must not inline processor_model/topology; "
            "those belong in their dedicated input parts"
        )

    if "processor_model" not in processor_spec:
        raise ValueError(
            f"processor timing model file must contain 'processor_model': {processor_path}"
        )
    if "topology" not in topology_spec:
        raise ValueError(
            f"topology & fabric model file must contain 'topology': {topology_path}"
        )

    merged["processor_model"] = processor_spec["processor_model"]
    merged["topology"] = topology_spec["topology"]

    if "timing" in processor_spec and "timing" not in merged:
        merged["timing"] = processor_spec["timing"]
    if "policy" in topology_spec and "policy" not in merged:
        merged["policy"] = topology_spec["policy"]

    merged_metadata = dict(merged.get("metadata", {}))
    merged_metadata["input_bundle"] = {
        "source": str(bundle_path),
        "workload_specification": str(workload_path),
        "processor_timing_model": str(processor_path),
        "topology_fabric_model": str(topology_path),
    }
    if "metadata" in processor_spec:
        merged_metadata["processor_input_metadata"] = processor_spec["metadata"]
    if "metadata" in topology_spec:
        merged_metadata["topology_input_metadata"] = topology_spec["metadata"]
    if "metadata" in bundle:
        merged_metadata["bundle_metadata"] = bundle["metadata"]
    merged["metadata"] = merged_metadata

    if "experiment_name" in bundle and "experiment_name" not in merged:
        merged["experiment_name"] = bundle["experiment_name"]

    return merged


def load_system_input_spec(path: Path) -> Dict[str, Any]:
    """Load either a monolithic spec or a split-input bundle manifest."""

    spec = load_document(path)
    if _looks_like_split_bundle(spec):
        return merge_split_system_input(spec, bundle_path=path)
    return spec
