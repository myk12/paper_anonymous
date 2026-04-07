# SPDX-License-Identifier: BSD-2-Clause-Views

TOPLEVEL_LANG = verilog

SIM ?= icarus
WAVES ?= 1
COMPILE_ARGS += -I../../../../..

COCOTB_HDL_TIMEUNIT = 1ns
COCOTB_HDL_TIMEPRECISION = 1ps

TOPLEVEL = dni_subsystem
COCOTB_TEST_MODULES = test_dni_subsystem

VERILOG_SOURCES += ../../rtl/core/exec_table.v
VERILOG_SOURCES += ../../rtl/core/tt_scheduler.v
VERILOG_SOURCES += ../../rtl/apps/consensus_core.v
VERILOG_SOURCES += ../../rtl/apps/consensus_tx.v
VERILOG_SOURCES += ../../rtl/apps/consensus_rx.v
VERILOG_SOURCES += ../../rtl/apps/consensus_node.v
VERILOG_SOURCES += ../../rtl/apps/ai_trace_replay.v
VERILOG_SOURCES += ../../rtl/core/dni_tx_dest_format.v
VERILOG_SOURCES += ../../rtl/core/processor_runtime.v
VERILOG_SOURCES += ../../rtl/core/processor_adapter_stub.v
VERILOG_SOURCES += ../../rtl/core/comm_datapath.v
VERILOG_SOURCES += ../../rtl/dni_subsystem.v
VERILOG_SOURCES += ../../../../lib/axi/rtl/axil_reg_if_rd.v
VERILOG_SOURCES += ../../../../lib/axi/rtl/axil_reg_if_wr.v
VERILOG_SOURCES += ../../../../lib/axi/rtl/axil_reg_if.v

export PARAM_PTP_TS_WIDTH := 96
export PARAM_P_NODE_ID := 0
export PARAM_PORTS_PER_IF := 1
export PARAM_TX_TAG_WIDTH := 16
export PARAM_AXIL_APP_CTRL_DATA_WIDTH := 32
export PARAM_AXIL_APP_CTRL_ADDR_WIDTH := 16
export PARAM_AXIL_APP_CTRL_STRB_WIDTH := 4
export PARAM_AXIS_IF_DATA_WIDTH := 512
export PARAM_AXIS_IF_KEEP_WIDTH := 64
export PARAM_AXIS_IF_TX_ID_WIDTH := 12
export PARAM_AXIS_IF_RX_ID_WIDTH := 1
export PARAM_AXIS_IF_TX_DEST_WIDTH := 5
export PARAM_AXIS_IF_RX_DEST_WIDTH := 8
export PARAM_AXIS_IF_TX_USER_WIDTH := 1
export PARAM_AXIS_IF_RX_USER_WIDTH := 1

COCOTB_MAKEFILES := $(shell cocotb-config --makefiles 2>/dev/null)

ifeq ($(strip $(COCOTB_MAKEFILES)),)
$(error cocotb and cocotb-config are required to run this testbench)
endif

include $(COCOTB_MAKEFILES)/Makefile.sim
