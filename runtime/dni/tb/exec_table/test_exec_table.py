import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge


WORD_START_LO = 0
WORD_START_HI = 1
WORD_END_LO = 2
WORD_END_HI = 3
WORD_META = 4
WORD_ROUTE = 5
WORD_FLOW = 6


async def reset_dut(dut):
    dut.i_active_bank.value = 0

    dut.cfg_tx_wr_en.value = 0
    dut.cfg_tx_wr_bank.value = 0
    dut.cfg_tx_wr_entry.value = 0
    dut.cfg_tx_wr_word.value = 0
    dut.cfg_tx_wr_data.value = 0
    dut.cfg_tx_rd_bank.value = 0
    dut.cfg_tx_rd_entry.value = 0
    dut.cfg_tx_rd_word.value = 0

    dut.cfg_rx_wr_en.value = 0
    dut.cfg_rx_wr_bank.value = 0
    dut.cfg_rx_wr_entry.value = 0
    dut.cfg_rx_wr_word.value = 0
    dut.cfg_rx_wr_data.value = 0
    dut.cfg_rx_rd_bank.value = 0
    dut.cfg_rx_rd_entry.value = 0
    dut.cfg_rx_rd_word.value = 0

    dut.i_tx_eval_entry.value = 0
    dut.i_rx_eval_entry.value = 0

    dut.rst.value = 1
    for _ in range(5):
        await RisingEdge(dut.clk)
    dut.rst.value = 0
    for _ in range(2):
        await RisingEdge(dut.clk)


async def write_tx_word(dut, bank, entry, word, data):
    dut.cfg_tx_wr_bank.value = bank
    dut.cfg_tx_wr_entry.value = entry
    dut.cfg_tx_wr_word.value = word
    dut.cfg_tx_wr_data.value = data
    dut.cfg_tx_wr_en.value = 1
    await RisingEdge(dut.clk)
    dut.cfg_tx_wr_en.value = 0
    await RisingEdge(dut.clk)


async def write_rx_word(dut, bank, entry, word, data):
    dut.cfg_rx_wr_bank.value = bank
    dut.cfg_rx_wr_entry.value = entry
    dut.cfg_rx_wr_word.value = word
    dut.cfg_rx_wr_data.value = data
    dut.cfg_rx_wr_en.value = 1
    await RisingEdge(dut.clk)
    dut.cfg_rx_wr_en.value = 0
    await RisingEdge(dut.clk)


async def read_tx_word(dut, bank, entry, word):
    dut.cfg_tx_rd_bank.value = bank
    dut.cfg_tx_rd_entry.value = entry
    dut.cfg_tx_rd_word.value = word
    await RisingEdge(dut.clk)
    return dut.cfg_tx_rd_data.value.to_unsigned()


async def read_rx_word(dut, bank, entry, word):
    dut.cfg_rx_rd_bank.value = bank
    dut.cfg_rx_rd_entry.value = entry
    dut.cfg_rx_rd_word.value = word
    await RisingEdge(dut.clk)
    return dut.cfg_rx_rd_data.value.to_unsigned()


async def write_entry_words(write_fn, dut, bank, entry, words):
    for word, data in words.items():
        await write_fn(dut, bank, entry, word, data)


def make_entry(seed):
    return {
        WORD_START_LO: seed + 0x01,
        WORD_START_HI: seed + 0x02,
        WORD_END_LO: seed + 0x03,
        WORD_END_HI: seed + 0x04,
        WORD_META: seed + 0x05,
        WORD_ROUTE: seed + 0x06,
        WORD_FLOW: seed + 0x07,
    }


