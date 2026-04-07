# Processor Adapter Minimal Interface

## Goal

`processor_adapter` is the first future-facing replacement for the current
FPGA-local stand-in engines below `processor_runtime`.

Its purpose is to preserve the existing DNI split:

- `tt_scheduler` decides when a window becomes active
- `processor_runtime` translates that window into a processor-visible runtime
  contract
- `processor_adapter` realizes that contract for a concrete local processor
- `comm_datapath` carries processor-owned traffic to and from the MAC boundary

This document defines the smallest useful interface for that adapter.

## Position in the Stack

The intended stack is:

```text
tt_scheduler
  -> processor_runtime
      -> processor_adapter
          -> local processor / DMA / queueing substrate
  -> comm_datapath
```

The adapter sits below `processor_runtime` and should fit under one app slot.

## Design Principle

The adapter should be the first component that becomes processor-specific.

That means:

- `processor_runtime` stays generic
- `comm_datapath` stays generic
- `processor_adapter` absorbs:
  - queueing details
  - DMA details
  - launch details
  - completion details

## Minimal Functional Surfaces

The minimal adapter needs four surfaces.

### 1. Runtime Control Surface

This is the app-slot control ingress from `processor_runtime`.

The adapter should consume:

- `window_id`
- `window_open_pulse`
- `window_close_pulse`
- `commit_start_pulse`
- `tx_allowed`
- `rx_enabled`
- `opcode`
- `context_id`
- `active`

This surface tells the adapter:

- which execution window is active
- whether the current role is TX or RX
- which context is selected
- when the current window begins and ends

### 2. Launch / Command Surface

The adapter should expose an internal command model that can drive a real
processor or DMA backend.

The smallest useful command representation is:

- `cmd_valid`
- `cmd_ready`
- `cmd_window_id`
- `cmd_opcode`
- `cmd_context_id`
- `cmd_tx_not_rx`
- `cmd_start`
- `cmd_stop`

This is intentionally smaller than a full RDMA-style work queue.  It is enough
to represent:

- start a TX phase for a context
- start an RX/expect phase for a context
- stop or retire a context
- react to a commit event

### 3. Data Surface

The adapter should continue to match the app-slot streaming boundary used today.

#### TX toward `processor_runtime`

- `m_axis_tx_tdata`
- `m_axis_tx_tkeep`
- `m_axis_tx_tvalid`
- `m_axis_tx_tready`
- `m_axis_tx_tlast`
- `m_axis_tx_tuser`

#### RX from `processor_runtime`

- `s_axis_rx_tdata`
- `s_axis_rx_tkeep`
- `s_axis_rx_tvalid`
- `s_axis_rx_tready`
- `s_axis_rx_tlast`
- `s_axis_rx_tuser`

This keeps the adapter plug-compatible with the existing per-app slot
interface.

Internally, the adapter can implement these streams using:

- DMA descriptors
- shared-memory buffers
- queue pairs
- GPU-side staging buffers

## 4. Completion / Status Surface

The adapter should expose a small, generic completion model back to
`processor_runtime`.

The minimal useful surface is:

- `o_done`
- `o_error`
- `o_halt`
- `o_busy`
- `o_status`

Recommended meaning:

- `o_done`: the current context completed its local work
- `o_error`: the current context failed
- `o_halt`: stop the processor side until host intervention
- `o_busy`: the adapter currently owns outstanding work
- `o_status`: implementation-defined status summary

## Minimal Verilog-Shaped Interface

The following is a recommended starting point.

```verilog
module processor_adapter #(
    parameter integer AXIS_DATA_WIDTH = 512,
    parameter integer AXIS_KEEP_WIDTH = AXIS_DATA_WIDTH/8,
    parameter integer AXIS_TX_USER_WIDTH = 1,
    parameter integer AXIS_RX_USER_WIDTH = 1,
    parameter integer STATUS_WIDTH = 32
) (
    input  wire                                 clk,
    input  wire                                 rst,
    input  wire                                 i_enable,

    // Runtime control ingress
    input  wire [63:0]                          i_tx_window_id,
    input  wire                                 i_tx_window_open_pulse,
    input  wire                                 i_tx_window_close_pulse,
    input  wire                                 i_tx_commit_start_pulse,
    input  wire                                 i_tx_allowed,
    input  wire                                 i_tx_active,
    input  wire [7:0]                           i_tx_opcode,
    input  wire [15:0]                          i_tx_context_id,

    input  wire [63:0]                          i_rx_window_id,
    input  wire                                 i_rx_window_open_pulse,
    input  wire                                 i_rx_window_close_pulse,
    input  wire                                 i_rx_commit_start_pulse,
    input  wire                                 i_rx_enabled,
    input  wire                                 i_rx_active,
    input  wire [7:0]                           i_rx_opcode,
    input  wire [15:0]                          i_rx_context_id,

    // TX toward processor_runtime
    output wire [AXIS_DATA_WIDTH-1:0]           m_axis_tx_tdata,
    output wire [AXIS_KEEP_WIDTH-1:0]           m_axis_tx_tkeep,
    output wire                                 m_axis_tx_tvalid,
    input  wire                                 m_axis_tx_tready,
    output wire                                 m_axis_tx_tlast,
    output wire [AXIS_TX_USER_WIDTH-1:0]        m_axis_tx_tuser,

    // RX from processor_runtime
    input  wire [AXIS_DATA_WIDTH-1:0]           s_axis_rx_tdata,
    input  wire [AXIS_KEEP_WIDTH-1:0]           s_axis_rx_tkeep,
    input  wire                                 s_axis_rx_tvalid,
    output wire                                 s_axis_rx_tready,
    input  wire                                 s_axis_rx_tlast,
    input  wire [AXIS_RX_USER_WIDTH-1:0]        s_axis_rx_tuser,

    // Completion / status
    output wire                                 o_done,
    output wire                                 o_error,
    output wire                                 o_halt,
    output wire                                 o_busy,
    output wire [STATUS_WIDTH-1:0]              o_status
);
```

## Recommended First Implementation

The first processor-backed adapter does not need to implement the full
long-term vision.

A good phase-1 adapter would:

1. consume runtime window control
2. convert it into a tiny local command queue
3. emit or consume packets on the existing streaming boundary
4. expose `busy`, `done`, and `error`

That is enough to validate the architectural split before introducing:

- richer queueing
- multiple outstanding contexts
- host-visible completion rings
- real GPU DMA interactions

## What Should Not Move Into the Adapter

The adapter should not absorb responsibilities that belong elsewhere.

It should not own:

- table storage
- bank switching
- time-triggered entry progression
- host/processor/MAC arbitration
- generic processor-app multiplexing across slots

Those should remain in:

- `exec_table`
- `tt_scheduler`
- `comm_datapath`
- `processor_runtime`

## Migration Path

Recommended sequence:

1. keep `processor_runtime` as the stable generic shell
2. keep stand-in engines working under the standardized slot interface
3. introduce one `processor_adapter` as another app-slot implementation
4. validate that the adapter and stand-in engines can coexist
5. gradually move more workloads from stand-in engines to the adapter

This keeps the paper architecture stable while the implementation moves toward
a real processor-backed runtime.
