import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge


APP_COUNT = 2
AXIS_DATA_WIDTH = 64
AXIS_KEEP_WIDTH = 8
AXIS_USER_WIDTH = 1


def bit(sig):
    return int(sig.value)


def set_app_tx(dut, slot, *, data, keep, valid, last, user):
    base_d = slot * AXIS_DATA_WIDTH
    base_k = slot * AXIS_KEEP_WIDTH
    base_u = slot * AXIS_USER_WIDTH
    current_data = dut.s_axis_app_tx_tdata.value.to_unsigned()
    current_keep = dut.s_axis_app_tx_tkeep.value.to_unsigned()
    current_user = dut.s_axis_app_tx_tuser.value.to_unsigned()

    data_mask = ((1 << AXIS_DATA_WIDTH) - 1) << base_d
    keep_mask = ((1 << AXIS_KEEP_WIDTH) - 1) << base_k
    user_mask = ((1 << AXIS_USER_WIDTH) - 1) << base_u

    dut.s_axis_app_tx_tdata.value = (current_data & ~data_mask) | ((data & ((1 << AXIS_DATA_WIDTH) - 1)) << base_d)
    dut.s_axis_app_tx_tkeep.value = (current_keep & ~keep_mask) | ((keep & ((1 << AXIS_KEEP_WIDTH) - 1)) << base_k)
    dut.s_axis_app_tx_tuser.value = (current_user & ~user_mask) | ((user & ((1 << AXIS_USER_WIDTH) - 1)) << base_u)

    tx_valid = dut.s_axis_app_tx_tvalid.value.to_unsigned()
    tx_last = dut.s_axis_app_tx_tlast.value.to_unsigned()
    if valid:
        tx_valid |= (1 << slot)
    else:
        tx_valid &= ~(1 << slot)
    if last:
        tx_last |= (1 << slot)
    else:
        tx_last &= ~(1 << slot)
    dut.s_axis_app_tx_tvalid.value = tx_valid
    dut.s_axis_app_tx_tlast.value = tx_last


def app_rx_slice(sig, slot, width):
    return (sig.value.to_unsigned() >> (slot * width)) & ((1 << width) - 1)


def packed_bit(sig, slot):
    return (sig.value.to_unsigned() >> slot) & 0x1


async def reset_dut(dut):
    dut.i_enable.value = 0
    dut.i_tx_current_window_id.value = 0
    dut.i_tx_window_open_pulse.value = 0
    dut.i_tx_commit_start_pulse.value = 0
    dut.i_tx_window_close_pulse.value = 0
    dut.i_tx_allowed.value = 0
    dut.i_tx_app_id.value = 0
    dut.i_tx_opcode.value = 0
    dut.i_tx_context_id.value = 0
    dut.i_rx_current_window_id.value = 0
    dut.i_rx_window_open_pulse.value = 0
    dut.i_rx_commit_start_pulse.value = 0
    dut.i_rx_window_close_pulse.value = 0
    dut.i_rx_enabled.value = 0
    dut.i_rx_app_id.value = 0
    dut.i_rx_opcode.value = 0
    dut.i_rx_context_id.value = 0
    dut.s_axis_processor_rx_tdata.value = 0
    dut.s_axis_processor_rx_tkeep.value = 0
    dut.s_axis_processor_rx_tvalid.value = 0
    dut.s_axis_processor_rx_tlast.value = 0
    dut.s_axis_processor_rx_tuser.value = 0
    dut.m_axis_processor_tx_tready.value = 0
    dut.s_axis_app_tx_tdata.value = 0
    dut.s_axis_app_tx_tkeep.value = 0
    dut.s_axis_app_tx_tvalid.value = 0
    dut.s_axis_app_tx_tlast.value = 0
    dut.s_axis_app_tx_tuser.value = 0
    dut.m_axis_app_rx_tready.value = 0

    dut.rst.value = 1
    for _ in range(5):
        await RisingEdge(dut.clk)
    dut.rst.value = 0
    for _ in range(2):
        await RisingEdge(dut.clk)


