# Sync-DCN

Sync-DCN is the active research prototype for a deterministic FPGA network
interface inside Corundum. The system compiles structured distributed
workloads offline and enforces the resulting schedule at the NIC using a
synchronized time base.

At a high level, the active design is:

```text
split system input
-> offline global co-compiler
-> processor / NIC / fabric artifacts
-> host-side programming
-> PHC-driven FPGA execution
```

The current prototype targets two workload classes:

- MoE-style AI communication
- periodic consensus control traffic

The implementation already supports:

- split system input:
  - workload specification
  - processor timing model
  - topology and fabric model
- formal multi-target artifacts:
  - `results/processor`
  - `results/nic`
  - `results/fabric`
- split TX/RX scheduling in the formal NIC artifact and RTL executor
- topology-derived OCS epoch durations
- a compatibility-only lowering path for the older merged FPGA execution model

The implementation intentionally does not yet claim true local per-plane
parallel transmission. The active hardware path uses split TX/RX scheduling
with a shared local datapath.

## Core System Model

The system is organized around three compiler-visible input classes.

1. Workload specification
   This describes structured application communication, such as a Mixtral
   8x7B MoE inference sequence or periodic consensus rounds.
2. Processor timing model
   This gives bounded latency budgets for processor-visible phases such as
   dispatch preparation, expert computation, combine preparation, and
   completion slack.
3. Topology and fabric model
   This captures EPS and OCS properties, including bounded-delay behavior,
   OCS reconfiguration cost, guard requirements, link rate, and fixed fabric
   latency terms used during schedule construction.

The compiler uses these three inputs to build a global execution plan and then
lowers that plan into component-specific artifacts.

## Formal Artifacts

The current target architecture is expressed through three formal artifact
classes.

### Processor Artifacts

Per-node processor artifacts contain:

- processor phase timelines
- AI trace descriptors and timing contracts

These artifacts describe when local computation phases occur and what
processor/plugin-side communication contract should be visible to the runtime.

### NIC Artifacts

Per-node NIC artifacts contain:

- `tx_execution_entries`
- `rx_execution_entries`
- activation metadata

These artifacts are schedule-only views of endpoint communication behavior.
Passive fabric windows such as OCS guard and reconfiguration intervals are not
stored here.

### Fabric Artifacts

Fabric artifacts contain plane-specific network control schedules:

- EPS control windows
- OCS epochs
- OCS guard and reconfiguration intervals

This keeps switching-fabric control separate from endpoint-local execution.

## Prototype Compatibility Path

The repository still emits a compatibility-only artifact under:

- `compat/prototype_runtime`

This path exists solely to bridge the formal processor/NIC/fabric decomposition
to the current FPGA prototype bring-up model. It merges:

- active local windows
- passive OCS windows
- FPGA-resident AI trace programming

Use this compatibility path only when explicitly targeting the older merged
prototype flow. The preferred path for understanding the current architecture
is the formal one:

- `results/processor`
- `results/nic`
- `results/fabric`

## Repository Layout

```text
sync_dcn/
├─ rtl/                    active deterministic-NIC RTL
├─ tb/
│  └─ sync_dcn_subsystem/ active cocotb sign-off boundary
├─ modules/
│  └─ mqnic -> ../../../../modules/mqnic/
├─ utils/
│  ├─ system_input/       split-input loaders and builders
│  ├─ global_co_compiler/ global planning
│  ├─ per_node_lowering/  local JSON ABI lowering helpers
│  ├─ host_control_plane/ MMIO helpers and programmer
│  ├─ experiment_flow/    full workspace generation
│  └─ visualization/      schedule export helpers
└─ README.md
```

## Where To Start

### Software Path

Read these in order:

1. [`utils/system_input/README.md`](/Users/mayuke/Project/OpticalDCN/infra/nic_fpga/corundum/fpga/app/sync_dcn/utils/system_input/README.md)
2. [`utils/global_co_compiler/README.md`](/Users/mayuke/Project/OpticalDCN/infra/nic_fpga/corundum/fpga/app/sync_dcn/utils/global_co_compiler/README.md)
3. [`utils/experiment_flow/README.md`](/Users/mayuke/Project/OpticalDCN/infra/nic_fpga/corundum/fpga/app/sync_dcn/utils/experiment_flow/README.md)
4. [`utils/host_control_plane/README.md`](/Users/mayuke/Project/OpticalDCN/infra/nic_fpga/corundum/fpga/app/sync_dcn/utils/host_control_plane/README.md)

### RTL Path

Read these in order:

