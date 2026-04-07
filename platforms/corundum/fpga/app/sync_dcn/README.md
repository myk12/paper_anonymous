# Corundum Integration Stub for Utopia

This directory is intentionally kept as a thin integration stub inside the
Corundum subtree.

Utopia-specific sources have been migrated into the repository's system-level
layout:

- DNI runtime RTL and testbenches live under
  [`runtime/dni/`](../../../../../runtime/dni/README.md)
- compile-time logic lives under
  [`toolchain/`](../../../../../toolchain/README.md)
- host-side control logic lives under
  [`host/`](../../../../../host/README.md)

If Corundum-specific build glue is needed later, it should remain minimal here
and reference the canonical sources above rather than duplicating them.
