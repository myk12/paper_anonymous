import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge


AXIS_DATA_WIDTH = 64
AXIS_KEEP_WIDTH = 8
AXIS_USER_WIDTH = 1
PROC_ETHERTYPE0 = 0x88B5


def bit(sig):
    return int(sig.value)


def build_frame_word(ethertype, payload=0x0):
    data = 0
    data |= 0xAA << (0 * 8)
    data |= 0xBB << (1 * 8)
    data |= 0xCC << (2 * 8)
    data |= 0xDD << (3 * 8)
    data |= 0xEE << (4 * 8)
    data |= 0xFF << (5 * 8)
    data |= 0x11 << (6 * 8)
    data |= 0x22 << (7 * 8)
    # For AXIS_DATA_WIDTH=64 and default offset 12, the ethertype sits outside
    # this beat in a real Ethernet frame.  The test overrides the bytes at the
    # configured slice location directly so the classifier logic is exercised.
    data |= payload << 16
    return data


def build_classifier_beat(ethertype):
    data = 0
    start = 0
    data |= (((ethertype >> 8) & 0xFF) << start)
    data |= ((ethertype & 0xFF) << (start + 8))
    return data


async def reset_dut(dut):
    dut.i_enable.value = 0
    dut.s_axis_dma_tx_tdata.value = 0
    dut.s_axis_dma_tx_tkeep.value = 0
    dut.s_axis_dma_tx_tvalid.value = 0
    dut.s_axis_dma_tx_tlast.value = 0
    dut.s_axis_dma_tx_tuser.value = 0
    dut.s_axis_processor_tx_tdata.value = 0
    dut.s_axis_processor_tx_tkeep.value = 0
    dut.s_axis_processor_tx_tvalid.value = 0
    dut.s_axis_processor_tx_tlast.value = 0
    dut.s_axis_processor_tx_tuser.value = 0
    dut.i_processor_tx_valid.value = 0
    dut.m_axis_mac_tx_tready.value = 0
    dut.s_axis_mac_rx_tdata.value = 0
    dut.s_axis_mac_rx_tkeep.value = 0
    dut.s_axis_mac_rx_tvalid.value = 0
    dut.s_axis_mac_rx_tlast.value = 0
    dut.s_axis_mac_rx_tuser.value = 0
    dut.m_axis_dma_rx_tready.value = 0
    dut.m_axis_processor_rx_tready.value = 0

    dut.rst.value = 1
    for _ in range(5):
        await RisingEdge(dut.clk)
    dut.rst.value = 0
    for _ in range(2):
        await RisingEdge(dut.clk)


@cocotb.test()
async def test_comm_datapath_tx_prefers_processor_and_locks_frame(dut):
    cocotb.start_soon(Clock(dut.clk, 4, unit="ns").start())
    await reset_dut(dut)

    dut.i_enable.value = 1
    dut.m_axis_mac_tx_tready.value = 1

    dut.s_axis_dma_tx_tdata.value = 0x1111222233334444
    dut.s_axis_dma_tx_tkeep.value = 0xFF
    dut.s_axis_dma_tx_tvalid.value = 1
    dut.s_axis_dma_tx_tlast.value = 0

    dut.s_axis_processor_tx_tdata.value = 0xAAAABBBBCCCCDDDD
    dut.s_axis_processor_tx_tkeep.value = 0x0F
    dut.s_axis_processor_tx_tvalid.value = 1
    dut.s_axis_processor_tx_tlast.value = 0
    dut.s_axis_processor_tx_tuser.value = 1
    dut.i_processor_tx_valid.value = 1
    await RisingEdge(dut.clk)

    assert dut.m_axis_mac_tx_tdata.value.to_unsigned() == 0xAAAABBBBCCCCDDDD
    assert dut.m_axis_mac_tx_tkeep.value.to_unsigned() == 0x0F
    assert bit(dut.m_axis_mac_tx_tvalid) == 1
    assert bit(dut.s_axis_processor_tx_tready) == 1
    assert bit(dut.s_axis_dma_tx_tready) == 0
    assert bit(dut.o_processor_tx_selected) == 1

    # Hold processor selection for the rest of the frame even if host remains valid.
    dut.s_axis_processor_tx_tdata.value = 0x9999000011112222
    dut.s_axis_processor_tx_tlast.value = 1
    await RisingEdge(dut.clk)
    assert dut.m_axis_mac_tx_tdata.value.to_unsigned() == 0x9999000011112222
    assert bit(dut.s_axis_processor_tx_tready) == 1


@cocotb.test()
async def test_comm_datapath_rx_routes_processor_owned_frame(dut):
    cocotb.start_soon(Clock(dut.clk, 4, unit="ns").start())
    await reset_dut(dut)

    dut.i_enable.value = 1
    dut.m_axis_dma_rx_tready.value = 1
    dut.m_axis_processor_rx_tready.value = 1

    dut.s_axis_mac_rx_tdata.value = build_classifier_beat(PROC_ETHERTYPE0)
    dut.s_axis_mac_rx_tkeep.value = 0xFF
    dut.s_axis_mac_rx_tvalid.value = 1
    dut.s_axis_mac_rx_tlast.value = 0
    await RisingEdge(dut.clk)

    assert bit(dut.m_axis_processor_rx_tvalid) == 1
    assert bit(dut.m_axis_dma_rx_tvalid) == 0
    assert bit(dut.s_axis_mac_rx_tready) == 1

    dut.s_axis_mac_rx_tdata.value = 0xDEADBEEFCAFEBABE
    dut.s_axis_mac_rx_tlast.value = 1
    await RisingEdge(dut.clk)

    assert dut.m_axis_processor_rx_tdata.value.to_unsigned() == 0xDEADBEEFCAFEBABE
    assert bit(dut.m_axis_processor_rx_tlast) == 1
    assert bit(dut.m_axis_dma_rx_tvalid) == 0


@cocotb.test()
async def test_comm_datapath_disable_bypasses_processor_path(dut):
    cocotb.start_soon(Clock(dut.clk, 4, unit="ns").start())
    await reset_dut(dut)

    dut.i_enable.value = 0
    dut.m_axis_mac_tx_tready.value = 1
    dut.m_axis_dma_rx_tready.value = 1

    # TX bypass: host should flow directly to MAC.
    dut.s_axis_dma_tx_tdata.value = 0x0123456789ABCDEF
    dut.s_axis_dma_tx_tkeep.value = 0xFF
    dut.s_axis_dma_tx_tvalid.value = 1
    dut.s_axis_dma_tx_tlast.value = 1
    await RisingEdge(dut.clk)

    assert dut.m_axis_mac_tx_tdata.value.to_unsigned() == 0x0123456789ABCDEF
    assert bit(dut.m_axis_mac_tx_tvalid) == 1
    assert bit(dut.s_axis_dma_tx_tready) == 1
    assert bit(dut.s_axis_processor_tx_tready) == 0

    # RX bypass: all RX should return to host when disabled.
    dut.s_axis_mac_rx_tdata.value = build_classifier_beat(PROC_ETHERTYPE0)
    dut.s_axis_mac_rx_tkeep.value = 0xFF
    dut.s_axis_mac_rx_tvalid.value = 1
    dut.s_axis_mac_rx_tlast.value = 1
    await RisingEdge(dut.clk)

    assert bit(dut.m_axis_dma_rx_tvalid) == 1
    assert bit(dut.m_axis_processor_rx_tvalid) == 0
    assert bit(dut.s_axis_mac_rx_tready) == 1
