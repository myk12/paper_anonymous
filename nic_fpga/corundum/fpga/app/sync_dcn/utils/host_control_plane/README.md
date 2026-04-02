# Host Control Plane Stage

This directory contains the active host-side programming path.

Active tools:

- [`sync_dcn_host.py`](/Users/mayuke/Project/OpticalDCN/infra/nic_fpga/corundum/fpga/app/sync_dcn/utils/host_control_plane/sync_dcn_host.py)
  - backend-agnostic MMIO helper and ABI packer
- [`sync_dcn_program.py`](/Users/mayuke/Project/OpticalDCN/infra/nic_fpga/corundum/fpga/app/sync_dcn/utils/host_control_plane/sync_dcn_program.py)
  - manifest-aware command-line programmer

This stage performs:

- AXI-Lite register writes
- execution-table loading
- AI trace-table loading
- bank arming
- status readback

The hardware counterpart is
[`sync_dcn_subsystem.v`](/Users/mayuke/Project/OpticalDCN/infra/nic_fpga/corundum/fpga/app/sync_dcn/rtl/sync_dcn_subsystem.v).
