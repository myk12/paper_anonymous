#!/usr/bin/env python
# SPDX-License-Identifier: BSD-2-Clause-Views
# Copyright (c) 2020-2024 The Regents of the University of California

import logging
import os

import cocotb_test.simulator

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, Timer
from cocotb.regression import TestFactory

from cocotbext.eth import PtpClockSimTime


class TB(object):
    def __init__(self, dut):
        self.dut = dut

        self.log = logging.getLogger("cocotb.tb")
        self.log.setLevel(logging.DEBUG)

        cocotb.start_soon(Clock(dut.clk, 4, units="ns").start())

        self.ptp_clock = PtpClockSimTime(ts_tod=dut.ptp_ts_tod, clock=dut.clk)
        dut.ptp_ts_tod_step.setimmediatevalue(0)

        dut.ctrl_reg_wr_addr.setimmediatevalue(0)
        dut.ctrl_reg_wr_data.setimmediatevalue(0)
        dut.ctrl_reg_wr_strb.setimmediatevalue(0)
        dut.ctrl_reg_wr_en.setimmediatevalue(0)
        dut.ctrl_reg_rd_addr.setimmediatevalue(0)
        dut.ctrl_reg_rd_en.setimmediatevalue(0)

        dut.phy_rx_error_count.setimmediatevalue(0)

        for ch in dut.ch:
            cocotb.start_soon(Clock(ch.ch_phy_tx_clk, 6.4, units="ns").start())
            cocotb.start_soon(Clock(ch.ch_phy_rx_clk, 6.4, units="ns").start())
            ch.ch_phy_rx_error_count.setimmediatevalue(0)

    async def wr_ctrl_reg(self, addr, val):
        self.dut.ctrl_reg_wr_addr.value = addr
        self.dut.ctrl_reg_wr_data.value = val
        self.dut.ctrl_reg_wr_strb.value = 0xf
        self.dut.ctrl_reg_wr_en.value = 1
        await RisingEdge(self.dut.clk)
        self.dut.ctrl_reg_wr_en.value = 0
        await RisingEdge(self.dut.clk)

    async def rd_ctrl_reg(self, addr):
        self.dut.ctrl_reg_rd_addr.value = addr
        self.dut.ctrl_reg_rd_en.value = 1
        await RisingEdge(self.dut.clk)
        self.dut.ctrl_reg_rd_en.value = 0
        await RisingEdge(self.dut.clk)
        return self.dut.ctrl_reg_rd_data.value.integer

    async def dump_counters(self):
        cycles = await self.rd_ctrl_reg(0x5C)

        self.log.info("Cycles: %d", cycles)

        bits = []
        errors = []
        for ch in range(self.dut.COUNT.value):
            b = await self.rd_ctrl_reg(0x80+ch*16)
            e = await self.rd_ctrl_reg(0x84+ch*16)

            self.log.info("Ch %d bits: %d errors: %d", ch, b, e)

            bits.append(b)
            errors.append(e)

        return cycles, bits, errors

    async def dump_timeslot_counters(self, index=0):
        await self.wr_ctrl_reg(0x58, index)

        bits = []
        errors = []
        for ch in range(self.dut.COUNT.value):
            b = await self.rd_ctrl_reg(0x88+ch*16)
            e = await self.rd_ctrl_reg(0x8C+ch*16)

            self.log.info("Ch %d index %d bits: %d errors: %d", ch, index, b, e)

            bits.append(b)
            errors.append(e)

        return bits, errors

    async def clear_timeslot_counters(self, index=0):
        await self.wr_ctrl_reg(0x58, index | 0x80000000)

    async def reset(self):
        self.dut.rst.setimmediatevalue(0)
        await RisingEdge(self.dut.clk)
        await RisingEdge(self.dut.clk)
        self.dut.rst.value = 1
        await RisingEdge(self.dut.clk)
        await RisingEdge(self.dut.clk)
        self.dut.rst.value = 0
        await RisingEdge(self.dut.clk)
        await RisingEdge(self.dut.clk)


