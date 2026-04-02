# Active Subsystem Verification

Active cocotb sign-off boundary for Sync-DCN.

Scope:

- direct verification of [`sync_dcn_subsystem.v`](/Users/mayuke/Project/OpticalDCN/infra/nic_fpga/corundum/fpga/app/sync_dcn/rtl/sync_dcn_subsystem.v)
- AXI-Lite ABI checks
- schedule-executor behavior
- AI TX / AI RX behavior
- consensus round behavior

Start with:

1. [`test_sync_dcn_subsystem.py`](/Users/mayuke/Project/OpticalDCN/infra/nic_fpga/corundum/fpga/app/sync_dcn/tb/sync_dcn_subsystem/test_sync_dcn_subsystem.py)
2. [`fixtures/comprehensive_ai_lifecycle.json`](/Users/mayuke/Project/OpticalDCN/infra/nic_fpga/corundum/fpga/app/sync_dcn/tb/sync_dcn_subsystem/fixtures/comprehensive_ai_lifecycle.json)
3. [`fixtures/comprehensive_consensus_round.json`](/Users/mayuke/Project/OpticalDCN/infra/nic_fpga/corundum/fpga/app/sync_dcn/tb/sync_dcn_subsystem/fixtures/comprehensive_consensus_round.json)

Key tests:

- `test_compiled_program_lifecycle_end_to_end`
- `test_compiled_consensus_round_end_to_end`
- `test_consensus_quorum_fail_then_clear_halt_and_recover`
