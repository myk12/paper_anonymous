# SPDX-License-Identifier: BSD-2-Clause-Views
# Copyright (c) 2019-2024 The Regents of the University of California

# TDMA BER module

foreach inst [get_cells -hier -filter {(ORIG_REF_NAME == mqnic_tdma_ber || REF_NAME == mqnic_tdma_ber)}] {
    puts "Inserting timing constraints for mqnic_tdma_ber instance $inst"

    # get clock periods
    set clk [get_clocks -of_objects [get_cells "$inst/acc_en_reg_reg"]]

    set clk_period [if {[llength $clk]} {get_property -min PERIOD $clk} {expr 1.0}]

    # control synchronization
    set_property ASYNC_REG TRUE [get_cells -hier -regexp ".*/ch\\\[\\d*\\\].phy_cfg_(rx|tx)_prbs31_enable_reg_reg" -filter "PARENT == $inst"]

    set_false_path -from [get_cells "$inst/cfg_tx_prbs31_enable_reg_reg[*]"] -to [get_cells "$inst/ch[*].phy_cfg_tx_prbs31_enable_reg_reg"]
    set_false_path -from [get_cells "$inst/cfg_rx_prbs31_enable_reg_reg[*]"] -to [get_cells "$inst/ch[*].phy_cfg_rx_prbs31_enable_reg_reg"]

    # data synchronization
    set_property ASYNC_REG TRUE [get_cells -hier -regexp ".*/ch\\\[\\d*\\\].rx_flag_sync_reg_\[123\]_reg" -filter "PARENT == $inst"]
    set_property ASYNC_REG TRUE [get_cells -hier -regexp ".*/ch\\\[\\d*\\\].phy_rx_error_count_sync_reg_reg\\\[\\d*\\\]" -filter "PARENT == $inst"]

    set_max_delay -from [get_cells "$inst/ch[*].phy_rx_flag_reg_reg"] -to [get_cells "$inst/ch[*].rx_flag_sync_reg_1_reg"] -datapath_only $clk_period
    set_max_delay -from [get_cells "$inst/ch[*].phy_rx_error_count_reg_reg[*]"] -to [get_cells "$inst/ch[*].phy_rx_error_count_sync_reg_reg[*]"] -datapath_only $clk_period
}
