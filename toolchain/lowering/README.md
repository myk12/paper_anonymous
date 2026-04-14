# Per-Node Lowering Stage

This directory contains the local compiler that turns one node's high-level
phase list into the low-level JSON ABI understood by the FPGA NIC.

Active tool:

- [`compile.py`](compile.py)
  - expands `consensus_periodic`, `ai_window`, `guard`, and `reconfig`
    phases into:
    - `execution_entries`
    - `ai_trace_entries`
    - activation metadata

This stage is the exact software boundary mirrored by:

- [`dni_subsystem.v`](../../runtime/dni/rtl/dni_subsystem.v)
- [`tt_scheduler.v`](../../runtime/dni/rtl/core/tt_scheduler.v)

The direct examples for this stage were removed during repository cleanup; the
recommended way to exercise it now is through the global compiler and
experiment-flow pipeline.
