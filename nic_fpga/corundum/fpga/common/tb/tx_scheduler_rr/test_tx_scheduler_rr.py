#!/usr/bin/env python
# SPDX-License-Identifier: BSD-2-Clause-Views
# Copyright (c) 2020-2023 The Regents of the University of California

import itertools
import logging
import os

import cocotb_test.simulator

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge
from cocotb.regression import TestFactory

from cocotbext.axi import AxiLiteBus, AxiLiteMaster
from cocotbext.axi.stream import define_stream


TxReqBus, TxReqTransaction, TxReqSource, TxReqSink, TxReqMonitor = define_stream("TxReq",
    signals=["queue", "dest", "tag", "valid"],
    optional_signals=["ready"]
)


TxStatusBus, TxStatusTransaction, TxStatusSource, TxStatusSink, TxStatusMonitor = define_stream("TxStatus",
    signals=["queue", "tag", "valid"],
    optional_signals=["empty", "error", "len", "ready"]
)


DoorbellBus, DoorbellTransaction, DoorbellSource, DoorbellSink, DoorbellMonitor = define_stream("Doorbell",
    signals=["queue", "valid"],
    optional_signals=["ready"]
)


CtrlBus, CtrlTransaction, CtrlSource, CtrlSink, CtrlMonitor = define_stream("Ctrl",
    signals=["queue", "enable", "valid"],
    optional_signals=["ready"]
)


class TB(object):
    def __init__(self, dut):
        self.dut = dut

        self.log = logging.getLogger("cocotb.tb")
        self.log.setLevel(logging.DEBUG)

        cocotb.start_soon(Clock(dut.clk, 4, units="ns").start())

        self.tx_req_sink = TxReqSink(TxReqBus.from_prefix(dut, "m_axis_tx_req"), dut.clk, dut.rst)
        self.tx_status_dequeue_source = TxStatusSource(TxStatusBus.from_prefix(dut, "s_axis_tx_status_dequeue"), dut.clk, dut.rst)
        self.tx_status_start_source = TxStatusSource(TxStatusBus.from_prefix(dut, "s_axis_tx_status_start"), dut.clk, dut.rst)
        self.tx_status_finish_source = TxStatusSource(TxStatusBus.from_prefix(dut, "s_axis_tx_status_finish"), dut.clk, dut.rst)

        self.doorbell_source = DoorbellSource(DoorbellBus.from_prefix(dut, "s_axis_doorbell"), dut.clk, dut.rst)

        self.ctrl_source = CtrlSource(CtrlBus.from_prefix(dut, "s_axis_sched_ctrl"), dut.clk, dut.rst)

        self.axil_master = AxiLiteMaster(AxiLiteBus.from_prefix(dut, "s_axil"), dut.clk, dut.rst)

        dut.enable.setimmediatevalue(0)

    def set_idle_generator(self, generator=None):
        if generator:
            self.tx_status_dequeue_source.set_pause_generator(generator())
            self.tx_status_start_source.set_pause_generator(generator())
            self.tx_status_finish_source.set_pause_generator(generator())

    def set_backpressure_generator(self, generator=None):
        if generator:
            self.tx_req_sink.set_pause_generator(generator())

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


MQNIC_RB_SCHED_RR_REG_OFFSET        = 0x0C
MQNIC_RB_SCHED_RR_REG_QUEUE_COUNT   = 0x10
MQNIC_RB_SCHED_RR_REG_QUEUE_STRIDE  = 0x14
MQNIC_RB_SCHED_RR_REG_CTRL          = 0x18
MQNIC_RB_SCHED_RR_REG_CFG           = 0x1C
MQNIC_RB_SCHED_RR_REG_CH_STRIDE     = 0x10
MQNIC_RB_SCHED_RR_REG_CH0_CTRL      = 0x20
MQNIC_RB_SCHED_RR_REG_CH0_FC1       = 0x24
MQNIC_RB_SCHED_RR_REG_CH0_FC1_DEST  = 0x24
MQNIC_RB_SCHED_RR_REG_CH0_FC1_PB    = 0x26
MQNIC_RB_SCHED_RR_REG_CH0_FC2       = 0x28
MQNIC_RB_SCHED_RR_REG_CH0_FC2_DB    = 0x28
MQNIC_RB_SCHED_RR_REG_CH0_FC2_PL    = 0x2A
MQNIC_RB_SCHED_RR_REG_CH0_FC3       = 0x2C
MQNIC_RB_SCHED_RR_REG_CH0_FC3_DL    = 0x2C

