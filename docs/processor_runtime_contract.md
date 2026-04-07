# Processor Runtime Contract

## Goal

`processor_runtime` is the DNI-side runtime boundary to a local processor.

Today, the FPGA prototype connects local stand-in engines below this boundary.
In the long-term architecture, the same boundary should support a real local
processor such as a GPU or another accelerator.

This document defines the intended contract so future implementations can
replace the current stand-in engines without changing the surrounding DNI
architecture.

## Position in the Stack

The intended split is:

- `exec_table`: owns compiled execution-table storage
- `tt_scheduler`: interprets time-triggered windows and emits window-level
  execution control
- `processor_runtime`: translates scheduler-issued control into
  processor-visible execution and data movement
- `comm_datapath`: carries host-owned and processor-owned traffic to and from
  the MAC boundary

`processor_runtime` should not own table storage, bank switching, or generic
host/MAC routing.

## Functional Responsibilities

`processor_runtime` owns four interface surfaces.

### 1. Control Ingress

This is the scheduler-facing control surface.

It consumes:

- `window_id`
- `window_open_pulse`
- `window_close_pulse`
- `commit_start_pulse`
- `allowed` / `enabled`
- `app_id`
- `opcode`
- `context_id`

Its role is to translate a compiled execution window into a processor-visible
runtime event.

### 2. Command / Launch Interface

This is the future processor-launch surface.

It should translate the scheduler-issued execution context into a processor
work item, such as:

- descriptor enqueue
- queue entry publication
- doorbell
- local launch record

The runtime should be able to drive commands such as:

- transmit a processor-owned flow
- receive/expect traffic for a processor-owned context
- start a local processing phase
- observe a commit/completion event

The exact transport can vary, but the abstraction should be stable.

### 3. Data Interface

This is the processor-facing data surface.

The current prototype uses a unified streaming boundary:

- `processor_tx_*`
- `processor_rx_*`

In a future processor-backed implementation, this may become a DMA- or
queue-based interface internally, but the role remains the same:

- export processor-generated traffic toward `comm_datapath`
- receive processor-owned traffic from `comm_datapath`

### 4. Completion / Status Interface

This is the processor progress surface.

It should support:

- completion indication
- status reporting
- halt/error propagation
- optional counters or progress records

The current prototype exposes engine-specific counters and halt signals.  A
future processor-backed runtime should aggregate these into a more uniform
status model.

## External Runtime Boundary

The external `processor_runtime` interface should remain stable as the internal
implementation evolves.

### Scheduler-Facing Inputs

- TX control:
  - `i_tx_current_window_id`
  - `i_tx_window_open_pulse`
  - `i_tx_window_close_pulse`
  - `i_tx_commit_start_pulse`
  - `i_tx_allowed`
  - `i_tx_app_id`
  - `i_tx_opcode`
  - `i_tx_context_id`
- RX control:
  - `i_rx_current_window_id`
  - `i_rx_window_open_pulse`
  - `i_rx_window_close_pulse`
  - `i_rx_commit_start_pulse`
  - `i_rx_enabled`
  - `i_rx_app_id`
  - `i_rx_opcode`
  - `i_rx_context_id`

### Datapath-Facing Boundary

- TX toward `comm_datapath`:
  - `m_axis_processor_tx_*`
  - `o_processor_tx_valid`
- RX from `comm_datapath`:
  - `s_axis_processor_rx_*`

These signals express processor ownership, not app-specific semantics.

## Internal App / Engine Boundary

Below the runtime shell, the current implementation uses standardized app
slots.

This slot interface is intentionally generic:

- per-app TX window control
- per-app RX window control
- per-app TX stream
- per-app RX stream

This allows the current stand-in engines to remain plug-compatible while also
providing a migration path to a future processor adapter.

## Multi-App Model

There should be one `processor_runtime` per endpoint, not one per app.

Multiple apps or engines are attached below the runtime shell via
parameterized app slots:

- `APP_COUNT`
- standardized per-app control fanout
- per-app TX/RX streams

The runtime is responsible for:

- selecting the active app slot for TX
- demultiplexing processor RX traffic to the selected app slot
- keeping the external processor-facing boundary unified

## Recommended Future Processor Adapter

The long-term replacement for the current stand-in engines should look like a
`processor_adapter` below `processor_runtime`.

The adapter should provide at least:

- command queue / launch interface
- DMA or queue-backed TX data source
- DMA or queue-backed RX data sink
- completion/status feedback

At that point, workload-specific engines such as consensus or AI replay can
either:

- remain as prototype-only engines, or
- be replaced by processor-managed software workloads

without changing the DNI core.

The minimal starting point for that adapter is described in
[`processor_adapter_minimal_interface.md`](processor_adapter_minimal_interface.md).

## Migration Plan

The intended migration path is:

1. keep the current `processor_runtime` shell stable
2. keep stand-in engines below the per-app slot interface
3. introduce a `processor_adapter` that implements the same slot contract
4. move launch/data/completion semantics into that adapter
5. eventually reduce the stand-in engines to optional prototype modules

This preserves the architectural split while allowing the implementation to
move from FPGA-local engines toward a real processor-backed runtime.
