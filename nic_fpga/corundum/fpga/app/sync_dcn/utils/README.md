# Sync-DCN Utility Flow

Use these directories in order:

1. [`system_input/README.md`](/Users/mayuke/Project/OpticalDCN/infra/nic_fpga/corundum/fpga/app/sync_dcn/utils/system_input/README.md)
2. [`global_co_compiler/README.md`](/Users/mayuke/Project/OpticalDCN/infra/nic_fpga/corundum/fpga/app/sync_dcn/utils/global_co_compiler/README.md)
3. [`experiment_flow/README.md`](/Users/mayuke/Project/OpticalDCN/infra/nic_fpga/corundum/fpga/app/sync_dcn/utils/experiment_flow/README.md)
4. [`host_control_plane/README.md`](/Users/mayuke/Project/OpticalDCN/infra/nic_fpga/corundum/fpga/app/sync_dcn/utils/host_control_plane/README.md)
5. [`per_node_lowering/README.md`](/Users/mayuke/Project/OpticalDCN/infra/nic_fpga/corundum/fpga/app/sync_dcn/utils/per_node_lowering/README.md)
6. [`visualization/README.md`](/Users/mayuke/Project/OpticalDCN/infra/nic_fpga/corundum/fpga/app/sync_dcn/utils/visualization/README.md)

Roles:

- `system_input`: split-input loaders and builders
- `global_co_compiler`: topology-aware global planning
- `experiment_flow`: workspace generation and artifact materialization
- `host_control_plane`: MMIO helpers and programming CLI
- `per_node_lowering`: local JSON ABI lowering helpers
- `visualization`: schedule export
