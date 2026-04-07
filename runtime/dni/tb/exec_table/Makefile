# SPDX-License-Identifier: BSD-2-Clause-Views

TOPLEVEL_LANG = verilog

SIM ?= icarus
WAVES ?= 1

COCOTB_HDL_TIMEUNIT = 1ns
COCOTB_HDL_TIMEPRECISION = 1ps

TOPLEVEL = exec_table
MODULE = test_exec_table
COCOTB_TEST_MODULES = test_exec_table

VERILOG_SOURCES += ../../rtl/core/exec_table.v

COCOTB_MAKEFILES := $(shell cocotb-config --makefiles 2>/dev/null)

ifeq ($(strip $(COCOTB_MAKEFILES)),)
$(error cocotb and cocotb-config are required to run this testbench)
endif

include $(COCOTB_MAKEFILES)/Makefile.sim
