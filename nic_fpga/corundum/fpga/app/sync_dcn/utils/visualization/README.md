# Visualization Stage

This directory contains helpers that export a compiled global plan into
debugging-friendly formats.

Active tool:

- [`sync_dcn_export_schedule.py`](/Users/mayuke/Project/OpticalDCN/infra/nic_fpga/corundum/fpga/app/sync_dcn/utils/visualization/sync_dcn_export_schedule.py)
  - exports:
    - CSV timeline
    - flattened JSON timeline
    - Mermaid Gantt data

Use this stage after co-compilation when you want:

- debugging views
- timeline inspection
- figure inputs