@cocotb.test()
async def test_exec_table_banked_storage_and_readout(dut):
    cocotb.start_soon(Clock(dut.clk, 4, unit="ns").start())
    await reset_dut(dut)

    tx_bank0 = {
        WORD_START_LO: 0x11111111,
        WORD_START_HI: 0x22222222,
        WORD_END_LO: 0x33333333,
        WORD_END_HI: 0x44444444,
        WORD_META: 0x55555555,
        WORD_ROUTE: 0x66666666,
        WORD_FLOW: 0x77777777,
    }
    tx_bank1 = {
        WORD_START_LO: 0xAAAA0001,
        WORD_START_HI: 0xAAAA0002,
        WORD_END_LO: 0xAAAA0003,
        WORD_END_HI: 0xAAAA0004,
        WORD_META: 0xAAAA0005,
        WORD_ROUTE: 0xAAAA0006,
        WORD_FLOW: 0xAAAA0007,
    }
    rx_bank0 = {
        WORD_START_LO: 0xBBBB0001,
        WORD_START_HI: 0xBBBB0002,
        WORD_END_LO: 0xBBBB0003,
        WORD_END_HI: 0xBBBB0004,
        WORD_META: 0xBBBB0005,
        WORD_ROUTE: 0xBBBB0006,
        WORD_FLOW: 0xBBBB0007,
    }
    rx_bank1 = {
        WORD_START_LO: 0xCCCC0001,
        WORD_START_HI: 0xCCCC0002,
        WORD_END_LO: 0xCCCC0003,
        WORD_END_HI: 0xCCCC0004,
        WORD_META: 0xCCCC0005,
        WORD_ROUTE: 0xCCCC0006,
        WORD_FLOW: 0xCCCC0007,
    }

    await write_entry_words(write_tx_word, dut, 0, 0, tx_bank0)
    await write_entry_words(write_tx_word, dut, 1, 0, tx_bank1)
    await write_entry_words(write_rx_word, dut, 0, 0, rx_bank0)
    await write_entry_words(write_rx_word, dut, 1, 0, rx_bank1)

    for word, data in tx_bank0.items():
        assert await read_tx_word(dut, 0, 0, word) == data
    for word, data in tx_bank1.items():
        assert await read_tx_word(dut, 1, 0, word) == data
    for word, data in rx_bank0.items():
        assert await read_rx_word(dut, 0, 0, word) == data
    for word, data in rx_bank1.items():
        assert await read_rx_word(dut, 1, 0, word) == data

    dut.i_tx_eval_entry.value = 0
    dut.i_rx_eval_entry.value = 0

    dut.i_active_bank.value = 0
    await RisingEdge(dut.clk)
    assert dut.o_tx_word_start_lo.value.to_unsigned() == tx_bank0[WORD_START_LO]
    assert dut.o_tx_word_meta.value.to_unsigned() == tx_bank0[WORD_META]
    assert dut.o_rx_word_start_lo.value.to_unsigned() == rx_bank0[WORD_START_LO]
    assert dut.o_rx_word_route.value.to_unsigned() == rx_bank0[WORD_ROUTE]

    dut.i_active_bank.value = 1
    await RisingEdge(dut.clk)
    assert dut.o_tx_word_start_lo.value.to_unsigned() == tx_bank1[WORD_START_LO]
    assert dut.o_tx_word_meta.value.to_unsigned() == tx_bank1[WORD_META]
    assert dut.o_rx_word_start_lo.value.to_unsigned() == rx_bank1[WORD_START_LO]
    assert dut.o_rx_word_route.value.to_unsigned() == rx_bank1[WORD_ROUTE]


@cocotb.test()
async def test_exec_table_tx_rx_isolation(dut):
    cocotb.start_soon(Clock(dut.clk, 4, unit="ns").start())
    await reset_dut(dut)

    await write_tx_word(dut, 0, 1, WORD_ROUTE, 0x12345678)
    await write_rx_word(dut, 0, 1, WORD_ROUTE, 0x87654321)

    dut.i_active_bank.value = 0
    dut.i_tx_eval_entry.value = 1
    dut.i_rx_eval_entry.value = 1
    await RisingEdge(dut.clk)

    assert dut.o_tx_word_route.value.to_unsigned() == 0x12345678
    assert dut.o_rx_word_route.value.to_unsigned() == 0x87654321


@cocotb.test()
async def test_exec_table_multiple_entries_and_bank_switching(dut):
    cocotb.start_soon(Clock(dut.clk, 4, unit="ns").start())
    await reset_dut(dut)

    tx_entry0_bank0 = make_entry(0x1000)
    tx_entry1_bank0 = make_entry(0x2000)
    tx_entry0_bank1 = make_entry(0x3000)
    rx_entry0_bank0 = make_entry(0x4000)
    rx_entry1_bank1 = make_entry(0x5000)

    await write_entry_words(write_tx_word, dut, 0, 0, tx_entry0_bank0)
    await write_entry_words(write_tx_word, dut, 0, 1, tx_entry1_bank0)
    await write_entry_words(write_tx_word, dut, 1, 0, tx_entry0_bank1)
    await write_entry_words(write_rx_word, dut, 0, 0, rx_entry0_bank0)
    await write_entry_words(write_rx_word, dut, 1, 1, rx_entry1_bank1)

    dut.i_active_bank.value = 0
    dut.i_tx_eval_entry.value = 0
    dut.i_rx_eval_entry.value = 0
    await RisingEdge(dut.clk)
    assert dut.o_tx_word_meta.value.to_unsigned() == tx_entry0_bank0[WORD_META]
    assert dut.o_rx_word_meta.value.to_unsigned() == rx_entry0_bank0[WORD_META]

    dut.i_tx_eval_entry.value = 1
    await RisingEdge(dut.clk)
    assert dut.o_tx_word_meta.value.to_unsigned() == tx_entry1_bank0[WORD_META]

    dut.i_active_bank.value = 1
    dut.i_tx_eval_entry.value = 0
    dut.i_rx_eval_entry.value = 1
    await RisingEdge(dut.clk)
    assert dut.o_tx_word_meta.value.to_unsigned() == tx_entry0_bank1[WORD_META]
    assert dut.o_rx_word_meta.value.to_unsigned() == rx_entry1_bank1[WORD_META]


@cocotb.test()
async def test_exec_table_reset_clears_storage(dut):
    cocotb.start_soon(Clock(dut.clk, 4, unit="ns").start())
    await reset_dut(dut)

    await write_tx_word(dut, 0, 3, WORD_META, 0xDEADBEEF)
    await write_rx_word(dut, 1, 2, WORD_ROUTE, 0xCAFEBABE)

    dut.rst.value = 1
    for _ in range(2):
        await RisingEdge(dut.clk)
    dut.rst.value = 0
    await RisingEdge(dut.clk)

    assert await read_tx_word(dut, 0, 3, WORD_META) == 0
    assert await read_rx_word(dut, 1, 2, WORD_ROUTE) == 0