async def run_test_config(dut):

    tb = TB(dut)

    await tb.reset()

    assert await tb.rd_ctrl_reg(0x50) == 0x00000000
    assert await tb.rd_ctrl_reg(0x54) == 0x00000000

    await tb.wr_ctrl_reg(0x50, 0xffffffff)
    await tb.wr_ctrl_reg(0x54, 0xffffffff)

    await RisingEdge(dut.clk)
    await RisingEdge(dut.clk)


async def run_test_sched(dut):

    tb = TB(dut)

    await tb.reset()

    tb.log.info("Test scheduler")

    await tb.wr_ctrl_reg(0x24, 0)
    await tb.wr_ctrl_reg(0x28, 0)
    await tb.wr_ctrl_reg(0x2C, 0)
    await tb.wr_ctrl_reg(0x34, 2000)
    await tb.wr_ctrl_reg(0x38, 400)
    await tb.wr_ctrl_reg(0x3C, 300)
    await tb.wr_ctrl_reg(0x1C, 0x00000001)

    await Timer(10000, 'ns')

    await RisingEdge(dut.clk)
    await RisingEdge(dut.clk)


async def run_test_counters(dut):

    tb = TB(dut)

    await tb.reset()

    await tb.wr_ctrl_reg(0x50, 0xffffffff)
    await tb.wr_ctrl_reg(0x54, 0xffffffff)

    await tb.wr_ctrl_reg(0x24, 0)
    await tb.wr_ctrl_reg(0x28, 0)
    await tb.wr_ctrl_reg(0x2C, 0)
    await tb.wr_ctrl_reg(0x34, 2000)
    await tb.wr_ctrl_reg(0x38, 400)
    await tb.wr_ctrl_reg(0x3C, 300)
    await tb.wr_ctrl_reg(0x1C, 0x00000001)

    while not dut.tdma_schedule_start.value:
        await RisingEdge(dut.clk)

    tb.log.info("Test error counts")

    for k in range(5):
        await tb.clear_timeslot_counters(k)

    await tb.dump_counters()
    for k in range(5):
        await tb.dump_timeslot_counters(k)

    await tb.wr_ctrl_reg(0x4C, 0x00000001)

    await Timer(10000, 'ns')

    await tb.dump_counters()
    for k in range(5):
        await tb.dump_timeslot_counters(k)

    for ch in dut.ch:
        ch.ch_phy_rx_error_count.value = 1

    await Timer(10000, 'ns')

    for ch in dut.ch:
        ch.ch_phy_rx_error_count.value = 0

    await tb.wr_ctrl_reg(0x4C, 0x00000000)

    await tb.dump_counters()
    for k in range(5):
        await tb.dump_timeslot_counters(k)

    tb.log.info("Change duty cycle")

    await tb.wr_ctrl_reg(0x3C, 200)

    await tb.dump_counters()
    for k in range(5):
        await tb.dump_timeslot_counters(k)

    await tb.wr_ctrl_reg(0x4C, 0x00000001)

    for ch in dut.ch:
        ch.ch_phy_rx_error_count.value = 1

    await Timer(10000, 'ns')

    for ch in dut.ch:
        ch.ch_phy_rx_error_count.value = 0

    await tb.wr_ctrl_reg(0x4C, 0x00000000)

    await tb.dump_counters()
    for k in range(5):
        await tb.dump_timeslot_counters(k)

    await tb.wr_ctrl_reg(0x3C, 300)

    await RisingEdge(dut.clk)
    await RisingEdge(dut.clk)


