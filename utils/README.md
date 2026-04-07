# Shared Utilities

This directory contains small cross-cutting helper modules shared across the
repository.

Only lightweight helpers that are reused by multiple subsystems should live
here. Subsystem-specific logic should remain in its owning module, such as
`toolchain/`, `host/`, or `runtime/`.