MQNIC_SCHED_RR_PORT_TC         = (0x7 << 0)
MQNIC_SCHED_RR_PORT_EN         = (1 << 3)
MQNIC_SCHED_RR_PORT_PAUSE      = (1 << 4)
MQNIC_SCHED_RR_PORT_SCHEDULED  = (1 << 5)
MQNIC_SCHED_RR_QUEUE_EN        = (1 << 6)
MQNIC_SCHED_RR_QUEUE_PAUSE     = (1 << 7)
MQNIC_SCHED_RR_QUEUE_ACTIVE    = (1 << 14)

MQNIC_SCHED_RR_CMD_SET_PORT_TC       = 0x80010000
MQNIC_SCHED_RR_CMD_SET_PORT_ENABLE   = 0x80020000
MQNIC_SCHED_RR_CMD_SET_PORT_PAUSE    = 0x80030000
MQNIC_SCHED_RR_CMD_SET_QUEUE_ENABLE  = 0x40000100
MQNIC_SCHED_RR_CMD_SET_QUEUE_PAUSE   = 0x40000200


async def run_test_config(dut):

    tb = TB(dut)

    await tb.reset()

    # enable
    assert await tb.rd_ctrl_reg(MQNIC_RB_SCHED_RR_REG_CTRL) == 0
    await tb.wr_ctrl_reg(MQNIC_RB_SCHED_RR_REG_CTRL, 1)
    assert await tb.rd_ctrl_reg(MQNIC_RB_SCHED_RR_REG_CTRL) == 1

    val = await tb.rd_ctrl_reg(MQNIC_RB_SCHED_RR_REG_CH0_CTRL)
    tb.log.info("CTRL: %08x", val)
    val = await tb.rd_ctrl_reg(MQNIC_RB_SCHED_RR_REG_CH0_FC1)
    tb.log.info("FC1: %08x", val)
    val = await tb.rd_ctrl_reg(MQNIC_RB_SCHED_RR_REG_CH0_FC2)
    tb.log.info("FC2: %08x", val)
    val = await tb.rd_ctrl_reg(MQNIC_RB_SCHED_RR_REG_CH0_FC3)
    tb.log.info("FC3: %08x", val)

    assert await tb.axil_master.read_dword(0*4) == 0
    await tb.axil_master.write_dword(0*4, MQNIC_SCHED_RR_CMD_SET_PORT_TC | (0 << 8) | 0)
    await tb.axil_master.write_dword(0*4, MQNIC_SCHED_RR_CMD_SET_PORT_ENABLE | (0 << 8) | 1)
    await tb.axil_master.write_dword(0*4, MQNIC_SCHED_RR_CMD_SET_QUEUE_ENABLE | 1)
    assert await tb.axil_master.read_dword(0*4) == 0x00000048

    await RisingEdge(dut.clk)
    await RisingEdge(dut.clk)


