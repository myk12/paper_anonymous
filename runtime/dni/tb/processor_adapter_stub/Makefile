TOPLEVEL_LANG = verilog
VERILOG_SOURCES += ../../rtl/core/processor_adapter_stub.v
TOPLEVEL = processor_adapter_stub
MODULE = test_processor_adapter_stub
COCOTB_TEST_MODULES = test_processor_adapter_stub

COMPILE_ARGS += -P$(TOPLEVEL).AXIS_DATA_WIDTH=64
COMPILE_ARGS += -P$(TOPLEVEL).AXIS_KEEP_WIDTH=8
COMPILE_ARGS += -P$(TOPLEVEL).AXIS_TX_USER_WIDTH=1
COMPILE_ARGS += -P$(TOPLEVEL).AXIS_RX_USER_WIDTH=1
COMPILE_ARGS += -P$(TOPLEVEL).STATUS_WIDTH=32

COCOTB_MAKEFILES := $(shell cocotb-config --makefiles 2>/dev/null)
include $(COCOTB_MAKEFILES)/Makefile.sim
