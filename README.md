# Utopia: A Datacenter-Scale Synchronous Dataplane

This repository is the reference implementation of Utopia, a system that
separates an asynchronous control plane from a synchronous dataplane spanning
the processor runtime, deterministic network interface, and scheduled network
fabric.

## Repository Layout

- `toolchain/`: compile-time pipeline, including topology validation,
  orchestration, and future compiler/lowering stages
- `runtime/`: synchronous dataplane components, including the deterministic
  network interface (DNI) and fabric runtime
- `host/`: host-side asynchronous control components, including the control
  plane and NIC driver interface layer
- `platforms/`: third-party or substrate implementations that Utopia builds on,
  including the Corundum codebase
- `workloads/`: representative workload integrations and case studies
- `configs/`: topology and deployment descriptions
- `docs/`: architecture notes and developer-facing documentation
- `utils/`: shared helper modules and utilities

## Design Mapping

At a high level, the repository mirrors the architecture described in the
paper:

- the asynchronous control plane lives primarily in `toolchain/` and `host/`
- the synchronous dataplane lives primarily in `runtime/`
- substrate-specific implementation code lives in `platforms/`
- workload-specific integrations live in `workloads/`

## Current Status

This repository is being reorganized around the system architecture of the
paper. Some modules are still thin wrappers around earlier prototypes and will
continue to be migrated into the layout above.
