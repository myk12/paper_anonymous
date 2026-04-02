# SPDX-License-Identifier: BSD-2-Clause-Views
# Copyright (c) 2020-2023 The Regents of the University of California

TOPLEVEL_LANG = verilog

SIM ?= icarus
WAVES ?= 0

COCOTB_HDL_TIMEUNIT = 1ns
COCOTB_HDL_TIMEPRECISION = 1ps

DUT      = tx_scheduler_rr
TOPLEVEL = $(DUT)
MODULE   = test_$(DUT)
VERILOG_SOURCES += ../../rtl/$(DUT).v
VERILOG_SOURCES += ../../lib/axis/rtl/axis_fifo.v
VERILOG_SOURCES += ../../lib/axis/rtl/priority_encoder.v

# module parameters
export PARAM_LEN_WIDTH := 16
export PARAM_REQ_DEST_WIDTH := 8
export PARAM_REQ_TAG_WIDTH := 8
export PARAM_QUEUE_INDEX_WIDTH := 6
export PARAM_PIPELINE := 2
export PARAM_SCHED_CTRL_ENABLE := 1
export PARAM_REQ_DEST_DEFAULT := 0
export PARAM_MAX_TX_SIZE := 9216
export PARAM_FC_SCALE := 64

export PARAM_AXIL_BASE_ADDR := 0
export PARAM_AXIL_DATA_WIDTH := 32
export PARAM_AXIL_ADDR_WIDTH := $(shell expr $(PARAM_QUEUE_INDEX_WIDTH) + 2 )
export PARAM_AXIL_STRB_WIDTH := $(shell expr $(PARAM_AXIL_DATA_WIDTH) / 8 )

export PARAM_REG_ADDR_WIDTH := 12
export PARAM_REG_DATA_WIDTH := $(PARAM_AXIL_DATA_WIDTH)
export PARAM_REG_STRB_WIDTH := $(shell expr $(PARAM_REG_DATA_WIDTH) / 8 )
export PARAM_RB_BLOCK_TYPE := $(shell echo $$((0x0000C040)) )
export PARAM_RB_BASE_ADDR := 0
export PARAM_RB_NEXT_PTR := 0

ifeq ($(SIM), icarus)
	PLUSARGS += -fst

	COMPILE_ARGS += $(foreach v,$(filter PARAM_%,$(.VARIABLES)),-P $(TOPLEVEL).$(subst PARAM_,,$(v))=$($(v)))

	ifeq ($(WAVES), 1)
		VERILOG_SOURCES += iverilog_dump.v
		COMPILE_ARGS += -s iverilog_dump
	endif
else ifeq ($(SIM), verilator)
	COMPILE_ARGS += -Wno-SELRANGE -Wno-WIDTH

	COMPILE_ARGS += $(foreach v,$(filter PARAM_%,$(.VARIABLES)),-G$(subst PARAM_,,$(v))=$($(v)))

	ifeq ($(WAVES), 1)
		COMPILE_ARGS += --trace-fst
	endif
endif

include $(shell cocotb-config --makefiles)/Makefile.sim

iverilog_dump.v:
	echo 'module iverilog_dump();' > $@
	echo 'initial begin' >> $@
	echo '    $$dumpfile("$(TOPLEVEL).fst");' >> $@
	echo '    $$dumpvars(0, $(TOPLEVEL));' >> $@
	echo 'end' >> $@
	echo 'endmodule' >> $@

clean::
	@rm -rf iverilog_dump.v
	@rm -rf dump.fst $(TOPLEVEL).fst
