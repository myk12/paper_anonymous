#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml
from loguru import logger

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.append(str(REPO_ROOT))

from toolchain.topology import load_topology, validate_topology


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--topo", required=True, help="Path to system-spineleaf-topo.yaml")
    args = ap.parse_args()

    doc = yaml.safe_load(Path(args.topo).read_text())
    topo = load_topology(doc)
    validate_topology(topo)
    logger.info("validated topology: hosts={} endpoints={}", len(topo.hosts), len(topo.endpoints))


if __name__ == "__main__":
    main()
