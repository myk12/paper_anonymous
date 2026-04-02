#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

import yaml
from loguru import logger

from src.topo.model import load_topology
from src.topo.validate import validate_topology


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--topo", required=True, help="Path to system-spineleaf-topo.yaml")
    args = ap.parse_args()

    doc = yaml.safe_load(Path(args.topo).read_text())
    topo = load_topology(doc)
    validate_topology(topo)


if __name__ == "__main__":
    main()