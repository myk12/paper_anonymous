# Experiment Flow Stage

This is the main entry point for the active software flow.

Tool:

- [`utopia_prepare_experiment.py`](utopia_prepare_experiment.py)
  - runs the global co-compiler
  - materializes `results/` and `compat/`
  - writes manifests and timeline exports
- [`export_schedule.py`](export_schedule.py)
  - exports:
    - CSV timeline
    - flattened JSON timeline
    - Mermaid Gantt data

Output layout:

```text
results/
  manifest.json
  global_plan.json
  global_plan_timeline.csv
  global_plan_timeline.json
  global_plan_timeline.mmd
  processor/
  nic/
  fabric/
compat/
  prototype_runtime/
```

Artifact meaning:

- `processor/`: processor-side phase timelines and AI descriptors
- `nic/`: formal NIC schedules with split TX/RX entries
- `fabric/`: EPS and OCS control schedules
- `compat/prototype_runtime/`: compatibility-only FPGA prototype artifacts

This directory also contains the schedule-export helpers used for:

- debugging views
- timeline inspection
- figure inputs