1. [`rtl/README.md`](/Users/mayuke/Project/OpticalDCN/infra/nic_fpga/corundum/fpga/app/sync_dcn/rtl/README.md)
2. [`rtl/sync_dcn_subsystem.v`](/Users/mayuke/Project/OpticalDCN/infra/nic_fpga/corundum/fpga/app/sync_dcn/rtl/sync_dcn_subsystem.v)
3. [`rtl/sync_schedule_executor.v`](/Users/mayuke/Project/OpticalDCN/infra/nic_fpga/corundum/fpga/app/sync_dcn/rtl/sync_schedule_executor.v)
4. [`rtl/sync_dcn_apps.v`](/Users/mayuke/Project/OpticalDCN/infra/nic_fpga/corundum/fpga/app/sync_dcn/rtl/sync_dcn_apps.v)
5. [`rtl/ai_trace_replay.v`](/Users/mayuke/Project/OpticalDCN/infra/nic_fpga/corundum/fpga/app/sync_dcn/rtl/ai_trace_replay.v)

## Active Example Inputs

Two split-input bundles are intentionally kept as the main examples.

### Compact 8-node MoE example

- [`utils/system_input/examples/moe_model_8node_split/system_input_bundle.json`](/Users/mayuke/Project/OpticalDCN/infra/nic_fpga/corundum/fpga/app/sync_dcn/utils/system_input/examples/moe_model_8node_split/system_input_bundle.json)

This is useful for quick walkthroughs and smaller checks.

### Full Mixtral + consensus example

- [`utils/system_input/examples/mixtral_full_inference_consensus_split/system_input_bundle.json`](/Users/mayuke/Project/OpticalDCN/infra/nic_fpga/corundum/fpga/app/sync_dcn/utils/system_input/examples/mixtral_full_inference_consensus_split/system_input_bundle.json)

This is the main end-to-end example for the current system design:

- Mixtral 8x7B-inspired MoE inference
- 32 transformer layers
- topology-derived OCS epoch timing
- 128 consensus rounds on EPS

## Quickstart

Prepare a full workspace from the active full example:

```bash
python3 /path/to/corundum/fpga/app/sync_dcn/utils/experiment_flow/sync_dcn_prepare_experiment.py \
  --force \
  -o /tmp/sync_dcn_eval \
  /path/to/corundum/fpga/app/sync_dcn/utils/system_input/examples/mixtral_full_inference_consensus_split/system_input_bundle.json
```

Inspect the generated artifact summary:

```bash
jq '.summary' /tmp/sync_dcn_eval/results/manifest.json
```

Dry-run one formal processor artifact:

```bash
python3 /path/to/corundum/fpga/app/sync_dcn/utils/host_control_plane/sync_dcn_program.py \
  --dry-run --target-type processor --node-id 0 \
  /tmp/sync_dcn_eval/results/manifest.json
```

Dry-run one formal NIC artifact:

```bash
python3 /path/to/corundum/fpga/app/sync_dcn/utils/host_control_plane/sync_dcn_program.py \
  --dry-run --target-type nic --node-id 0 \
  /tmp/sync_dcn_eval/results/manifest.json
```

## Active Hardware/ABI State

The current hardware programming model uses:

- TX execution table
- RX execution table
- AI trace table

The active RTL now includes:

- split TX/RX schedule execution
- split TX/RX app-layer control bundles
- split TX/RX control inputs for `ai_trace_replay`

The local datapath remains shared, so TX/RX scheduling is more accurate than
in the old single-table model, but the prototype still stops short of claiming
fully independent local EPS/OCS egress.

## Verification

The active sign-off boundary is:

- [`tb/sync_dcn_subsystem/README.md`](/Users/mayuke/Project/OpticalDCN/infra/nic_fpga/corundum/fpga/app/sync_dcn/tb/sync_dcn_subsystem/README.md)
- [`tb/sync_dcn_subsystem/test_sync_dcn_subsystem.py`](/Users/mayuke/Project/OpticalDCN/infra/nic_fpga/corundum/fpga/app/sync_dcn/tb/sync_dcn_subsystem/test_sync_dcn_subsystem.py)

Representative tests:

- `test_compiled_program_lifecycle_end_to_end`
- `test_compiled_consensus_round_end_to_end`
- `test_consensus_quorum_fail_then_clear_halt_and_recover`

In addition to cocotb, the repository is routinely checked through:

- `py_compile` for the active Python toolchain
- host-side dry-run programming over formal artifacts
- direct Verilog compilation of the active subsystem

## Current Limitations

- The formal architecture is cleaner than the compatibility path; do not use
  `compat/prototype_runtime` as the primary mental model for the current
  design.
- OCS epoch construction is still based on greedy matching rather than a full
  Birkhoff--von Neumann decomposition.
- Consensus handling is more conservative than the final target architecture.
- The local datapath is still shared even though the formal schedule model is
  split into TX and RX channels.
