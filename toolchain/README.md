# Toolchain

This directory contains the compile-time and deployment-time control-plane
logic for Utopia.

## Subdirectories

- `compiler/`: global scheduling and workload-aware compilation passes
- `lowering/`: translation from global schedules into per-target artifacts
- `orchestration/`: host and switch setup/install scripts
- `topology/`: topology validation and system-model utilities
- `inputs/`: high-level system and workload input builders/loaders
- `experiments/`: end-to-end experiment preparation and artifact export helpers
