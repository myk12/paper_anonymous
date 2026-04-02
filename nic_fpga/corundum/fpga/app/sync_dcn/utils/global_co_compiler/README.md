# Global Co-Compiler Stage

This directory contains the global co-compiler that bridges split system input
and multi-target execution artifacts.

Active tool:

- [`sync_dcn_global_compile.py`](/Users/mayuke/Project/OpticalDCN/infra/nic_fpga/corundum/fpga/app/sync_dcn/utils/global_co_compiler/sync_dcn_global_compile.py)
  - consumes normalized workload, processor, and topology input
  - produces a human-readable global plan
  - lowers the result into processor, NIC, and fabric views

Current output:

- `global_plan`
- `per_node_programs`
- `per_node_high_level_specs`
- metadata used by the experiment-flow stage to emit:
  - `results/processor`
  - `results/nic`
  - `results/fabric`

Most users should reach this stage through
[`sync_dcn_prepare_experiment.py`](/Users/mayuke/Project/OpticalDCN/infra/nic_fpga/corundum/fpga/app/sync_dcn/utils/experiment_flow/sync_dcn_prepare_experiment.py)
instead of invoking the compiler directly.
