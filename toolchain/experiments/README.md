# Experiment Flow Stage

This is the main entry point for the active software flow.

Tool:

- [`sync_dcn_prepare_experiment.py`](sync_dcn_prepare_experiment.py)
  - runs the global co-compiler
  - materializes `results/` and `compat/`
  - writes manifests and timeline exports

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
