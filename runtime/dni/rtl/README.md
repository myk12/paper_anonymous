# Active RTL Walkthrough

This directory contains the active deterministic-NIC RTL.

The layout is organized into three buckets:

- `rtl/`
  - top-level shells and subsystem assembly
- `rtl/core/`
  - reusable DNI runtime core blocks
- `rtl/apps/`
  - workload-specific stand-in engines

The code is easiest to understand in three layers:

## 1. Top-Level Shells

These files assemble the runtime core and bridge it into the Corundum-facing
shell.

Read these first:

1. [`mqnic_app_block_dni.v`](mqnic_app_block_dni.v)
   - thin Corundum-facing shell
2. [`dni_subsystem.v`](dni_subsystem.v)
   - top-level subsystem, register ABI, table windows, and runtime wiring

## 2. DNI Core

These files define the core endpoint runtime semantics of the deterministic
network interface.

1. [`exec_table.v`](core/exec_table.v)
   - banked TX/RX execution-table storage and host programming access
2. [`tt_scheduler.v`](core/tt_scheduler.v)
   - dual-channel time-triggered scheduling, entry progression, and bank switching
3. [`processor_runtime.v`](core/processor_runtime.v)
   - generic processor-runtime shell with standardized per-app slots
4. [`processor_adapter_stub.v`](core/processor_adapter_stub.v)
   - future-facing stub for a processor-backed slot implementation
5. [`comm_datapath.v`](core/comm_datapath.v)
   - host/processor datapath boundary with processor-owned RX classification
6. [`dni_tx_dest_format.v`](core/dni_tx_dest_format.v)
   - plane-aware transmit destination formatting

[`schedule_decode.v`](schedule_decode.v) remains in this directory as a helper
used internally by [`tt_scheduler.v`](core/tt_scheduler.v). It is not intended to
represent a separate architectural block in the paper-level design.

## 3. Application / Case-Study Engines

These files implement the current workload-specific engines used by the paper
prototype. They demonstrate what the DNI can host, but they are not themselves
the core DNI abstraction.

- [`consensus_core.v`](apps/consensus_core.v)
- [`consensus_node.v`](apps/consensus_node.v)
- [`consensus_rx.v`](apps/consensus_rx.v)
- [`consensus_tx.v`](apps/consensus_tx.v)
- [`ai_trace_replay.v`](apps/ai_trace_replay.v)

## Important Note

Shared execution-program constants now live in
[`common/dni_defs.vh`](common/dni_defs.vh), local to this runtime
tree.
