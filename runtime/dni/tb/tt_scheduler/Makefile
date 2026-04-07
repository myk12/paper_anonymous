# SPDX-License-Identifier: BSD-2-Clause-Views

TOPLEVEL_LANG = verilog

SIM ?= icarus
WAVES ?= 1
COMPILE_ARGS += -I../../rtl

COCOTB_HDL_TIMEUNIT = 1ns
COCOTB_HDL_TIMEPRECISION = 1ps

TOPLEVEL = tt_scheduler
MODULE = test_tt_scheduler
COCOTB_TEST_MODULES = test_tt_scheduler

VERILOG_SOURCES += ../../rtl/core/tt_scheduler.v

COCOTB_MAKEFILES := $(shell cocotb-config --makefiles 2>/dev/null)

ifeq ($(strip $(COCOTB_MAKEFILES)),)
$(error cocotb and cocotb-config are required to run this testbench)
endif

include $(COCOTB_MAKEFILES)/Makefile.sim
