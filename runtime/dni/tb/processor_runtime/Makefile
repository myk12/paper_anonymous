# SPDX-License-Identifier: MIT

TOPLEVEL_LANG = verilog

SIM ?= icarus
WAVES ?= 1
COMPILE_ARGS += -I../../rtl

COCOTB_HDL_TIMEUNIT = 1ns
COCOTB_HDL_TIMEPRECISION = 1ps

TOPLEVEL = processor_runtime
MODULE = test_processor_runtime
COCOTB_TEST_MODULES = test_processor_runtime

VERILOG_SOURCES += ../../rtl/core/processor_runtime.v

COMPILE_ARGS += -P$(TOPLEVEL).AXIS_DATA_WIDTH=64
COMPILE_ARGS += -P$(TOPLEVEL).AXIS_KEEP_WIDTH=8
COMPILE_ARGS += -P$(TOPLEVEL).AXIS_TX_USER_WIDTH=1
COMPILE_ARGS += -P$(TOPLEVEL).AXIS_RX_USER_WIDTH=1
COMPILE_ARGS += -P$(TOPLEVEL).APP_COUNT=2

COCOTB_MAKEFILES := $(shell cocotb-config --makefiles 2>/dev/null)
include $(COCOTB_MAKEFILES)/Makefile.sim
