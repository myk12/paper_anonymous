import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ReadOnly, NextTimeStep


def bit(sig):
    return int(sig.value)


async def reset_dut(dut):
    dut.i_enable.value = 0

    dut.i_tx_window_id.value = 0
    dut.i_tx_window_open_pulse.value = 0
    dut.i_tx_window_close_pulse.value = 0
    dut.i_tx_commit_start_pulse.value = 0
    dut.i_tx_allowed.value = 0
    dut.i_tx_active.value = 0
    dut.i_tx_opcode.value = 0
    dut.i_tx_context_id.value = 0

    dut.i_rx_window_id.value = 0
    dut.i_rx_window_open_pulse.value = 0
    dut.i_rx_window_close_pulse.value = 0
    dut.i_rx_commit_start_pulse.value = 0
    dut.i_rx_enabled.value = 0
    dut.i_rx_active.value = 0
    dut.i_rx_opcode.value = 0
    dut.i_rx_context_id.value = 0

    dut.m_axis_tx_tready.value = 0
    dut.s_axis_rx_tdata.value = 0
    dut.s_axis_rx_tkeep.value = 0
    dut.s_axis_rx_tvalid.value = 0
    dut.s_axis_rx_tlast.value = 0
    dut.s_axis_rx_tuser.value = 0

    dut.rst.value = 1
    for _ in range(5):
        await RisingEdge(dut.clk)
    dut.rst.value = 0
    for _ in range(2):
        await RisingEdge(dut.clk)


async def sample_after_edge(dut):
    await RisingEdge(dut.clk)
    await ReadOnly()


@cocotb.test()
async def test_processor_adapter_stub_tx_launch_and_complete(dut):
    cocotb.start_soon(Clock(dut.clk, 4, unit="ns").start())
    await reset_dut(dut)

    dut.i_enable.value = 1
    dut.i_tx_window_id.value = 0x1234
    dut.i_tx_window_open_pulse.value = 1
    dut.i_tx_allowed.value = 1
    dut.i_tx_active.value = 1
    dut.i_tx_opcode.value = 0x20
    dut.i_tx_context_id.value = 0x55AA
    dut.m_axis_tx_tready.value = 1
    await sample_after_edge(dut)

    assert bit(dut.o_busy) == 1
    assert bit(dut.m_axis_tx_tvalid) == 1
    assert dut.m_axis_tx_tdata.value.to_unsigned() == 0x1234
    assert bit(dut.m_axis_tx_tlast) == 1

    await NextTimeStep()
    dut.i_tx_window_open_pulse.value = 0

    await sample_after_edge(dut)
    assert bit(dut.o_done) == 1
    assert bit(dut.o_busy) == 0

    await sample_after_edge(dut)
    assert dut.o_status.value.to_unsigned() & 0x3 == 0


@cocotb.test()
async def test_processor_adapter_stub_rx_accept_and_complete(dut):
    cocotb.start_soon(Clock(dut.clk, 4, unit="ns").start())
    await reset_dut(dut)

    dut.i_enable.value = 1
    dut.i_rx_window_id.value = 0x2222
    dut.i_rx_window_open_pulse.value = 1
    dut.i_rx_enabled.value = 1
    dut.i_rx_active.value = 1
    dut.i_rx_opcode.value = 0x21
    dut.i_rx_context_id.value = 0x3344
    await sample_after_edge(dut)

    assert bit(dut.o_busy) == 1
    assert bit(dut.s_axis_rx_tready) == 1

    await NextTimeStep()
    dut.i_rx_window_open_pulse.value = 0

    dut.s_axis_rx_tdata.value = 0xDEADBEEFCAFEBABE
    dut.s_axis_rx_tkeep.value = 0xFF
    dut.s_axis_rx_tvalid.value = 1
    dut.s_axis_rx_tlast.value = 1
    await sample_after_edge(dut)

    assert bit(dut.o_done) == 1
    assert bit(dut.o_busy) == 0
    assert bit(dut.s_axis_rx_tready) == 0


@cocotb.test()
async def test_processor_adapter_stub_commit_closes_active_tx(dut):
    cocotb.start_soon(Clock(dut.clk, 4, unit="ns").start())
    await reset_dut(dut)

    dut.i_enable.value = 1
    dut.i_tx_window_id.value = 0x9999
    dut.i_tx_window_open_pulse.value = 1
    dut.i_tx_allowed.value = 1
    dut.i_tx_active.value = 1
    dut.i_tx_opcode.value = 0x30
    dut.i_tx_context_id.value = 0x1010
    dut.m_axis_tx_tready.value = 0
    await sample_after_edge(dut)

    assert bit(dut.o_busy) == 1

    await NextTimeStep()
    dut.i_tx_window_open_pulse.value = 0

    dut.i_tx_commit_start_pulse.value = 1
    await sample_after_edge(dut)
    assert bit(dut.o_done) == 1
    assert bit(dut.o_busy) == 0

    await NextTimeStep()
    dut.i_tx_commit_start_pulse.value = 0
    assert bit(dut.m_axis_tx_tvalid) == 0
