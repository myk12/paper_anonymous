# Host

This directory contains host-side software components that remain in Utopia's
asynchronous control plane.

## Current Contents

- `control_plane/`: host-side control logic that installs artifacts and
  programs runtime state
- `nic_driver/`: kernel driver interface used by the host-side control stack
  to access the FPGA-based DNI platform

This directory intentionally keeps only these two host-side layers. Workload-
specific integrations should generally live under `workloads/` rather than
directly in `host/`.
