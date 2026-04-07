# System Input Stage

This directory contains the user-facing inputs for the active flow.

Tools:

- [`sync_dcn_build_moe_model_experiment.py`](sync_dcn_build_moe_model_experiment.py)
  - expands a SimAI-inspired MoE model description into a global compiler input
- [`sync_dcn_load_system_input.py`](sync_dcn_load_system_input.py)
  - loads monolithic input or split-input bundles
- [`sync_dcn_build_consensus_periodic_experiment.py`](sync_dcn_build_consensus_periodic_experiment.py)
  - expands compact consensus configuration into repeated rounds

Recommended examples:

- [`examples/moe_model_8node_split/system_input_bundle.json`](examples/moe_model_8node_split/system_input_bundle.json)
- [`examples/mixtral_full_inference_consensus_split/system_input_bundle.json`](examples/mixtral_full_inference_consensus_split/system_input_bundle.json)

Recommended format:

- workload specification
- processor timing model
- topology & fabric model

Output of this stage:

- a normalized global compiler input suitable for
  [`sync_dcn_global_compile.py`](../compiler/sync_dcn_global_compile.py)
