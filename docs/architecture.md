# Utopia Repository Architecture

This repository is organized around the architectural split in Utopia:

- an asynchronous control plane that admits jobs, validates topology, compiles
  schedules, lowers artifacts, and installs them into the runtime
- a synchronous dataplane that executes those artifacts across endpoints and
  the network fabric

## Module Overview

### `toolchain/`

Compile-time and deployment-time logic. This includes system-input parsing,
topology validation, future global compilation and lowering passes, and
orchestration scripts that install artifacts into hosts and switches.

### `runtime/`

Runtime dataplane components. The main focus is the deterministic network
interface (DNI), which enforces locally installed communication epochs against
the shared time base, together with the scheduled network fabric runtime.

Within the DNI, the current architectural split is:

- `exec_table`: banked endpoint-local execution-table storage and programming
  access
- `tt_scheduler`: time-triggered execution, bank switching, and window
  progression
- `processor_runtime`: the endpoint-local runtime boundary to a future local
  processor, currently realized with standardized app slots and prototype
  stand-in engines
- `comm_datapath`: the generic host/processor/MAC communication boundary

The current FPGA prototype still uses workload-specific stand-in engines such
as consensus and AI replay, but those engines now sit below the
`processor_runtime` boundary rather than defining it.

### `host/`

Host-side components that remain in the asynchronous control plane. In the
current layout, this directory intentionally contains only two layers:

- `control_plane/`: host-side logic that installs artifacts and programs the
  runtime
- `nic_driver/`: the kernel driver interface layer used to access the DNI
  platform from the host side

### `platforms/`

Underlying hardware substrates that Utopia builds on. These are not the system
architecture themselves, but implementation bases used to realize it.

### `workloads/`

Representative applications and case studies, such as consensus and structured
AI communication.
