# Visualization Stage

This directory contains helpers that export a compiled global plan into
debugging-friendly formats.

Active tool:

- [`sync_dcn_export_schedule.py`](sync_dcn_export_schedule.py)
  - exports:
    - CSV timeline
    - flattened JSON timeline
    - Mermaid Gantt data

Use this stage after co-compilation when you want:

- debugging views
- timeline inspection
- figure inputs
