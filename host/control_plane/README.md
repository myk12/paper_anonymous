# Host Control Plane Stage

This directory contains the active host-side programming path.

Active tools:

- [`sync_dcn_host.py`](sync_dcn_host.py)
  - backend-agnostic MMIO helper and ABI packer
- [`sync_dcn_program.py`](sync_dcn_program.py)
  - manifest-aware command-line programmer

This stage performs:

- AXI-Lite register writes
- execution-table loading
- AI trace-table loading
- bank arming
- status readback

The hardware counterpart is
[`dni_subsystem.v`](../../runtime/dni/rtl/dni_subsystem.v).
