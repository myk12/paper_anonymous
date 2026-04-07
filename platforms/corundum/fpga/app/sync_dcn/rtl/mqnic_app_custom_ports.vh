// SPDX-License-Identifier: BSD-2-Clause-Views
/*
 * Copyright (c) 2023 Missing Link Electronics, Inc.
 *
 * Template verilog header containing definitions for custom app ports.
 * See fpga/mqnic/ZCU102/fpga/fpga_app_custom_port_demo for an example design.
 *
 * The macros defined within this file and mqnic_app_custom_params.vh allow
 * users to add custom ports and parameters to the mqnic_app_block. The
 * additional ports and parameters are added and propagated throughout
 * hierarchical modules of mqnic, starting from the toplevel mqnic_core modules:
 *   - mqnic_core_axi
 *   - mqnic_core_pcie_ptile
 *   - mqnic_core_pcie_s10
 *   - mqnic_core_pcie_us
 *
 * Usage:
 * 1. Enable custom app ports by adding the following line to config.tcl:
 *        set_property VERILOG_DEFINE  {APP_CUSTOM_PORTS_ENABLE} [get_filesets sources_1]
 *    For custom parameters, add:
 *        set_property VERILOG_DEFINE  {APP_CUSTOM_PARAMS_ENABLE} [get_filesets sources_1]
 */

// Custom port list (direction, name, width)
`define APP_CUSTOM_PORTS(X_PORT) \
    X_PORT(input,  template_input,  32) \
    X_PORT(output, template_output, 32)