async def run_test_slices(dut):

    tb = TB(dut)

    await tb.reset()

    await tb.wr_ctrl_reg(0x50, 0xffffffff)
    await tb.wr_ctrl_reg(0x54, 0xffffffff)

    await tb.wr_ctrl_reg(0x24, 0)
    await tb.wr_ctrl_reg(0x28, 0)
    await tb.wr_ctrl_reg(0x2C, 0)
    await tb.wr_ctrl_reg(0x34, 2000)
    await tb.wr_ctrl_reg(0x38, 400)
    await tb.wr_ctrl_reg(0x3C, 300)
    await tb.wr_ctrl_reg(0x1C, 0x00000001)

    while not dut.tdma_schedule_start.value:
        await RisingEdge(dut.clk)

    tb.log.info("Test slices")

    for k in range(5*4):
        await tb.clear_timeslot_counters(k)

    await tb.dump_counters()
    for k in range(5*4):
        await tb.dump_timeslot_counters(k)

    await tb.wr_ctrl_reg(0x60, 50)
    await tb.wr_ctrl_reg(0x64, 100)
    await tb.wr_ctrl_reg(0x68, 2)

    await tb.wr_ctrl_reg(0x4C, 0x00000003)

    await Timer(10000, 'ns')

    await tb.dump_counters()
    for k in range(5*4):
        await tb.dump_timeslot_counters(k)

    for ch in dut.ch:
        ch.ch_phy_rx_error_count.value = 1

    await Timer(10000, 'ns')

    for ch in dut.ch:
        ch.ch_phy_rx_error_count.value = 0

    await tb.wr_ctrl_reg(0x4C, 0x00000000)

    await tb.dump_counters()
    for k in range(5*4):
        await tb.dump_timeslot_counters(k)

    await RisingEdge(dut.clk)
    await RisingEdge(dut.clk)


if cocotb.SIM_NAME:

    for test in [
                run_test_config,
                run_test_sched,
                run_test_counters,
                run_test_slices,
            ]:
        factory = TestFactory(test)
        factory.generate_tests()


# cocotb-test

tests_dir = os.path.dirname(__file__)
rtl_dir = os.path.abspath(os.path.join(tests_dir, '..', '..', 'rtl'))
lib_dir = os.path.abspath(os.path.join(rtl_dir, '..', 'lib'))
axi_rtl_dir = os.path.abspath(os.path.join(lib_dir, 'axi', 'rtl'))
axis_rtl_dir = os.path.abspath(os.path.join(lib_dir, 'axis', 'rtl'))
eth_rtl_dir = os.path.abspath(os.path.join(lib_dir, 'eth', 'rtl'))
pcie_rtl_dir = os.path.abspath(os.path.join(lib_dir, 'pcie', 'rtl'))


def test_mqnic_tdma_ber(request):
    dut = "mqnic_tdma_ber"
    module = os.path.splitext(os.path.basename(__file__))[0]
    toplevel = dut

    verilog_sources = [
        os.path.join(rtl_dir, f"{dut}.v"),
        os.path.join(rtl_dir, "tdma_scheduler.v"),
    ]

    parameters = {}

    parameters['COUNT'] = 4
    parameters['TDMA_INDEX_W'] = 6
    parameters['ERR_BITS'] = 66
    parameters['ERR_CNT_W'] = (parameters['ERR_BITS']-1).bit_length()
    parameters['RAM_SIZE'] = 1024
    parameters['PHY_PIPELINE'] = 2

    parameters['REG_ADDR_WIDTH'] = 16
    parameters['REG_DATA_WIDTH'] = 32
    parameters['REG_STRB_WIDTH'] = (parameters['REG_DATA_WIDTH']/8)
    parameters['RB_BASE_ADDR'] = 0
    parameters['RB_NEXT_PTR'] = 0

    extra_env = {f'PARAM_{k}': str(v) for k, v in parameters.items()}

    sim_build = os.path.join(tests_dir, "sim_build",
        request.node.name.replace('[', '-').replace(']', ''))

    cocotb_test.simulator.run(
        python_search=[tests_dir],
        verilog_sources=verilog_sources,
        toplevel=toplevel,
        module=module,
        parameters=parameters,
        sim_build=sim_build,
        extra_env=extra_env,
    )
