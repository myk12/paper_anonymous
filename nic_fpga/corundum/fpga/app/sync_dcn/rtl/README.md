# Active RTL Walkthrough

This directory contains the active deterministic-NIC RTL.

Read in this order:

1. [`mqnic_app_block_sync_dcn.v`](/Users/mayuke/Project/OpticalDCN/infra/nic_fpga/corundum/fpga/app/sync_dcn/rtl/mqnic_app_block_sync_dcn.v)
   - integration shell
2. [`sync_dcn_subsystem.v`](/Users/mayuke/Project/OpticalDCN/infra/nic_fpga/corundum/fpga/app/sync_dcn/rtl/sync_dcn_subsystem.v)
   - top-level subsystem and register/table ABI
3. [`sync_schedule_executor.v`](/Users/mayuke/Project/OpticalDCN/infra/nic_fpga/corundum/fpga/app/sync_dcn/rtl/sync_schedule_executor.v)
   - split TX/RX schedule execution
4. [`sync_dcn_apps.v`](/Users/mayuke/Project/OpticalDCN/infra/nic_fpga/corundum/fpga/app/sync_dcn/rtl/sync_dcn_apps.v)
   - NIC-side plugin layer
5. [`sync_dcn_datapath.v`](/Users/mayuke/Project/OpticalDCN/infra/nic_fpga/corundum/fpga/app/sync_dcn/rtl/sync_dcn_datapath.v)
   - host/app datapath boundary

Plugin modules:

- [`ai_trace_replay.v`](/Users/mayuke/Project/OpticalDCN/infra/nic_fpga/corundum/fpga/app/sync_dcn/rtl/ai_trace_replay.v)
- [`consensus_node.v`](/Users/mayuke/Project/OpticalDCN/infra/nic_fpga/corundum/fpga/app/sync_dcn/rtl/consensus_node.v)
- [`sync_app_tx_dispatch.v`](/Users/mayuke/Project/OpticalDCN/infra/nic_fpga/corundum/fpga/app/sync_dcn/rtl/sync_app_tx_dispatch.v)
- [`sync_app_rx_dispatch.v`](/Users/mayuke/Project/OpticalDCN/infra/nic_fpga/corundum/fpga/app/sync_dcn/rtl/sync_app_rx_dispatch.v)
