# Toolchain

This directory contains the compile-time and deployment-time control-plane
logic for Utopia.

## Subdirectories

- `compiler/`: global scheduling and workload-aware compilation passes
- `lowering/`: translation from global schedules into per-target artifacts
- `orchestration/`: host and switch setup/install scripts
- `topology/`: topology validation and system-model utilities

At the moment, the compiler and lowering directories are placeholders for the
next migration step, while orchestration and topology already contain early
scripts.