@cocotb.test()
async def test_processor_runtime_control_fanout(dut):
    cocotb.start_soon(Clock(dut.clk, 4, unit="ns").start())
    await reset_dut(dut)

    dut.i_enable.value = 1
    dut.i_tx_current_window_id.value = 0x1122
    dut.i_tx_window_open_pulse.value = 1
    dut.i_tx_commit_start_pulse.value = 1
    dut.i_tx_window_close_pulse.value = 0
    dut.i_tx_allowed.value = 1
    dut.i_tx_app_id.value = 2
    dut.i_tx_opcode.value = 0x20
    dut.i_tx_context_id.value = 0x3344

    dut.i_rx_current_window_id.value = 0x5566
    dut.i_rx_window_open_pulse.value = 1
    dut.i_rx_commit_start_pulse.value = 0
    dut.i_rx_window_close_pulse.value = 1
    dut.i_rx_enabled.value = 1
    dut.i_rx_app_id.value = 1
    dut.i_rx_opcode.value = 0x11
    dut.i_rx_context_id.value = 0x7788
    await RisingEdge(dut.clk)

    # TX targets slot 1 (app_id 2 -> slot 1)
    assert ((dut.o_app_tx_window_id.value.to_unsigned() >> 64) & ((1 << 64) - 1)) == 0x1122
    assert packed_bit(dut.o_app_tx_window_open_pulse, 1) == 1
    assert packed_bit(dut.o_app_tx_commit_start_pulse, 1) == 1
    assert packed_bit(dut.o_app_tx_allowed, 1) == 1
    assert packed_bit(dut.o_app_tx_active, 1) == 1
    assert app_rx_slice(dut.o_app_tx_opcode, 1, 8) == 0x20
    assert app_rx_slice(dut.o_app_tx_context_id, 1, 16) == 0x3344
    assert packed_bit(dut.o_app_tx_window_open_pulse, 0) == 0
    assert packed_bit(dut.o_app_tx_active, 0) == 0

    # RX targets slot 0 (app_id 1 -> slot 0)
    assert (dut.o_app_rx_window_id.value.to_unsigned() & ((1 << 64) - 1)) == 0x5566
    assert packed_bit(dut.o_app_rx_window_open_pulse, 0) == 1
    assert packed_bit(dut.o_app_rx_window_close_pulse, 0) == 1
    assert packed_bit(dut.o_app_rx_enabled, 0) == 1
    assert packed_bit(dut.o_app_rx_active, 0) == 1
    assert app_rx_slice(dut.o_app_rx_opcode, 0, 8) == 0x11
    assert app_rx_slice(dut.o_app_rx_context_id, 0, 16) == 0x7788
    assert packed_bit(dut.o_app_rx_active, 1) == 0


@cocotb.test()
async def test_processor_runtime_tx_mux_selects_active_app(dut):
    cocotb.start_soon(Clock(dut.clk, 4, unit="ns").start())
    await reset_dut(dut)

    dut.i_enable.value = 1
    dut.i_tx_allowed.value = 1
    dut.i_tx_app_id.value = 2
    dut.i_tx_opcode.value = 0x20
    dut.m_axis_processor_tx_tready.value = 1

    set_app_tx(dut, 0, data=0x1111222233334444, keep=0x0F, valid=1, last=1, user=0)
    set_app_tx(dut, 1, data=0xAAAABBBBCCCCDDDD, keep=0xFF, valid=1, last=1, user=1)
    await RisingEdge(dut.clk)

    assert dut.m_axis_processor_tx_tdata.value.to_unsigned() == 0xAAAABBBBCCCCDDDD
    assert dut.m_axis_processor_tx_tkeep.value.to_unsigned() == 0xFF
    assert bit(dut.m_axis_processor_tx_tvalid) == 1
    assert bit(dut.m_axis_processor_tx_tlast) == 1
    assert bit(dut.m_axis_processor_tx_tuser) == 1
    assert bit(dut.o_processor_tx_valid) == 1
    assert dut.s_axis_app_tx_tready.value.to_unsigned() == 0b10


@cocotb.test()
async def test_processor_runtime_rx_demux_selects_active_app(dut):
    cocotb.start_soon(Clock(dut.clk, 4, unit="ns").start())
    await reset_dut(dut)

    dut.i_enable.value = 1
    dut.i_rx_enabled.value = 1
    dut.i_rx_app_id.value = 1
    dut.i_rx_opcode.value = 0x11
    dut.s_axis_processor_rx_tdata.value = 0x123456789ABCDEF0
    dut.s_axis_processor_rx_tkeep.value = 0xFF
    dut.s_axis_processor_rx_tvalid.value = 1
    dut.s_axis_processor_rx_tlast.value = 1
    dut.s_axis_processor_rx_tuser.value = 1
    dut.m_axis_app_rx_tready.value = 0b01
    await RisingEdge(dut.clk)

    assert app_rx_slice(dut.m_axis_app_rx_tdata, 0, AXIS_DATA_WIDTH) == 0x123456789ABCDEF0
    assert app_rx_slice(dut.m_axis_app_rx_tkeep, 0, AXIS_KEEP_WIDTH) == 0xFF
    assert packed_bit(dut.m_axis_app_rx_tvalid, 0) == 1
    assert packed_bit(dut.m_axis_app_rx_tlast, 0) == 1
    assert app_rx_slice(dut.m_axis_app_rx_tuser, 0, AXIS_USER_WIDTH) == 1
    assert bit(dut.s_axis_processor_rx_tready) == 1
    assert packed_bit(dut.m_axis_app_rx_tvalid, 1) == 0