async def run_test_single(dut, idle_inserter=None, backpressure_inserter=None):

    tb = TB(dut)

    await tb.reset()

    tb.set_idle_generator(idle_inserter)
    tb.set_backpressure_generator(backpressure_inserter)

    dut.enable.value = 1
    await tb.wr_ctrl_reg(MQNIC_RB_SCHED_RR_REG_CH0_FC2, (25 << 16) | ((1536+63)//64))
    await tb.wr_ctrl_reg(MQNIC_RB_SCHED_RR_REG_CH0_FC3, ((1536+63)//64)*32)
    await tb.wr_ctrl_reg(MQNIC_RB_SCHED_RR_REG_CH0_CTRL, 1)
    await tb.wr_ctrl_reg(MQNIC_RB_SCHED_RR_REG_CTRL, 1)

    await tb.axil_master.write_dword(0*4, MQNIC_SCHED_RR_CMD_SET_PORT_TC | (0 << 8) | 0)
    await tb.axil_master.write_dword(0*4, MQNIC_SCHED_RR_CMD_SET_PORT_ENABLE | (0 << 8) | 1)
    await tb.axil_master.write_dword(0*4, MQNIC_SCHED_RR_CMD_SET_QUEUE_ENABLE | 1)

    await tb.doorbell_source.send(DoorbellTransaction(queue=0))

    for k in range(200):
        await RisingEdge(dut.clk)

    for k in range(10):
        tx_req = await tb.tx_req_sink.recv()
        tb.log.info("TX request: %s", tx_req)

        assert tx_req.queue == 0

        status = TxStatusTransaction(empty=0, error=0, len=1000, queue=tx_req.queue, tag=tx_req.tag)
        tb.log.info("TX status: %s", status)
        await tb.tx_status_dequeue_source.send(status)
        await tb.tx_status_start_source.send(status)
        await tb.tx_status_finish_source.send(status)

    tx_req = await tb.tx_req_sink.recv()
    tb.log.info("TX request: %s", tx_req)

    assert tx_req.queue == 0

    status = TxStatusTransaction(empty=1, error=0, len=0, queue=tx_req.queue, tag=tx_req.tag)
    tb.log.info("TX status: %s", status)
    await tb.tx_status_dequeue_source.send(status)

    for k in range(200):
        await RisingEdge(dut.clk)

    while not tb.tx_req_sink.empty():
        tx_req = await tb.tx_req_sink.recv()
        tb.log.info("TX request: %s", tx_req)

        assert tx_req.queue == 0

        status = TxStatusTransaction(empty=1, error=0, len=0, queue=tx_req.queue, tag=tx_req.tag)
        tb.log.info("TX status: %s", status)
        await tb.tx_status_dequeue_source.send(status)

        for k in range(10):
            await RisingEdge(dut.clk)

    await RisingEdge(dut.clk)
    await RisingEdge(dut.clk)


async def run_test_multiple(dut, idle_inserter=None, backpressure_inserter=None):

    tb = TB(dut)

    await tb.reset()

    tb.set_idle_generator(idle_inserter)
    tb.set_backpressure_generator(backpressure_inserter)

    dut.enable.value = 1
    await tb.wr_ctrl_reg(MQNIC_RB_SCHED_RR_REG_CH0_FC2, (25 << 16) | ((1536+63)//64))
    await tb.wr_ctrl_reg(MQNIC_RB_SCHED_RR_REG_CH0_FC3, ((1536+63)//64)*32)
    await tb.wr_ctrl_reg(MQNIC_RB_SCHED_RR_REG_CH0_CTRL, 1)
    await tb.wr_ctrl_reg(MQNIC_RB_SCHED_RR_REG_CTRL, 1)

    for k in range(10):
        await tb.axil_master.write_dword(k*4, MQNIC_SCHED_RR_CMD_SET_PORT_TC | (0 << 8) | 0)
        await tb.axil_master.write_dword(k*4, MQNIC_SCHED_RR_CMD_SET_PORT_ENABLE | (0 << 8) | 1)
        await tb.axil_master.write_dword(k*4, MQNIC_SCHED_RR_CMD_SET_QUEUE_ENABLE | 1)

    for k in range(10):
        await tb.doorbell_source.send(DoorbellTransaction(queue=k))

    for k in range(200):
        await RisingEdge(dut.clk)

    for k in range(100):
        tx_req = await tb.tx_req_sink.recv()
        tb.log.info("TX request: %s", tx_req)

        assert tx_req.queue == k % 10

        status = TxStatusTransaction(empty=0, error=0, len=1000, queue=tx_req.queue, tag=tx_req.tag)
        tb.log.info("TX status: %s", status)
        await tb.tx_status_dequeue_source.send(status)
        await tb.tx_status_start_source.send(status)
        await tb.tx_status_finish_source.send(status)

    for k in range(10):
        tx_req = await tb.tx_req_sink.recv()
        tb.log.info("TX request: %s", tx_req)

        status = TxStatusTransaction(empty=1, error=0, len=0, queue=tx_req.queue, tag=tx_req.tag)
        tb.log.info("TX status: %s", status)
        await tb.tx_status_dequeue_source.send(status)

    for k in range(200):
        await RisingEdge(dut.clk)

    while not tb.tx_req_sink.empty():
        tx_req = await tb.tx_req_sink.recv()
        tb.log.info("TX request: %s", tx_req)

        status = TxStatusTransaction(empty=1, error=0, len=0, queue=tx_req.queue, tag=tx_req.tag)
        tb.log.info("TX status: %s", status)
        await tb.tx_status_dequeue_source.send(status)

        for k in range(10):
            await RisingEdge(dut.clk)

    await RisingEdge(dut.clk)
    await RisingEdge(dut.clk)


async def run_test_doorbell(dut, idle_inserter=None, backpressure_inserter=None):

    tb = TB(dut)

    await tb.reset()

    tb.set_idle_generator(idle_inserter)
    tb.set_backpressure_generator(backpressure_inserter)

    dut.enable.value = 1
    await tb.wr_ctrl_reg(MQNIC_RB_SCHED_RR_REG_CH0_FC2, (25 << 16) | ((1536+63)//64))
    await tb.wr_ctrl_reg(MQNIC_RB_SCHED_RR_REG_CH0_FC3, ((1536+63)//64)*32)
    await tb.wr_ctrl_reg(MQNIC_RB_SCHED_RR_REG_CH0_CTRL, 1)
    await tb.wr_ctrl_reg(MQNIC_RB_SCHED_RR_REG_CTRL, 1)

    await tb.axil_master.write_dword(0*4, MQNIC_SCHED_RR_CMD_SET_PORT_TC | (0 << 8) | 0)
    await tb.axil_master.write_dword(0*4, MQNIC_SCHED_RR_CMD_SET_PORT_ENABLE | (0 << 8) | 1)
    await tb.axil_master.write_dword(0*4, MQNIC_SCHED_RR_CMD_SET_QUEUE_ENABLE | 1)

    await tb.doorbell_source.send(DoorbellTransaction(queue=0))

    for k in range(200):
        await RisingEdge(dut.clk)

    for k in range(10):
        tx_req = await tb.tx_req_sink.recv()
        tb.log.info("TX request: %s", tx_req)

        assert tx_req.queue == 0

        status = TxStatusTransaction(empty=0, error=0, len=1000, queue=tx_req.queue, tag=tx_req.tag)
        tb.log.info("TX status: %s", status)
        await tb.tx_status_dequeue_source.send(status)
        await tb.tx_status_start_source.send(status)
        await tb.tx_status_finish_source.send(status)

    for k in range(200):
        await RisingEdge(dut.clk)

    tx_req = await tb.tx_req_sink.recv()
    tb.log.info("TX request: %s", tx_req)

    assert tx_req.queue == 0

    status = TxStatusTransaction(empty=1, error=0, len=0, queue=tx_req.queue, tag=tx_req.tag)
    tb.log.info("TX status: %s", status)
    await tb.tx_status_dequeue_source.send(status)

    await tb.doorbell_source.send(DoorbellTransaction(queue=0))

    tx_req = await tb.tx_req_sink.recv()
    tb.log.info("TX request: %s", tx_req)

    assert tx_req.queue == 0

    status = TxStatusTransaction(empty=1, error=0, len=0, queue=tx_req.queue, tag=tx_req.tag)
    tb.log.info("TX status: %s", status)
    await tb.tx_status_dequeue_source.send(status)

    tx_req = await tb.tx_req_sink.recv()
    tb.log.info("TX request: %s", tx_req)

    assert tx_req.queue == 0

    status = TxStatusTransaction(empty=1, error=0, len=0, queue=tx_req.queue, tag=tx_req.tag)
    tb.log.info("TX status: %s", status)
    await tb.tx_status_dequeue_source.send(status)

    for k in range(10):
        tx_req = await tb.tx_req_sink.recv()
        tb.log.info("TX request: %s", tx_req)

        assert tx_req.queue == 0

        status = TxStatusTransaction(empty=0, error=0, len=1000, queue=tx_req.queue, tag=tx_req.tag)
        tb.log.info("TX status: %s", status)
        await tb.tx_status_dequeue_source.send(status)
        await tb.tx_status_start_source.send(status)
        await tb.tx_status_finish_source.send(status)

    for k in range(200):
        await RisingEdge(dut.clk)

    tx_req = await tb.tx_req_sink.recv()
    tb.log.info("TX request: %s", tx_req)

    assert tx_req.queue == 0

    status = TxStatusTransaction(empty=1, error=0, len=0, queue=tx_req.queue, tag=tx_req.tag)
    tb.log.info("TX status: %s", status)
    await tb.tx_status_dequeue_source.send(status)

    for k in range(200):
        await RisingEdge(dut.clk)

    while not tb.tx_req_sink.empty():
        tx_req = await tb.tx_req_sink.recv()
        tb.log.info("TX request: %s", tx_req)

        assert tx_req.queue == 0

        status = TxStatusTransaction(empty=1, error=0, len=0, queue=tx_req.queue, tag=tx_req.tag)
        tb.log.info("TX status: %s", status)
        await tb.tx_status_dequeue_source.send(status)

        for k in range(10):
            await RisingEdge(dut.clk)

    await RisingEdge(dut.clk)
    await RisingEdge(dut.clk)


def cycle_pause():
    return itertools.cycle([1, 1, 1, 0])


if cocotb.SIM_NAME:

    factory = TestFactory(run_test_config)
    factory.generate_tests()

    for test in [
                run_test_single,
                run_test_multiple,
                run_test_doorbell
            ]:

        factory = TestFactory(test)
        factory.add_option("idle_inserter", [None, cycle_pause])
        factory.add_option("backpressure_inserter", [None, cycle_pause])
        factory.generate_tests()


# cocotb-test

tests_dir = os.path.dirname(__file__)
rtl_dir = os.path.abspath(os.path.join(tests_dir, '..', '..', 'rtl'))
lib_dir = os.path.abspath(os.path.join(rtl_dir, '..', 'lib'))
axi_rtl_dir = os.path.abspath(os.path.join(lib_dir, 'axi', 'rtl'))
axis_rtl_dir = os.path.abspath(os.path.join(lib_dir, 'axis', 'rtl'))
eth_rtl_dir = os.path.abspath(os.path.join(lib_dir, 'eth', 'rtl'))
pcie_rtl_dir = os.path.abspath(os.path.join(lib_dir, 'pcie', 'rtl'))


def test_tx_scheduler_rr(request):
    dut = "tx_scheduler_rr"
    module = os.path.splitext(os.path.basename(__file__))[0]
    toplevel = dut

    verilog_sources = [
        os.path.join(rtl_dir, f"{dut}.v"),
        os.path.join(axis_rtl_dir, "axis_fifo.v"),
        os.path.join(axis_rtl_dir, "priority_encoder.v"),
    ]

    parameters = {}

    parameters['LEN_WIDTH'] = 16
    parameters['REQ_DEST_WIDTH'] = 8
    parameters['REQ_TAG_WIDTH'] = 8
    parameters['QUEUE_INDEX_WIDTH'] = 6
    parameters['PIPELINE'] = 2
    parameters['SCHED_CTRL_ENABLE'] = 1
    parameters['REQ_DEST_DEFAULT'] = 0
    parameters['MAX_TX_SIZE'] = 9216
    parameters['FC_SCALE'] = 64

    parameters['AXIL_BASE_ADDR'] = 0
    parameters['AXIL_DATA_WIDTH'] = 32
    parameters['AXIL_ADDR_WIDTH'] = parameters['QUEUE_INDEX_WIDTH'] + 2
    parameters['AXIL_STRB_WIDTH'] = parameters['AXIL_DATA_WIDTH'] // 8

    parameters['REG_ADDR_WIDTH'] = 12
    parameters['REG_DATA_WIDTH'] = parameters['AXIL_DATA_WIDTH']
    parameters['REG_STRB_WIDTH'] = parameters['REG_DATA_WIDTH'] // 8
    parameters['RB_BLOCK_TYPE'] = 0x0000C040
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
