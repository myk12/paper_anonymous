# SPDX-License-Identifier: BSD-2-Clause-Views
# Copyright (c) 2019-2023 The Regents of the University of California

import datetime
from collections import deque
from decimal import Decimal

import cocotb
from cocotb.log import SimLog
from cocotb.queue import Queue
from cocotb.triggers import Event, Edge, RisingEdge

from cocotbext.axi import Window

import struct

MQNIC_MAX_EQ   = 1
MQNIC_MAX_TXQ  = 32
MQNIC_MAX_RXQ  = 32
MQNIC_MAX_CQ   = MQNIC_MAX_TXQ*2

# Register blocks
MQNIC_RB_REG_TYPE      = 0x00
MQNIC_RB_REG_VER       = 0x04
MQNIC_RB_REG_NEXT_PTR  = 0x08

MQNIC_RB_FW_ID_TYPE            = 0xFFFFFFFF
MQNIC_RB_FW_ID_VER             = 0x00000100
MQNIC_RB_FW_ID_REG_FPGA_ID     = 0x0C
MQNIC_RB_FW_ID_REG_FW_ID       = 0x10
MQNIC_RB_FW_ID_REG_FW_VER      = 0x14
MQNIC_RB_FW_ID_REG_BOARD_ID    = 0x18
MQNIC_RB_FW_ID_REG_BOARD_VER   = 0x1C
MQNIC_RB_FW_ID_REG_BUILD_DATE  = 0x20
MQNIC_RB_FW_ID_REG_GIT_HASH    = 0x24
MQNIC_RB_FW_ID_REG_REL_INFO    = 0x28

MQNIC_RB_GPIO_TYPE          = 0x0000C100
MQNIC_RB_GPIO_VER           = 0x00000100
MQNIC_RB_GPIO_REG_GPIO_IN   = 0x0C
MQNIC_RB_GPIO_REG_GPIO_OUT  = 0x10

MQNIC_RB_I2C_TYPE      = 0x0000C110
MQNIC_RB_I2C_VER       = 0x00000100
MQNIC_RB_I2C_REG_CTRL  = 0x0C

MQNIC_RB_SPI_FLASH_TYPE        = 0x0000C120
MQNIC_RB_SPI_FLASH_VER         = 0x00000100
MQNIC_RB_SPI_FLASH_REG_FORMAT  = 0x0C
MQNIC_RB_SPI_FLASH_REG_CTRL_0  = 0x10
MQNIC_RB_SPI_FLASH_REG_CTRL_1  = 0x14

MQNIC_RB_BPI_FLASH_TYPE        = 0x0000C121
MQNIC_RB_BPI_FLASH_VER         = 0x00000100
MQNIC_RB_BPI_FLASH_REG_FORMAT  = 0x0C
MQNIC_RB_BPI_FLASH_REG_ADDR    = 0x10
MQNIC_RB_BPI_FLASH_REG_DATA    = 0x14
MQNIC_RB_BPI_FLASH_REG_CTRL    = 0x18

MQNIC_RB_ALVEO_BMC_TYPE      = 0x0000C140
MQNIC_RB_ALVEO_BMC_VER       = 0x00000100
MQNIC_RB_ALVEO_BMC_REG_ADDR  = 0x0C
MQNIC_RB_ALVEO_BMC_REG_DATA  = 0x10

MQNIC_RB_GECKO_BMC_TYPE        = 0x0000C141
MQNIC_RB_GECKO_BMC_VER         = 0x00000100
MQNIC_RB_GECKO_BMC_REG_STATUS  = 0x0C
MQNIC_RB_GECKO_BMC_REG_DATA    = 0x10
MQNIC_RB_GECKO_BMC_REG_CMD     = 0x14

MQNIC_RB_STATS_TYPE        = 0x0000C006
MQNIC_RB_STATS_VER         = 0x00000100
MQNIC_RB_STATS_REG_OFFSET  = 0x0C
MQNIC_RB_STATS_REG_COUNT   = 0x10
MQNIC_RB_STATS_REG_STRIDE  = 0x14
MQNIC_RB_STATS_REG_FLAGS   = 0x18

MQNIC_RB_IRQ_TYPE        = 0x0000C007
MQNIC_RB_IRQ_VER         = 0x00000100

MQNIC_RB_CLK_INFO_TYPE         = 0x0000C008
MQNIC_RB_CLK_INFO_VER          = 0x00000100
MQNIC_RB_CLK_INFO_COUNT        = 0x0C
MQNIC_RB_CLK_INFO_REF_NOM_PER  = 0x10
MQNIC_RB_CLK_INFO_CLK_NOM_PER  = 0x18
MQNIC_RB_CLK_INFO_CLK_FREQ     = 0x1C
MQNIC_RB_CLK_INFO_FREQ_BASE    = 0x20

MQNIC_RB_PHC_TYPE                = 0x0000C080
MQNIC_RB_PHC_VER                 = 0x00000200
MQNIC_RB_PHC_REG_CTRL            = 0x0C
MQNIC_RB_PHC_REG_CUR_FNS         = 0x10
MQNIC_RB_PHC_REG_CUR_TOD_NS      = 0x14
MQNIC_RB_PHC_REG_CUR_TOD_SEC_L   = 0x18
MQNIC_RB_PHC_REG_CUR_TOD_SEC_H   = 0x1C
MQNIC_RB_PHC_REG_CUR_REL_NS_L    = 0x20
MQNIC_RB_PHC_REG_CUR_REL_NS_H    = 0x24
MQNIC_RB_PHC_REG_CUR_PTM_NS_L    = 0x28
MQNIC_RB_PHC_REG_CUR_PTM_NS_H    = 0x2C
MQNIC_RB_PHC_REG_SNAP_FNS        = 0x30
MQNIC_RB_PHC_REG_SNAP_TOD_NS     = 0x34
MQNIC_RB_PHC_REG_SNAP_TOD_SEC_L  = 0x38
MQNIC_RB_PHC_REG_SNAP_TOD_SEC_H  = 0x3C
MQNIC_RB_PHC_REG_SNAP_REL_NS_L   = 0x40
MQNIC_RB_PHC_REG_SNAP_REL_NS_H   = 0x44
MQNIC_RB_PHC_REG_SNAP_PTM_NS_L   = 0x48
MQNIC_RB_PHC_REG_SNAP_PTM_NS_H   = 0x4C
MQNIC_RB_PHC_REG_OFFSET_TOD_NS   = 0x50
MQNIC_RB_PHC_REG_SET_TOD_NS      = 0x54
MQNIC_RB_PHC_REG_SET_TOD_SEC_L   = 0x58
MQNIC_RB_PHC_REG_SET_TOD_SEC_H   = 0x5C
MQNIC_RB_PHC_REG_SET_REL_NS_L    = 0x60
MQNIC_RB_PHC_REG_SET_REL_NS_H    = 0x64
MQNIC_RB_PHC_REG_OFFSET_REL_NS   = 0x68
MQNIC_RB_PHC_REG_OFFSET_FNS      = 0x6C
MQNIC_RB_PHC_REG_NOM_PERIOD_FNS  = 0x70
MQNIC_RB_PHC_REG_NOM_PERIOD_NS   = 0x74
MQNIC_RB_PHC_REG_PERIOD_FNS      = 0x78
MQNIC_RB_PHC_REG_PERIOD_NS       = 0x7C

MQNIC_RB_PHC_PEROUT_TYPE              = 0x0000C081
MQNIC_RB_PHC_PEROUT_VER               = 0x00000100
MQNIC_RB_PHC_PEROUT_REG_CTRL          = 0x0C
MQNIC_RB_PHC_PEROUT_REG_START_FNS     = 0x10
MQNIC_RB_PHC_PEROUT_REG_START_NS      = 0x14
MQNIC_RB_PHC_PEROUT_REG_START_SEC_L   = 0x18
MQNIC_RB_PHC_PEROUT_REG_START_SEC_H   = 0x1C
MQNIC_RB_PHC_PEROUT_REG_PERIOD_FNS    = 0x20
MQNIC_RB_PHC_PEROUT_REG_PERIOD_NS     = 0x24
MQNIC_RB_PHC_PEROUT_REG_PERIOD_SEC_L  = 0x28
MQNIC_RB_PHC_PEROUT_REG_PERIOD_SEC_H  = 0x2C
MQNIC_RB_PHC_PEROUT_REG_WIDTH_FNS     = 0x30
MQNIC_RB_PHC_PEROUT_REG_WIDTH_NS      = 0x34
MQNIC_RB_PHC_PEROUT_REG_WIDTH_SEC_L   = 0x38
MQNIC_RB_PHC_PEROUT_REG_WIDTH_SEC_H   = 0x3C

MQNIC_RB_IF_TYPE            = 0x0000C000
MQNIC_RB_IF_VER             = 0x00000100
MQNIC_RB_IF_REG_OFFSET      = 0x0C
MQNIC_RB_IF_REG_COUNT       = 0x10
MQNIC_RB_IF_REG_STRIDE      = 0x14
MQNIC_RB_IF_REG_CSR_OFFSET  = 0x18

MQNIC_RB_IF_CTRL_TYPE               = 0x0000C001
MQNIC_RB_IF_CTRL_VER                = 0x00000400
MQNIC_RB_IF_CTRL_REG_FEATURES       = 0x0C
MQNIC_RB_IF_CTRL_REG_PORT_COUNT     = 0x10
MQNIC_RB_IF_CTRL_REG_SCHED_COUNT    = 0x14
MQNIC_RB_IF_CTRL_REG_MAX_TX_MTU     = 0x20
MQNIC_RB_IF_CTRL_REG_MAX_RX_MTU     = 0x24
MQNIC_RB_IF_CTRL_REG_TX_MTU         = 0x28
MQNIC_RB_IF_CTRL_REG_RX_MTU         = 0x2C
MQNIC_RB_IF_CTRL_REG_TX_FIFO_DEPTH  = 0x30
MQNIC_RB_IF_CTRL_REG_RX_FIFO_DEPTH  = 0x34

MQNIC_IF_FEATURE_RSS      = (1 << 0)
MQNIC_IF_FEATURE_PTP_TS   = (1 << 4)
MQNIC_IF_FEATURE_TX_CSUM  = (1 << 8)
MQNIC_IF_FEATURE_RX_CSUM  = (1 << 9)
MQNIC_IF_FEATURE_RX_HASH  = (1 << 10)
MQNIC_IF_FEATURE_LFC      = (1 << 11)
MQNIC_IF_FEATURE_PFC      = (1 << 12)

MQNIC_RB_RX_QUEUE_MAP_TYPE             = 0x0000C090
MQNIC_RB_RX_QUEUE_MAP_VER              = 0x00000200
MQNIC_RB_RX_QUEUE_MAP_REG_CFG          = 0x0C
MQNIC_RB_RX_QUEUE_MAP_CH_OFFSET        = 0x10
MQNIC_RB_RX_QUEUE_MAP_CH_STRIDE        = 0x10
MQNIC_RB_RX_QUEUE_MAP_CH_REG_OFFSET    = 0x00
MQNIC_RB_RX_QUEUE_MAP_CH_REG_RSS_MASK  = 0x04
MQNIC_RB_RX_QUEUE_MAP_CH_REG_APP_MASK  = 0x08

MQNIC_RB_EQM_TYPE        = 0x0000C010
MQNIC_RB_EQM_VER         = 0x00000400
MQNIC_RB_EQM_REG_OFFSET  = 0x0C
MQNIC_RB_EQM_REG_COUNT   = 0x10
MQNIC_RB_EQM_REG_STRIDE  = 0x14

MQNIC_RB_CQM_TYPE        = 0x0000C020
MQNIC_RB_CQM_VER         = 0x00000400
MQNIC_RB_CQM_REG_OFFSET  = 0x0C
MQNIC_RB_CQM_REG_COUNT   = 0x10
MQNIC_RB_CQM_REG_STRIDE  = 0x14

MQNIC_RB_TX_QM_TYPE        = 0x0000C030
MQNIC_RB_TX_QM_VER         = 0x00000400
MQNIC_RB_TX_QM_REG_OFFSET  = 0x0C
MQNIC_RB_TX_QM_REG_COUNT   = 0x10
MQNIC_RB_TX_QM_REG_STRIDE  = 0x14

MQNIC_RB_RX_QM_TYPE        = 0x0000C031
MQNIC_RB_RX_QM_VER         = 0x00000400
MQNIC_RB_RX_QM_REG_OFFSET  = 0x0C
MQNIC_RB_RX_QM_REG_COUNT   = 0x10
MQNIC_RB_RX_QM_REG_STRIDE  = 0x14

MQNIC_RB_PORT_TYPE        = 0x0000C002
MQNIC_RB_PORT_VER         = 0x00000200
MQNIC_RB_PORT_REG_OFFSET  = 0x0C

MQNIC_RB_PORT_CTRL_TYPE           = 0x0000C003
MQNIC_RB_PORT_CTRL_VER            = 0x00000300
MQNIC_RB_PORT_CTRL_REG_FEATURES   = 0x0C
MQNIC_RB_PORT_CTRL_REG_TX_CTRL    = 0x10
MQNIC_RB_PORT_CTRL_REG_RX_CTRL    = 0x14
MQNIC_RB_PORT_CTRL_REG_LFC_CTRL   = 0x1C
MQNIC_RB_PORT_CTRL_REG_PFC_CTRL0  = 0x20
MQNIC_RB_PORT_CTRL_REG_PFC_CTRL1  = 0x24
MQNIC_RB_PORT_CTRL_REG_PFC_CTRL2  = 0x28
MQNIC_RB_PORT_CTRL_REG_PFC_CTRL3  = 0x2C
MQNIC_RB_PORT_CTRL_REG_PFC_CTRL4  = 0x30
MQNIC_RB_PORT_CTRL_REG_PFC_CTRL5  = 0x34
MQNIC_RB_PORT_CTRL_REG_PFC_CTRL6  = 0x38
MQNIC_RB_PORT_CTRL_REG_PFC_CTRL7  = 0x3C

MQNIC_PORT_FEATURE_LFC           = (1 << 0)
MQNIC_PORT_FEATURE_PFC           = (1 << 1)
MQNIC_PORT_FEATURE_INT_MAC_CTRL  = (1 << 2)

MQNIC_PORT_TX_CTRL_EN            = (1 << 0)
MQNIC_PORT_TX_CTRL_PAUSE         = (1 << 8)
MQNIC_PORT_TX_CTRL_STATUS        = (1 << 16)
MQNIC_PORT_TX_CTRL_RESET         = (1 << 17)
MQNIC_PORT_TX_CTRL_PAUSE_REQ     = (1 << 24)
MQNIC_PORT_TX_CTRL_PAUSE_ACK     = (1 << 25)

MQNIC_PORT_RX_CTRL_EN            = (1 << 0)
MQNIC_PORT_RX_CTRL_PAUSE         = (1 << 8)
MQNIC_PORT_RX_CTRL_STATUS        = (1 << 16)
MQNIC_PORT_RX_CTRL_RESET         = (1 << 17)
MQNIC_PORT_RX_CTRL_PAUSE_REQ     = (1 << 24)
MQNIC_PORT_RX_CTRL_PAUSE_ACK     = (1 << 25)

MQNIC_PORT_LFC_CTRL_TX_LFC_EN    = (1 << 24)
MQNIC_PORT_LFC_CTRL_RX_LFC_EN    = (1 << 25)
MQNIC_PORT_LFC_CTRL_TX_LFC_REQ   = (1 << 28)
MQNIC_PORT_LFC_CTRL_RX_LFC_REQ   = (1 << 29)

MQNIC_PORT_PFC_CTRL_TX_PFC_EN    = (1 << 24)
MQNIC_PORT_PFC_CTRL_RX_PFC_EN    = (1 << 25)
MQNIC_PORT_PFC_CTRL_TX_PFC_REQ   = (1 << 28)
MQNIC_PORT_PFC_CTRL_RX_PFC_REQ   = (1 << 29)

MQNIC_RB_SCHED_BLOCK_TYPE        = 0x0000C004
MQNIC_RB_SCHED_BLOCK_VER         = 0x00000300
MQNIC_RB_SCHED_BLOCK_REG_OFFSET  = 0x0C

MQNIC_RB_SCHED_RR_TYPE              = 0x0000C040
MQNIC_RB_SCHED_RR_VER               = 0x00000200
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

MQNIC_RB_SCHED_CTRL_TDMA_TYPE           = 0x0000C050
MQNIC_RB_SCHED_CTRL_TDMA_VER            = 0x00000100
MQNIC_RB_SCHED_CTRL_TDMA_REG_OFFSET     = 0x0C
MQNIC_RB_SCHED_CTRL_TDMA_REG_CH_COUNT   = 0x10
MQNIC_RB_SCHED_CTRL_TDMA_REG_CH_STRIDE  = 0x14
MQNIC_RB_SCHED_CTRL_TDMA_REG_CTRL       = 0x18
MQNIC_RB_SCHED_CTRL_TDMA_REG_TS_COUNT   = 0x1C

MQNIC_RB_TDMA_SCH_TYPE                     = 0x0000C060
MQNIC_RB_TDMA_SCH_VER                      = 0x00000200
MQNIC_RB_TDMA_SCH_REG_CTRL                 = 0x0C
MQNIC_RB_TDMA_SCH_REG_SCH_START_FNS        = 0x10
MQNIC_RB_TDMA_SCH_REG_SCH_START_NS         = 0x14
MQNIC_RB_TDMA_SCH_REG_SCH_START_SEC_L      = 0x18
MQNIC_RB_TDMA_SCH_REG_SCH_START_SEC_H      = 0x1C
MQNIC_RB_TDMA_SCH_REG_SCH_PERIOD_FNS       = 0x20
MQNIC_RB_TDMA_SCH_REG_SCH_PERIOD_NS        = 0x24
MQNIC_RB_TDMA_SCH_REG_TS_PERIOD_NS         = 0x28
MQNIC_RB_TDMA_SCH_REG_ACTIVE_PERIOD_NS     = 0x2C

MQNIC_RB_APP_INFO_TYPE    = 0x0000C005
MQNIC_RB_APP_INFO_VER     = 0x00000200
MQNIC_RB_APP_INFO_REG_ID  = 0x0C

MQNIC_QUEUE_BASE_ADDR_VF_REG  = 0x00
MQNIC_QUEUE_CTRL_STATUS_REG   = 0x08
MQNIC_QUEUE_SIZE_CQN_REG      = 0x0C
MQNIC_QUEUE_PTR_REG           = 0x10
MQNIC_QUEUE_PROD_PTR_REG      = 0x10
MQNIC_QUEUE_CONS_PTR_REG      = 0x12

MQNIC_QUEUE_ENABLE_MASK  = 0x00000001
MQNIC_QUEUE_ACTIVE_MASK  = 0x00000008
MQNIC_QUEUE_PTR_MASK     = 0xFFFF

MQNIC_QUEUE_CMD_SET_VF_ID     = 0x80010000
MQNIC_QUEUE_CMD_SET_SIZE      = 0x80020000
MQNIC_QUEUE_CMD_SET_CQN       = 0xC0000000
MQNIC_QUEUE_CMD_SET_PROD_PTR  = 0x80800000
MQNIC_QUEUE_CMD_SET_CONS_PTR  = 0x80900000
MQNIC_QUEUE_CMD_SET_ENABLE    = 0x40000100

MQNIC_CQ_BASE_ADDR_VF_REG  = 0x00
MQNIC_CQ_CTRL_STATUS_REG   = 0x08
MQNIC_CQ_PTR_REG           = 0x0C
MQNIC_CQ_PROD_PTR_REG      = 0x0C
MQNIC_CQ_CONS_PTR_REG      = 0x0E

MQNIC_CQ_ENABLE_MASK  = 0x00010000
MQNIC_CQ_ARM_MASK     = 0x00020000
MQNIC_CQ_ACTIVE_MASK  = 0x00080000
MQNIC_CQ_PTR_MASK     = 0xFFFF

MQNIC_CQ_CMD_SET_VF_ID         = 0x80010000
MQNIC_CQ_CMD_SET_SIZE          = 0x80020000
MQNIC_CQ_CMD_SET_EQN           = 0xC0000000
MQNIC_CQ_CMD_SET_PROD_PTR      = 0x80800000
MQNIC_CQ_CMD_SET_CONS_PTR      = 0x80900000
MQNIC_CQ_CMD_SET_CONS_PTR_ARM  = 0x80910000
MQNIC_CQ_CMD_SET_ENABLE        = 0x40000100
MQNIC_CQ_CMD_SET_ARM           = 0x40000200

MQNIC_EQ_BASE_ADDR_VF_REG  = 0x00
MQNIC_EQ_CTRL_STATUS_REG   = 0x08
MQNIC_EQ_PTR_REG           = 0x0C
MQNIC_EQ_PROD_PTR_REG      = 0x0C
MQNIC_EQ_CONS_PTR_REG      = 0x0E

MQNIC_EQ_ENABLE_MASK  = 0x00010000
MQNIC_EQ_ARM_MASK     = 0x00020000
MQNIC_EQ_ACTIVE_MASK  = 0x00080000
MQNIC_EQ_PTR_MASK     = 0xFFFF

MQNIC_EQ_CMD_SET_VF_ID         = 0x80010000
MQNIC_EQ_CMD_SET_SIZE          = 0x80020000
MQNIC_EQ_CMD_SET_IRQN          = 0xC0000000
MQNIC_EQ_CMD_SET_PROD_PTR      = 0x80800000
MQNIC_EQ_CMD_SET_CONS_PTR      = 0x80900000
MQNIC_EQ_CMD_SET_CONS_PTR_ARM  = 0x80910000
MQNIC_EQ_CMD_SET_ENABLE        = 0x40000100
MQNIC_EQ_CMD_SET_ARM           = 0x40000200

MQNIC_EVENT_TYPE_CPL = 0x0000

MQNIC_DESC_SIZE = 16
MQNIC_CPL_SIZE = 32
MQNIC_EVENT_SIZE = 32


class Resource:
    def __init__(self, count, parent, stride):
        self.count = count
        self.parent = parent
        self.stride = stride

        self.windows = {}
        self.free_list = list(range(count))

    def alloc(self):
        return self.free_list.pop(0)

    def free(self, index):
        self.free_list.append(index)
        self.free_list.sort()

    def get_count(self):
        return self.count

    def get_window(self, index):
        if index not in self.windows:
            self.windows[index] = self.parent.create_window(index*self.stride, self.stride)
        return self.windows[index]


class RegBlock(Window):
    def __init__(self, parent, offset, size, base=0, **kwargs):
        super().__init__(parent, offset, size, base, **kwargs)
        self._offset = offset
        self.type = 0
        self.version = 0


class RegBlockList:
    def __init__(self):
        self.blocks = []

    async def enumerate_reg_blocks(self, window, offset=0):
        while True:
            rb_type = await window.read_dword(offset+MQNIC_RB_REG_TYPE)
            rb_version = await window.read_dword(offset+MQNIC_RB_REG_VER)
            rb = window.create_window(offset, window_type=RegBlock)
            rb.type = rb_type
            rb.version = rb_version
            print(f"Block ID {rb_type:#010x} version {rb_version:#010x} at offset {offset:#010x}")
            self.blocks.append(rb)
            offset = await window.read_dword(offset+MQNIC_RB_REG_NEXT_PTR)
            if offset == 0:
                return
            assert offset & 0x3 == 0, "Register block not aligned"
            for block in self.blocks:
                assert block.offset != offset, "Register blocks form a loop"

    def find(self, rb_type, version=None, index=0):
        for block in self.blocks:
            if block.type == rb_type and (not version or block.version == version):
                if index <= 0:
                    return block
                else:
                    index -= 1
        return None

    def __getitem__(self, key):
        return self.blocks[key]

    def __len__(self):
        return len(self.blocks)


class Packet:
    def __init__(self, data=b''):
        self.data = data
        self.queue = None
        self.timestamp_ns = None
        self.rx_checksum = None

    def __repr__(self):
        return (
            f'{type(self).__name__}(data={self.data}, '
            f'queue={self.queue}, '
            f'timestamp_ns={self.timestamp_ns}, '
            f'rx_checksum={self.rx_checksum:#06x})'
        )

    def __iter__(self):
        return self.data.__iter__()

    def __len__(self):
        return len(self.data)

    def __bytes__(self):
        return bytes(self.data)


class Eq:
    def __init__(self, interface):
        self.interface = interface
        self.log = interface.log
        self.driver = interface.driver
        self.log_size = 0
        self.size = 0
        self.size_mask = 0
        self.stride = 0
        self.eqn = None
        self.enabled = False

        self.buf_size = 0
        self.buf_region = None
        self.buf_dma = 0
        self.buf = None

        self.irq = None

        self.cq_table = {}

        self.cons_ptr = 0

        self.hw_regs = None

    async def open(self, irq, size):
        if self.hw_regs:
            raise Exception("Already open")

        self.eqn = self.interface.eq_res.alloc()

        self.log.info("Open EQ %d (interface %d)", self.eqn, self.interface.index)

        self.log_size = size.bit_length() - 1
        self.size = 2**self.log_size
        self.size_mask = self.size-1
        self.stride = MQNIC_EVENT_SIZE

        self.buf_size = self.size*self.stride
        self.buf_region = self.driver.pool.alloc_region(self.buf_size)
        self.buf_dma = self.buf_region.get_absolute_address(0)
        self.buf = self.buf_region.mem

        self.buf[0:self.buf_size] = b'\x00'*self.buf_size

        self.cons_ptr = 0

        self.irq = irq

        self.cq_table = {}

        self.hw_regs = self.interface.eq_res.get_window(self.eqn)

        await self.hw_regs.write_dword(MQNIC_EQ_CTRL_STATUS_REG, MQNIC_EQ_CMD_SET_ENABLE | 0)
        await self.hw_regs.write_dword(MQNIC_EQ_BASE_ADDR_VF_REG, self.buf_dma & 0xfffff000)
        await self.hw_regs.write_dword(MQNIC_EQ_BASE_ADDR_VF_REG+4, self.buf_dma >> 32)
        await self.hw_regs.write_dword(MQNIC_EQ_CTRL_STATUS_REG, MQNIC_EQ_CMD_SET_SIZE | self.log_size)
        await self.hw_regs.write_dword(MQNIC_EQ_CTRL_STATUS_REG, MQNIC_EQ_CMD_SET_IRQN | self.irq)
        await self.hw_regs.write_dword(MQNIC_EQ_CTRL_STATUS_REG, MQNIC_EQ_CMD_SET_PROD_PTR | 0)
        await self.hw_regs.write_dword(MQNIC_EQ_CTRL_STATUS_REG, MQNIC_EQ_CMD_SET_CONS_PTR | (self.cons_ptr & MQNIC_EQ_PTR_MASK))
        await self.hw_regs.write_dword(MQNIC_EQ_CTRL_STATUS_REG, MQNIC_EQ_CMD_SET_ENABLE | 1)

        self.enabled = True

    async def close(self):
        if not self.hw_regs:
            return

        await self.hw_regs.write_dword(MQNIC_EQ_CTRL_STATUS_REG, MQNIC_EQ_CMD_SET_ENABLE | 0)

        # TODO free buffer

        self.irq = None

        self.enabled = False

        self.hw_regs = None

        self.interface.eq_res.free(self.eqn)
        self.eqn = None

    def attach_cq(self, cq):
        self.cq_table[cq.cqn] = cq

    def detach_cq(self, cq):
        del self.cq_table[cq.cqn]

    async def write_cons_ptr(self):
        await self.hw_regs.write_dword(MQNIC_EQ_CTRL_STATUS_REG, MQNIC_EQ_CMD_SET_CONS_PTR | (self.cons_ptr & MQNIC_EQ_PTR_MASK))

    async def arm(self):
        if not self.hw_regs:
            return

        await self.hw_regs.write_dword(MQNIC_EQ_CTRL_STATUS_REG, MQNIC_EQ_CMD_SET_ARM | 1)

    async def process_eq(self):
        self.log.info("Process EQ")

        eq_cons_ptr = self.cons_ptr
        eq_index = eq_cons_ptr & self.size_mask

        while True:
            event_data = struct.unpack_from("<HHLLLLLLL", self.buf, eq_index*self.stride)

            self.log.info("EQ %d index %d data: %s", self.eqn, eq_index, repr(event_data))

            if bool(event_data[-1] & 0x80000000) == bool(eq_cons_ptr & self.size):
                self.log.info("EQ %d empty", self.eqn)
                break

            if event_data[0] == MQNIC_EVENT_TYPE_CPL:
                # completion
                cq = self.cq_table[event_data[1]]
                await cq.handler(cq)
                await cq.arm()

            eq_cons_ptr += 1
            eq_index = eq_cons_ptr & self.size_mask

        self.cons_ptr = eq_cons_ptr
        await self.write_cons_ptr()


class Cq:
    def __init__(self, interface):
        self.interface = interface
        self.log = interface.log
        self.driver = interface.driver
        self.log_size = 0
        self.size = 0
        self.size_mask = 0
        self.stride = 0
        self.cqn = None
        self.enabled = False

        self.buf_size = 0
        self.buf_region = None
        self.buf_dma = 0
        self.buf = None

        self.eq = None

        self.src_ring = None
        self.handler = None

        self.cons_ptr = 0

        self.hw_regs = None

    async def open(self, eq, size):
        if self.hw_regs:
            raise Exception("Already open")

        self.cqn = self.interface.cq_res.alloc()

        self.log.info("Open CQ %d (interface %d)", self.cqn, self.interface.index)

        self.log_size = size.bit_length() - 1
        self.size = 2**self.log_size
        self.size_mask = self.size-1
        self.stride = MQNIC_EVENT_SIZE

        self.buf_size = self.size*self.stride
        self.buf_region = self.driver.pool.alloc_region(self.buf_size)
        self.buf_dma = self.buf_region.get_absolute_address(0)
        self.buf = self.buf_region.mem

        self.buf[0:self.buf_size] = b'\x00'*self.buf_size

        self.cons_ptr = 0

        eq.attach_cq(self)
        self.eq = eq

        self.hw_regs = self.interface.cq_res.get_window(self.cqn)

        await self.hw_regs.write_dword(MQNIC_CQ_CTRL_STATUS_REG, MQNIC_CQ_CMD_SET_ENABLE | 0)
        await self.hw_regs.write_dword(MQNIC_CQ_BASE_ADDR_VF_REG, self.buf_dma & 0xfffff000)
        await self.hw_regs.write_dword(MQNIC_CQ_BASE_ADDR_VF_REG+4, self.buf_dma >> 32)
        await self.hw_regs.write_dword(MQNIC_CQ_CTRL_STATUS_REG, MQNIC_CQ_CMD_SET_SIZE | self.log_size)
        await self.hw_regs.write_dword(MQNIC_CQ_CTRL_STATUS_REG, MQNIC_CQ_CMD_SET_EQN | self.eq.eqn)
        await self.hw_regs.write_dword(MQNIC_CQ_CTRL_STATUS_REG, MQNIC_CQ_CMD_SET_PROD_PTR | 0)
        await self.hw_regs.write_dword(MQNIC_CQ_CTRL_STATUS_REG, MQNIC_CQ_CMD_SET_CONS_PTR | (self.cons_ptr & MQNIC_CQ_PTR_MASK))
        await self.hw_regs.write_dword(MQNIC_CQ_CTRL_STATUS_REG, MQNIC_CQ_CMD_SET_ENABLE | 1)

        self.enabled = True

    async def close(self):
        if not self.hw_regs:
            return

        await self.hw_regs.write_dword(MQNIC_CQ_CTRL_STATUS_REG, MQNIC_CQ_CMD_SET_ENABLE | 0)

        # TODO free buffer

        self.eq.detach_cq(self)
        self.eq = None

        self.enabled = False

        self.hw_regs = None

        self.interface.cq_res.free(self.cqn)
        self.cqn = None

    async def write_cons_ptr(self):
        await self.hw_regs.write_dword(MQNIC_CQ_CTRL_STATUS_REG, MQNIC_CQ_CMD_SET_CONS_PTR | (self.cons_ptr & MQNIC_CQ_PTR_MASK))

    async def arm(self):
        if not self.hw_regs:
            return

        await self.hw_regs.write_dword(MQNIC_CQ_CTRL_STATUS_REG, MQNIC_CQ_CMD_SET_ARM | 1)


class Txq:
    def __init__(self, interface):
        self.interface = interface
        self.log = interface.log
        self.driver = interface.driver
        self.log_queue_size = 0
        self.log_desc_block_size = 0
        self.desc_block_size = 0
        self.size = 0
        self.size_mask = 0
        self.full_size = 0
        self.stride = 0
        self.index = None
        self.enabled = False

        self.buf_size = 0
        self.buf_region = None
        self.buf_dma = 0
        self.buf = None

        self.ndev = None
        self.cq = None

        self.prod_ptr = 0
        self.cons_ptr = 0

        self.clean_event = Event()

        self.packets = 0
        self.bytes = 0

        self.hw_regs = None

    async def open(self, ndev, cq, size, desc_block_size):
        if self.hw_regs:
            raise Exception("Already open")

        self.index = self.interface.txq_res.alloc()

        self.log.info("Open TXQ %d (interface %d)", self.index, self.interface.index)

        self.log_queue_size = size.bit_length() - 1
        self.log_desc_block_size = desc_block_size.bit_length() - 1
        self.desc_block_size = 2**self.log_desc_block_size
        self.size = 2**self.log_queue_size
        self.size_mask = self.size-1
        self.full_size = self.size >> 1
        self.stride = MQNIC_DESC_SIZE*self.desc_block_size

        self.tx_info = [None]*self.size

        self.buf_size = self.size*self.stride
        self.buf_region = self.driver.pool.alloc_region(self.buf_size)
        self.buf_dma = self.buf_region.get_absolute_address(0)
        self.buf = self.buf_region.mem

        self.prod_ptr = 0
        self.cons_ptr = 0

        self.ndev = ndev
        self.cq = cq
        self.cq.src_ring = self
        self.cq.handler = Txq.process_tx_cq

        self.hw_regs = self.interface.txq_res.get_window(self.index)

        await self.hw_regs.write_dword(MQNIC_QUEUE_CTRL_STATUS_REG, MQNIC_QUEUE_CMD_SET_ENABLE | 0)
        await self.hw_regs.write_dword(MQNIC_QUEUE_BASE_ADDR_VF_REG, self.buf_dma & 0xfffff000)
        await self.hw_regs.write_dword(MQNIC_QUEUE_BASE_ADDR_VF_REG+4, self.buf_dma >> 32)
        await self.hw_regs.write_dword(MQNIC_QUEUE_CTRL_STATUS_REG, MQNIC_QUEUE_CMD_SET_SIZE | (self.log_desc_block_size << 8) | self.log_queue_size)
        await self.hw_regs.write_dword(MQNIC_QUEUE_CTRL_STATUS_REG, MQNIC_QUEUE_CMD_SET_CQN | self.cq.cqn)
        await self.hw_regs.write_dword(MQNIC_QUEUE_CTRL_STATUS_REG, MQNIC_QUEUE_CMD_SET_PROD_PTR | (self.prod_ptr & MQNIC_QUEUE_PTR_MASK))
        await self.hw_regs.write_dword(MQNIC_QUEUE_CTRL_STATUS_REG, MQNIC_QUEUE_CMD_SET_CONS_PTR | (self.cons_ptr & MQNIC_QUEUE_PTR_MASK))

    async def close(self):
        if not self.hw_regs:
            return

        await self.disable()

        # TODO free buffer

        if self.cq:
            self.cq.src_ring = None
            self.cq.handler = None

        self.ndev = None
        self.cq = None

        self.hw_regs = None

        self.interface.txq_res.free(self.index)
        self.index = None

    async def enable(self):
        if not self.hw_regs:
            raise Exception("Not open")

        await self.hw_regs.write_dword(MQNIC_QUEUE_CTRL_STATUS_REG, MQNIC_QUEUE_CMD_SET_ENABLE | 1)

        self.enabled = True

    async def disable(self):
        if not self.hw_regs:
            return

        await self.hw_regs.write_dword(MQNIC_QUEUE_CTRL_STATUS_REG, MQNIC_QUEUE_CMD_SET_ENABLE | 0)

        self.enabled = False

    def empty(self):
        return self.prod_ptr == self.cons_ptr

    def full(self):
        return self.prod_ptr - self.cons_ptr >= self.full_size

    async def read_cons_ptr(self):
        val = await self.hw_regs.read_dword(MQNIC_QUEUE_PTR_REG)
        self.cons_ptr += ((val >> 16) - self.cons_ptr) & MQNIC_QUEUE_PTR_MASK

    async def write_prod_ptr(self):
        await self.hw_regs.write_dword(MQNIC_QUEUE_CTRL_STATUS_REG, MQNIC_QUEUE_CMD_SET_PROD_PTR | (self.prod_ptr & MQNIC_QUEUE_PTR_MASK))

    def free_desc(self, index):
        pkt = self.tx_info[index]
        self.driver.free_pkt(pkt)
        self.tx_info[index] = None

    def free_buf(self):
        while not self.empty():
            index = self.cons_ptr & self.size_mask
            self.free_desc(index)
            self.cons_ptr += 1

    @staticmethod
    async def process_tx_cq(cq):
        interface = cq.interface
        ndev = cq.src_ring.ndev

        interface.log.info("Process CQ %d for TXQ %d (interface %d)", cq.cqn, cq.src_ring.index, interface.index)

        ring = cq.src_ring

        if not ndev.port_up:
            interface.log.info("Port not up, aborting")
            return

        # process completion queue
        cq_cons_ptr = cq.cons_ptr
        cq_index = cq_cons_ptr & cq.size_mask

        while True:
            cpl_data = struct.unpack_from("<HHHxxLHHLBBHLL", cq.buf, cq_index*cq.stride)
            ring_index = cpl_data[1] & ring.size_mask

            interface.log.info("CQ %d index %d data: %s", cq.cqn, cq_index, repr(cpl_data))

            if bool(cpl_data[-1] & 0x80000000) == bool(cq_cons_ptr & cq.size):
                interface.log.info("CQ %d empty", cq.cqn)
                break

            interface.log.info("Ring index: %d", ring_index)

            ring.free_desc(ring_index)

            cq_cons_ptr += 1
            cq_index = cq_cons_ptr & cq.size_mask

        cq.cons_ptr = cq_cons_ptr
        await cq.write_cons_ptr()

        # process ring
        ring_cons_ptr = ring.cons_ptr
        ring_index = ring_cons_ptr & ring.size_mask

        while (ring_cons_ptr != ring.prod_ptr):
            if ring.tx_info[ring_index]:
                break

            ring_cons_ptr += 1
            ring_index = ring_cons_ptr & ring.size_mask

        ring.cons_ptr = ring_cons_ptr

        ring.clean_event.set()


class Rxq:
    def __init__(self, interface):
        self.interface = interface
        self.log = interface.log
        self.driver = interface.driver
        self.log_queue_size = 0
        self.log_desc_block_size = 0
        self.desc_block_size = 0
        self.size = 0
        self.size_mask = 0
        self.full_size = 0
        self.stride = 0
        self.index = None
        self.enabled = False

        self.buf_size = 0
        self.buf_region = None
        self.buf_dma = 0
        self.buf = None

        self.ndev = None
        self.cq = None

        self.prod_ptr = 0
        self.cons_ptr = 0

        self.packets = 0
        self.bytes = 0

        self.hw_regs = None

    async def open(self, ndev, cq, size, desc_block_size):
        if self.hw_regs:
            raise Exception("Already open")

        self.index = self.interface.rxq_res.alloc()

        self.log.info("Open RXQ %d (interface %d)", self.index, self.interface.index)

        self.log_queue_size = size.bit_length() - 1
        self.log_desc_block_size = desc_block_size.bit_length() - 1
        self.desc_block_size = 2**self.log_desc_block_size
        self.size = 2**self.log_queue_size
        self.size_mask = self.size-1
        self.full_size = self.size >> 1
        self.stride = MQNIC_DESC_SIZE*self.desc_block_size

        self.rx_info = [None]*self.size

        self.buf_size = self.size*self.stride
        self.buf_region = self.driver.pool.alloc_region(self.buf_size)
        self.buf_dma = self.buf_region.get_absolute_address(0)
        self.buf = self.buf_region.mem

        self.prod_ptr = 0
        self.cons_ptr = 0

        self.ndev = ndev
        self.cq = cq
        self.cq.src_ring = self
        self.cq.handler = Rxq.process_rx_cq

        self.hw_regs = self.interface.rxq_res.get_window(self.index)

        await self.hw_regs.write_dword(MQNIC_QUEUE_CTRL_STATUS_REG, MQNIC_QUEUE_CMD_SET_ENABLE | 0)
        await self.hw_regs.write_dword(MQNIC_QUEUE_BASE_ADDR_VF_REG, self.buf_dma & 0xfffff000)
        await self.hw_regs.write_dword(MQNIC_QUEUE_BASE_ADDR_VF_REG+4, self.buf_dma >> 32)
        await self.hw_regs.write_dword(MQNIC_QUEUE_CTRL_STATUS_REG, MQNIC_QUEUE_CMD_SET_SIZE | (self.log_desc_block_size << 8) | self.log_queue_size)
        await self.hw_regs.write_dword(MQNIC_QUEUE_CTRL_STATUS_REG, MQNIC_QUEUE_CMD_SET_CQN | self.cq.cqn)
        await self.hw_regs.write_dword(MQNIC_QUEUE_CTRL_STATUS_REG, MQNIC_QUEUE_CMD_SET_PROD_PTR | (self.prod_ptr & MQNIC_QUEUE_PTR_MASK))
        await self.hw_regs.write_dword(MQNIC_QUEUE_CTRL_STATUS_REG, MQNIC_QUEUE_CMD_SET_CONS_PTR | (self.cons_ptr & MQNIC_QUEUE_PTR_MASK))

        await self.refill_buffers()

    async def close(self):
        if not self.hw_regs:
            return

        await self.disable()

        # TODO free buffer

        if self.cq:
            self.cq.src_ring = None
            self.cq.handler = None

        self.ndev = None
        self.cq = None

        self.hw_regs = None

        self.interface.rxq_res.free(self.index)
        self.index = None

    async def enable(self):
        if not self.hw_regs:
            raise Exception("Not open")

        await self.hw_regs.write_dword(MQNIC_QUEUE_CTRL_STATUS_REG, MQNIC_QUEUE_CMD_SET_ENABLE | 1)

        self.enabled = True

    async def disable(self):
        if not self.hw_regs:
            return

        await self.hw_regs.write_dword(MQNIC_QUEUE_CTRL_STATUS_REG, MQNIC_QUEUE_CMD_SET_ENABLE | 0)

        self.enabled = False

    def empty(self):
        return self.prod_ptr == self.cons_ptr

    def full(self):
        return self.prod_ptr - self.cons_ptr >= self.full_size

    async def read_cons_ptr(self):
        val = await self.hw_regs.read_dword(MQNIC_QUEUE_PTR_REG)
        self.cons_ptr += ((val >> 16) - self.cons_ptr) & MQNIC_QUEUE_PTR_MASK

    async def write_prod_ptr(self):
        await self.hw_regs.write_dword(MQNIC_QUEUE_CTRL_STATUS_REG, MQNIC_QUEUE_CMD_SET_PROD_PTR | (self.prod_ptr & MQNIC_QUEUE_PTR_MASK))

    def free_desc(self, index):
        pkt = self.rx_info[index]
        self.driver.free_pkt(pkt)
        self.rx_info[index] = None

    def free_buf(self):
        while not self.empty():
            index = self.cons_ptr & self.size_mask
            self.free_desc(index)
            self.cons_ptr += 1

    def prepare_desc(self, index):
        pkt = self.driver.alloc_pkt()
        self.rx_info[index] = pkt

        length = pkt.size
        ptr = pkt.get_absolute_address(0)
        offset = 0

        # write descriptors
        for k in range(0, self.desc_block_size):
            seg = min(length-offset, 4096) if k < self.desc_block_size-1 else length-offset
            struct.pack_into("<LLQ", self.buf, index*self.stride+k*MQNIC_DESC_SIZE, 0, seg, ptr+offset if seg else 0)
            offset += seg

    async def refill_buffers(self):
        missing = self.size - (self.prod_ptr - self.cons_ptr)

        if missing < 8:
            return

        for k in range(missing):
            self.prepare_desc(self.prod_ptr & self.size_mask)
            self.prod_ptr += 1

        await self.write_prod_ptr()

    @staticmethod
    async def process_rx_cq(cq):
        interface = cq.interface
        ndev = cq.src_ring.ndev

        interface.log.info("Process CQ %d for RXQ %d (interface %d)", cq.cqn, cq.src_ring.index, interface.index)

        ring = cq.src_ring

        if not ndev.port_up:
            interface.log.info("Port not up, aborting")
            return

        # process completion queue
        cq_cons_ptr = cq.cons_ptr
        cq_index = cq_cons_ptr & cq.size_mask

        while True:
            cpl_data = struct.unpack_from("<HHHHLHHLBBHLL", cq.buf, cq_index*cq.stride)
            ring_index = cpl_data[1] & ring.size_mask

            interface.log.info("CQ %d index %d data: %s", cq.cqn, cq_index, repr(cpl_data))

            if bool(cpl_data[-1] & 0x80000000) == bool(cq_cons_ptr & cq.size):
                interface.log.info("CQ %d empty", cq.cqn)
                break

            interface.log.info("Ring index: %d", ring_index)
            pkt = ring.rx_info[ring_index]

            length = cpl_data[2]

            skb = Packet()
            skb.data = pkt[:length]
            skb.queue = ring.index
            skb.timestamp_ns = Decimal(cpl_data[5]).scaleb(9) + Decimal(cpl_data[4]) + (Decimal(cpl_data[3]) / Decimal(2**16))
            skb.rx_checksum = cpl_data[6]

            interface.log.info("Packet: %s", skb)

            ndev.pkt_rx_queue.append(skb)
            ndev.pkt_rx_sync.set()

            ring.free_desc(ring_index)

            cq_cons_ptr += 1
            cq_index = cq_cons_ptr & cq.size_mask

        cq.cons_ptr = cq_cons_ptr
        await cq.write_cons_ptr()

        # process ring
        ring_cons_ptr = ring.cons_ptr
        ring_index = ring_cons_ptr & ring.size_mask

        while (ring_cons_ptr != ring.prod_ptr):
            if ring.rx_info[ring_index]:
                break

            ring_cons_ptr += 1
            ring_index = ring_cons_ptr & ring.size_mask

        ring.cons_ptr = ring_cons_ptr

        # replenish buffers
        await ring.refill_buffers()


class SchedulerPort:
    def __init__(self, sched, index):
        self.sched = sched
        self.index = index

        self.tc_count = sched.tc_count
        self.fc_scale = sched.fc_scale

    async def init(self):
        pass

    async def enable(self):
        await self.sched.enable()

    async def disable(self):
        await self.sched.disable()

    async def enable_ch(self, tc):
        await self.sched.enable_ch(self.index, tc)

    async def disable_ch(self, tc):
        await self.sched.disable_ch(self.index, tc)

    async def enable_queue(self, queue):
        await self.sched.enable_queue(queue)
        await self.sched.enable_queue_port(queue, self.index)

    async def disable_queue(self, queue):
        await self.sched.disable_queue_port(queue, self.index)
        await self.sched.disable_queue(queue)

    async def get_queue_enable(self, queue):
        return await self.sched.get_queue_port_enable(queue, self.index)

    async def set_queue_pause(self, queue, val):
        await self.set_queue_port_pause(queue, self.index, val)

    async def get_queue_puase(self, queue):
        return await self.sched.get_queue_port_puase(queue, self.index)

    async def set_queue_tc(self, queue, val):
        await self.set_queue_port_tc(queue, self.index, val)

    async def get_queue_tc(self, queue):
        return await self.sched.get_queue_port_tc(queue, self.index)

    async def get_ch_dest(self, tc):
        return await self.sched.get_ch_dest(self.index, tc)

    async def set_ch_dest(self, tc, val):
        await self.sched.set_ch_dest(self.index, tc, val)

    async def get_ch_pkt_budget(self, tc):
        return await self.sched.get_ch_pkt_budget(self.index, tc)

    async def set_ch_pkt_budget(self, tc, val):
        await self.sched.set_ch_pkt_budget(self.index, tc, val)

    async def get_ch_data_budget(self, tc):
        return await self.sched.get_ch_data_budget(self.index, tc)

    async def set_ch_data_budget(self, tc, val):
        await self.sched.set_ch_data_budget(self.index, tc, val)

    async def get_ch_pkt_limit(self, tc):
        return await self.sched.get_ch_pkt_limit(self.index, tc)

    async def set_ch_pkt_limit(self, tc, val):
        await self.sched.set_ch_pkt_limit(self.index, tc, val)

    async def get_ch_data_limit(self, tc):
        return await self.sched.get_ch_data_limit(self.index, tc)

    async def set_ch_data_limit(self, tc, val):
        await self.sched.set_ch_data_limit(self.index, tc, val)


class BaseScheduler:
    def __init__(self, block, index, rb):
        self.block = block
        self.log = block.log
        self.interface = block.interface
        self.driver = block.interface.driver
        self.index = index
        self.rb = rb
        self.hw_regs = None

        self.tc_count = None
        self.fc_scale = None

        self.enable_count = 0

        self.sched_ports = []

    async def init(self):
        pass

    async def enable(self):
        if self.enable_count == 0:
            await self._enable()
        self.enable_count += 1

    async def _enable(self):
        pass

    async def disable(self):
        self.enable_count -= 1
        if self.enable_count == 0:
            await self._disable()

    async def _disable(self):
        pass


class SchedulerRoundRobin(BaseScheduler):
    def __init__(self, block, index, rb):
        super().__init__(block, index, rb)

        self.queue_count = None
        self.queue_stride = None

        self.tc_count = None
        self.port_count = None
        self.channel_count = None
        self.fc_scale = None

        self.sched_ports = []

    async def init(self):
        await super().init()

        offset = await self.rb.read_dword(MQNIC_RB_SCHED_RR_REG_OFFSET)
        self.hw_regs = self.rb.parent.create_window(offset)

        self.queue_count = await self.rb.read_dword(MQNIC_RB_SCHED_RR_REG_QUEUE_COUNT)
        self.queue_stride = await self.rb.read_dword(MQNIC_RB_SCHED_RR_REG_QUEUE_STRIDE)

        self.queue_count = min(self.queue_count, MQNIC_MAX_TXQ)

        val = await self.rb.read_dword(MQNIC_RB_SCHED_RR_REG_CFG)
        self.tc_count = val & 0xff
        self.port_count = (val >> 8) & 0xff
        self.channel_count = self.port_count * self.tc_count
        self.fc_scale = 1 << ((val >> 16) & 0xff)

        for k in range(self.port_count):
            sched_port = SchedulerPort(self, k)
            self.sched_ports.append(sched_port)
            self.interface.register_sched_port(sched_port)

    async def _enable(self):
        await self.set_ctrl(1)

    async def _disable(self):
        await self.set_ctrl(0)

    async def enable_ch(self, port, tc):
        await self.set_ch_ctrl(port, tc, 1)

    async def disable_ch(self, port, tc):
        await self.set_ch_ctrl(port, tc, 0)

    async def enable_queue(self, queue):
        await self.set_queue_ctrl(queue, MQNIC_SCHED_RR_CMD_SET_QUEUE_ENABLE | 1)

    async def disable_queue(self, queue):
        await self.set_queue_ctrl(queue, MQNIC_SCHED_RR_CMD_SET_QUEUE_ENABLE | 0)

    async def enable_queue_port(self, queue, port):
        await self.set_queue_ctrl(queue, MQNIC_SCHED_RR_CMD_SET_PORT_ENABLE | (port << 8) | 1)

    async def disable_queue_port(self, queue, port):
        await self.set_queue_ctrl(queue, MQNIC_SCHED_RR_CMD_SET_PORT_ENABLE | (port << 8) | 0)

    async def get_queue_port_enable(self, queue, port):
        ctrl = await self.get_queue_ctrl(queue)
        return bool(ctrl & MQNIC_SCHED_RR_QUEUE_EN) and bool((ctrl >> 8*port) & MQNIC_SCHED_RR_PORT_EN)

    async def set_queue_port_pause(self, queue, port, val):
        await self.set_queue_ctrl(queue, MQNIC_SCHED_RR_CMD_SET_PORT_PAUSE | (port << 8) | bool(val))

    async def get_queue_port_pause(self, queue, port):
        ctrl = await self.get_queue_ctrl(queue)
        return bool((ctrl >> 8*port) & MQNIC_SCHED_RR_PORT_PAUSE)

    async def set_queue_port_tc(self, queue, port, val):
        await self.set_queue_ctrl(queue, MQNIC_SCHED_RR_CMD_SET_PORT_TC | (port << 8) | (val & 0x7))

    async def get_queue_port_tc(self, queue, port):
        ctrl = await self.get_queue_ctrl(queue)
        return bool((ctrl >> 8*port) & MQNIC_SCHED_RR_PORT_TC)

    async def disable_all_queues(self):
        for k in range(self.queue_count):
            await self.disable_queue(k)

    async def set_queue_pause(self, queue, val):
        await self.set_queue_ctrl(queue, MQNIC_SCHED_RR_CMD_SET_QUEUE_PAUSE | (1 if val else 0))

    async def get_ctrl(self):
        return await self.rb.read_dword(MQNIC_RB_SCHED_RR_REG_CTRL)

    async def set_ctrl(self, val):
        await self.rb.write_dword(MQNIC_RB_SCHED_RR_REG_CTRL, val)

    async def get_ch_ctrl(self, port, tc):
        ch = port*self.tc_count + tc
        return await self.rb.read_dword(MQNIC_RB_SCHED_RR_REG_CH0_CTRL + ch*MQNIC_RB_SCHED_RR_REG_CH_STRIDE)

    async def set_ch_ctrl(self, port, tc, val):
        ch = port*self.tc_count + tc
        await self.rb.write_dword(MQNIC_RB_SCHED_RR_REG_CH0_CTRL + ch*MQNIC_RB_SCHED_RR_REG_CH_STRIDE, val)

    async def get_ch_dest(self, port, tc):
        ch = port*self.tc_count + tc
        return await self.rb.read_word(MQNIC_RB_SCHED_RR_REG_CH0_FC1_DEST + ch*MQNIC_RB_SCHED_RR_REG_CH_STRIDE)

    async def set_ch_dest(self, port, tc, val):
        ch = port*self.tc_count + tc
        await self.rb.write_word(MQNIC_RB_SCHED_RR_REG_CH0_FC1_DEST + ch*MQNIC_RB_SCHED_RR_REG_CH_STRIDE, val)

    async def get_ch_pkt_budget(self, port, tc):
        ch = port*self.tc_count + tc
        return await self.rb.read_word(MQNIC_RB_SCHED_RR_REG_CH0_FC1_PB + ch*MQNIC_RB_SCHED_RR_REG_CH_STRIDE)

    async def set_ch_pkt_budget(self, port, tc, val):
        ch = port*self.tc_count + tc
        await self.rb.write_word(MQNIC_RB_SCHED_RR_REG_CH0_FC1_PB + ch*MQNIC_RB_SCHED_RR_REG_CH_STRIDE, val)

    async def get_ch_data_budget(self, port, tc):
        ch = port*self.tc_count + tc
        return await self.rb.read_word(MQNIC_RB_SCHED_RR_REG_CH0_FC2_DB + ch*MQNIC_RB_SCHED_RR_REG_CH_STRIDE) * self.fc_scale

    async def set_ch_data_budget(self, port, tc, val):
        ch = port*self.tc_count + tc
        val = (val + self.fc_scale-1) // self.fc_scale
        await self.rb.write_word(MQNIC_RB_SCHED_RR_REG_CH0_FC2_DB + ch*MQNIC_RB_SCHED_RR_REG_CH_STRIDE, val)

    async def get_ch_pkt_limit(self, port, tc):
        ch = port*self.tc_count + tc
        return await self.rb.read_word(MQNIC_RB_SCHED_RR_REG_CH0_FC2_PL + ch*MQNIC_RB_SCHED_RR_REG_CH_STRIDE)

    async def set_ch_pkt_limit(self, port, tc, val):
        ch = port*self.tc_count + tc
        await self.rb.write_word(MQNIC_RB_SCHED_RR_REG_CH0_FC2_PL + ch*MQNIC_RB_SCHED_RR_REG_CH_STRIDE, val)

    async def get_ch_data_limit(self, port, tc):
        ch = port*self.tc_count + tc
        return await self.rb.read_dword(MQNIC_RB_SCHED_RR_REG_CH0_FC3_DL + ch*MQNIC_RB_SCHED_RR_REG_CH_STRIDE) * self.fc_scale

    async def set_ch_data_limit(self, port, tc, val):
        ch = port*self.tc_count + tc
        val = (val + self.fc_scale-1) // self.fc_scale
        await self.rb.write_dword(MQNIC_RB_SCHED_RR_REG_CH0_FC3_DL + ch*MQNIC_RB_SCHED_RR_REG_CH_STRIDE, val)

    async def get_queue_ctrl(self, queue):
        return await self.hw_regs.read_dword(queue*4)

    async def set_queue_ctrl(self, queue, val):
        await self.hw_regs.write_dword(queue*4, val)


class SchedulerControlTdma(BaseScheduler):
    def __init__(self, block, index, rb):
        super().__init__(block, index, rb)

    async def init(self):
        await super().init()
        offset = await self.rb.read_dword(MQNIC_RB_SCHED_CTRL_TDMA_REG_OFFSET)
        self.hw_regs = self.rb.parent.create_window(offset)

    async def _enable(self):
        pass

    async def _disable(self):
        pass


class SchedulerBlock:
    def __init__(self, interface, index, rb):
        self.interface = interface
        self.log = interface.log
        self.driver = interface.driver
        self.index = index

        self.block_rb = rb
        self.reg_blocks = RegBlockList()

        self.sched_count = None

        self.schedulers = []

    async def init(self):
        # Read ID registers

        offset = await self.block_rb.read_dword(MQNIC_RB_SCHED_BLOCK_REG_OFFSET)
        await self.reg_blocks.enumerate_reg_blocks(self.block_rb.parent, offset)

        self.schedulers = []

        self.sched_count = 0
        for rb in self.reg_blocks:
            if rb.type == MQNIC_RB_SCHED_RR_TYPE and rb.version == MQNIC_RB_SCHED_RR_VER:
                s = SchedulerRoundRobin(self, self.sched_count, rb)
                await s.init()
                self.schedulers.append(s)

                self.sched_count += 1
            elif rb.type == MQNIC_RB_SCHED_CTRL_TDMA_TYPE and rb.version == MQNIC_RB_SCHED_CTRL_TDMA_VER:
                s = SchedulerControlTdma(self, self.sched_count, rb)
                await s.init()
                self.schedulers.append(s)

                self.sched_count += 1

        self.log.info("Scheduler count: %d", self.sched_count)

    async def activate(self):
        for sched in self.schedulers:
            await sched.enable()

    async def deactivate(self):
        for sched in self.schedulers:
            await sched.disable()


class NetDev:
    def __init__(self, interface, port):
        self.interface = interface
        self.log = interface.log
        self.driver = interface.driver
        self.port_up = False

        self.port = port
        self.sched_port = None

        self.txq_count = min(interface.txq_res.get_count() // interface.port_count, 4)
        self.rxq_count = min(interface.rxq_res.get_count() // interface.port_count, 4)

        self.rx_queue_map_indir_table_size = interface.rx_queue_map_indir_table_size
        self.rx_queue_map_indir_table = [k % self.rxq_count for k in range(self.rx_queue_map_indir_table_size)]

        self.txq = []
        self.rxq = []

        self.tx_ring_size = 1024
        self.rx_ring_size = 1024

        self.pkt_rx_queue = deque()
        self.pkt_rx_sync = Event()

    async def init(self):
        pass

    async def open(self):
        if self.port_up:
            return

        self.sched_port = self.interface.alloc_sched_port()

        for k in range(self.rxq_count):
            cq = self.interface.create_cq()
            await cq.open(self.interface.eq[k % len(self.interface.eq)], 1024)
            await cq.arm()
            rxq = self.interface.create_rxq()
            await rxq.open(self, cq, self.rx_ring_size, 4)
            self.rxq.append(rxq)

        for k in range(self.txq_count):
            cq = self.interface.create_cq()
            await cq.open(self.interface.eq[k % len(self.interface.eq)], 1024)
            await cq.arm()
            txq = self.interface.create_txq()
            await txq.open(self, cq, self.tx_ring_size, 4)
            self.txq.append(txq)

        for k in range(self.rx_queue_map_indir_table_size):
            self.rx_queue_map_indir_table[k] = k % self.rxq_count

        # configure RX indirection and RSS
        await self.update_rx_queue_map_indir_table()

        # enable queues
        for q in self.rxq:
            await q.enable()

        for q in self.txq:
            await q.enable()

        # enable transmit
        await self.port.set_tx_ctrl(MQNIC_PORT_TX_CTRL_EN)

        # configure scheduler
        for q in self.txq:
            await self.sched_port.enable_queue(q.index)

        # configure scheduler flow control
        await self.sched_port.set_ch_dest(0, self.port.index << 4 | 0)
        await self.sched_port.set_ch_pkt_budget(0, 1)
        await self.sched_port.set_ch_data_budget(0, self.interface.max_tx_mtu)
        await self.sched_port.set_ch_pkt_limit(0, 0xFFFF)
        await self.sched_port.set_ch_data_limit(0, self.interface.tx_fifo_depth)

        await self.sched_port.enable_ch(0)

        # enable scheduler
        await self.sched_port.enable()

        # enable receive
        await self.port.set_rx_ctrl(MQNIC_PORT_RX_CTRL_EN)

        # wait for all writes to complete
        await self.interface.hw_regs.read_dword(0)

        self.port_up = True

    async def close(self):
        if not self.port_up:
            return

        self.port_up = False

        await self.ports[0].set_rx_ctrl(0)

        for q in self.txq:
            q.disable()

        for q in self.rxq:
            q.disable()

        # configure scheduler
        for q in self.txq:
            await self.sched_port.disable_queue(q.index)

        # configure scheduler flow control
        await self.sched_port.disable_ch(0)

        # enable scheduler
        await self.sched_port.disable()

        # wait for all writes to complete
        await self.hw_regs.read_dword(0)

        for q in self.txq:
            cq = q.cq
            await q.free_buf()
            await q.close()
            await cq.close()

        for q in self.rxq:
            cq = q.cq
            await q.free_buf()
            await q.close()
            await cq.close()

        self.txq = []
        self.rxq = []

        await self.ports[0].set_tx_ctrl(0)

        self.interface.free_sched_port(self.sched_port)
        self.sched_port = None

    async def start_xmit(self, skb, tx_ring=None, csum_start=None, csum_offset=None):
        if not self.port_up:
            return

        data = bytes(skb)

        assert len(data) < self.interface.max_tx_mtu

        if tx_ring is not None:
            ring_index = tx_ring
        else:
            ring_index = 0

        ring = self.txq[ring_index]

        while True:
            # check for space in ring
            if ring.prod_ptr - ring.cons_ptr < ring.full_size:
                break

            # wait for space
            ring.clean_event.clear()
            await ring.clean_event.wait()

        index = ring.prod_ptr & ring.size_mask

        ring.packets += 1
        ring.bytes += len(data)

        pkt = self.driver.alloc_pkt()

        assert not ring.tx_info[index]
        ring.tx_info[index] = pkt

        # put data in packet buffer
        pkt[10:len(data)+10] = data

        csum_cmd = 0

        if csum_start is not None and csum_offset is not None:
            csum_cmd = 0x8000 | (csum_offset << 8) | csum_start

        length = len(data)
        ptr = pkt.get_absolute_address(0)+10
        offset = 0

        # write descriptors
        seg = min(length-offset, 42) if ring.desc_block_size > 1 else length-offset
        struct.pack_into("<HHLQ", ring.buf, index*ring.stride, 0, csum_cmd, seg, ptr+offset if seg else 0)
        offset += seg
        for k in range(1, ring.desc_block_size):
            seg = min(length-offset, 4096) if k < ring.desc_block_size-1 else length-offset
            struct.pack_into("<4xLQ", ring.buf, index*ring.stride+k*MQNIC_DESC_SIZE, seg, ptr+offset if seg else 0)
            offset += seg

        ring.prod_ptr += 1

        await ring.write_prod_ptr()

    async def update_rx_queue_map_indir_table(self):
        await self.interface.set_rx_queue_map_rss_mask(self.port.index, 0xffffffff)
        await self.interface.set_rx_queue_map_app_mask(self.port.index, 0)

        for k in range(self.rx_queue_map_indir_table_size):
            q = self.rxq[self.rx_queue_map_indir_table[k]]
            if q:
                await self.interface.set_rx_queue_map_indir_table(self.port.index, k, q.index)

    async def recv(self):
        if not self.pkt_rx_queue:
            self.pkt_rx_sync.clear()
            await self.pkt_rx_sync.wait()
        return self.recv_nowait()

    def recv_nowait(self):
        if self.pkt_rx_queue:
            return self.pkt_rx_queue.popleft()
        return None

    async def wait(self):
        if not self.pkt_rx_queue:
            self.pkt_rx_sync.clear()
            await self.pkt_rx_sync.wait()


class Port:
    def __init__(self, interface, index, rb):
        self.interface = interface
        self.log = interface.log
        self.driver = interface.driver
        self.index = index

        self.port_rb = rb
        self.reg_blocks = RegBlockList()
        self.port_ctrl_rb = None

        self.port_features = None
        self.port_feature_lfc = None
        self.port_feature_pfc = None
        self.port_feature_int_mac_ctrl = None

    async def init(self):
        # Read ID registers

        offset = await self.port_rb.read_dword(MQNIC_RB_PORT_REG_OFFSET)
        await self.reg_blocks.enumerate_reg_blocks(self.port_rb.parent, offset)

        self.port_ctrl_rb = self.reg_blocks.find(MQNIC_RB_PORT_CTRL_TYPE, MQNIC_RB_PORT_CTRL_VER)

        self.port_features = await self.port_ctrl_rb.read_dword(MQNIC_RB_PORT_CTRL_REG_FEATURES)
        self.port_feature_lfc = bool(self.port_features & MQNIC_PORT_FEATURE_LFC)
        self.port_feature_pfc = bool(self.port_features & MQNIC_PORT_FEATURE_PFC)
        self.port_feature_int_mac_ctrl = bool(self.port_features & MQNIC_PORT_FEATURE_INT_MAC_CTRL)

        self.log.info("Port features: 0x%08x", self.port_features)

        await self.set_tx_ctrl(0)
        await self.set_rx_ctrl(0)
        await self.set_lfc_ctrl(0)

        for k in range(8):
            await self.set_pfc_ctrl(k, 0)

    async def get_tx_ctrl(self):
        return await self.port_ctrl_rb.read_dword(MQNIC_RB_PORT_CTRL_REG_TX_CTRL)

    async def set_tx_ctrl(self, val):
        await self.port_ctrl_rb.write_dword(MQNIC_RB_PORT_CTRL_REG_TX_CTRL, val)

    async def get_rx_ctrl(self):
        return await self.port_ctrl_rb.read_dword(MQNIC_RB_PORT_CTRL_REG_RX_CTRL)

    async def set_rx_ctrl(self, val):
        await self.port_ctrl_rb.write_dword(MQNIC_RB_PORT_CTRL_REG_RX_CTRL, val)

    async def get_lfc_ctrl(self):
        return await self.port_ctrl_rb.read_dword(MQNIC_RB_PORT_CTRL_REG_LFC_CTRL)

    async def set_lfc_ctrl(self, val):
        await self.port_ctrl_rb.write_dword(MQNIC_RB_PORT_CTRL_REG_LFC_CTRL, val)

    async def get_pfc_ctrl(self, index):
        return await self.port_ctrl_rb.read_dword(MQNIC_RB_PORT_CTRL_REG_PFC_CTRL0 + 4*index)

    async def set_pfc_ctrl(self, index, val):
        await self.port_ctrl_rb.write_dword(MQNIC_RB_PORT_CTRL_REG_PFC_CTRL0 + 4*index, val)


class Interface:
    def __init__(self, driver, index, hw_regs):
        self.driver = driver
        self.log = driver.log
        self.index = index
        self.hw_regs = hw_regs
        self.csr_hw_regs = hw_regs.create_window(driver.if_csr_offset)

        self.reg_blocks = RegBlockList()
        self.if_ctrl_rb = None
        self.eq_rb = None
        self.cq_rb = None
        self.txq_rb = None
        self.rxq_rb = None
        self.rx_queue_map_rb = None

        self.if_features = None
        self.if_feature_rss = None
        self.if_feature_ptp_ts = None
        self.if_feature_tx_csum = None
        self.if_feature_rx_csum = None
        self.if_feature_rx_hash = None

        self.max_tx_mtu = 0
        self.max_rx_mtu = 0
        self.tx_fifo_depth = 0
        self.rx_fifo_depth = 0

        self.eq_res = None
        self.cq_res = None
        self.txq_res = None
        self.rxq_res = None

        self.port_count = None
        self.sched_block_count = None

        self.rx_queue_map_indir_table_size = None
        self.rx_queue_map_indir_table_regs = []

        self.eq = []

        self.ports = []
        self.sched_blocks = []
        self.ndevs = []

        self.sched_ports = []
        self.free_sched_ports = []

        self.interrupt_running = False
        self.interrupt_pending = 0

    async def init(self):
        # Read ID registers

        # Enumerate registers
        await self.reg_blocks.enumerate_reg_blocks(self.hw_regs, self.driver.if_csr_offset)

        self.if_ctrl_rb = self.reg_blocks.find(MQNIC_RB_IF_CTRL_TYPE, MQNIC_RB_IF_CTRL_VER)

        self.if_features = await self.if_ctrl_rb.read_dword(MQNIC_RB_IF_CTRL_REG_FEATURES)
        self.port_count = await self.if_ctrl_rb.read_dword(MQNIC_RB_IF_CTRL_REG_PORT_COUNT)
        self.sched_block_count = await self.if_ctrl_rb.read_dword(MQNIC_RB_IF_CTRL_REG_SCHED_COUNT)
        self.max_tx_mtu = await self.if_ctrl_rb.read_dword(MQNIC_RB_IF_CTRL_REG_MAX_TX_MTU)
        self.max_rx_mtu = await self.if_ctrl_rb.read_dword(MQNIC_RB_IF_CTRL_REG_MAX_RX_MTU)
        self.tx_fifo_depth = await self.if_ctrl_rb.read_dword(MQNIC_RB_IF_CTRL_REG_TX_FIFO_DEPTH)
        self.rx_fifo_depth = await self.if_ctrl_rb.read_dword(MQNIC_RB_IF_CTRL_REG_RX_FIFO_DEPTH)

        self.if_feature_rss = bool(self.if_features & MQNIC_IF_FEATURE_RSS)
        self.if_feature_ptp_ts = bool(self.if_features & MQNIC_IF_FEATURE_PTP_TS)
        self.if_feature_tx_csum = bool(self.if_features & MQNIC_IF_FEATURE_TX_CSUM)
        self.if_feature_rx_csum = bool(self.if_features & MQNIC_IF_FEATURE_RX_CSUM)
        self.if_feature_rx_hash = bool(self.if_features & MQNIC_IF_FEATURE_RX_HASH)
        self.if_feature_lfc = bool(self.if_features & MQNIC_IF_FEATURE_LFC)
        self.if_feature_pfc = bool(self.if_features & MQNIC_IF_FEATURE_PFC)

        self.log.info("IF features: 0x%08x", self.if_features)
        self.log.info("Port count: %d", self.port_count)
        self.log.info("Scheduler block count: %d", self.sched_block_count)
        self.log.info("Max TX MTU: %d", self.max_tx_mtu)
        self.log.info("Max RX MTU: %d", self.max_rx_mtu)

        await self.set_mtu(min(self.max_tx_mtu, self.max_rx_mtu, 9214))

        self.eq_rb = self.reg_blocks.find(MQNIC_RB_EQM_TYPE, MQNIC_RB_EQM_VER)

        offset = await self.eq_rb.read_dword(MQNIC_RB_EQM_REG_OFFSET)
        count = await self.eq_rb.read_dword(MQNIC_RB_EQM_REG_COUNT)
        stride = await self.eq_rb.read_dword(MQNIC_RB_EQM_REG_STRIDE)

        self.log.info("EQ offset: 0x%08x", offset)
        self.log.info("EQ count: %d", count)
        self.log.info("EQ stride: 0x%08x", stride)

        count = min(count, MQNIC_MAX_EQ)

        self.eq_res = Resource(count, self.hw_regs.create_window(offset), stride)

        self.cq_rb = self.reg_blocks.find(MQNIC_RB_CQM_TYPE, MQNIC_RB_CQM_VER)

        offset = await self.cq_rb.read_dword(MQNIC_RB_CQM_REG_OFFSET)
        count = await self.cq_rb.read_dword(MQNIC_RB_CQM_REG_COUNT)
        stride = await self.cq_rb.read_dword(MQNIC_RB_CQM_REG_STRIDE)

        self.log.info("CQ offset: 0x%08x", offset)
        self.log.info("CQ count: %d", count)
        self.log.info("CQ stride: 0x%08x", stride)

        count = min(count, MQNIC_MAX_CQ)

        self.cq_res = Resource(count, self.hw_regs.create_window(offset), stride)

        self.txq_rb = self.reg_blocks.find(MQNIC_RB_TX_QM_TYPE, MQNIC_RB_TX_QM_VER)

        offset = await self.txq_rb.read_dword(MQNIC_RB_TX_QM_REG_OFFSET)
        count = await self.txq_rb.read_dword(MQNIC_RB_TX_QM_REG_COUNT)
        stride = await self.txq_rb.read_dword(MQNIC_RB_TX_QM_REG_STRIDE)

        self.log.info("TXQ offset: 0x%08x", offset)
        self.log.info("TXQ count: %d", count)
        self.log.info("TXQ stride: 0x%08x", stride)

        count = min(count, MQNIC_MAX_TXQ)

        self.txq_res = Resource(count, self.hw_regs.create_window(offset), stride)

        self.rxq_rb = self.reg_blocks.find(MQNIC_RB_RX_QM_TYPE, MQNIC_RB_RX_QM_VER)

        offset = await self.rxq_rb.read_dword(MQNIC_RB_RX_QM_REG_OFFSET)
        count = await self.rxq_rb.read_dword(MQNIC_RB_RX_QM_REG_COUNT)
        stride = await self.rxq_rb.read_dword(MQNIC_RB_RX_QM_REG_STRIDE)

        self.log.info("RXQ offset: 0x%08x", offset)
        self.log.info("RXQ count: %d", count)
        self.log.info("RXQ stride: 0x%08x", stride)

        count = min(count, MQNIC_MAX_RXQ)

        self.rxq_res = Resource(count, self.hw_regs.create_window(offset), stride)

        self.rx_queue_map_rb = self.reg_blocks.find(MQNIC_RB_RX_QUEUE_MAP_TYPE, MQNIC_RB_RX_QUEUE_MAP_VER)

        val = await self.rx_queue_map_rb.read_dword(MQNIC_RB_RX_QUEUE_MAP_REG_CFG)
        self.rx_queue_map_indir_table_size = 2**((val >> 8) & 0xff)
        self.rx_queue_map_indir_table_regs = []
        self.rx_queue_map_indir_table = []
        for k in range(self.port_count):
            offset = await self.rx_queue_map_rb.read_dword(MQNIC_RB_RX_QUEUE_MAP_CH_OFFSET +
                    MQNIC_RB_RX_QUEUE_MAP_CH_STRIDE*k + MQNIC_RB_RX_QUEUE_MAP_CH_REG_OFFSET)
            self.rx_queue_map_indir_table_regs.append(self.rx_queue_map_rb.parent.create_window(offset))
            self.rx_queue_map_indir_table.append([0 for x in range(self.rx_queue_map_indir_table_size)])

            await self.set_rx_queue_map_rss_mask(k, 0)
            await self.set_rx_queue_map_app_mask(k, 0)
            await self.set_rx_queue_map_indir_table(k, 0, 0)

        # ensure all queues are disabled
        for k in range(self.eq_res.get_count()):
            await self.eq_res.get_window(k).write_dword(MQNIC_EQ_CTRL_STATUS_REG, MQNIC_QUEUE_CMD_SET_ENABLE | 0)

        for k in range(self.cq_res.get_count()):
            await self.cq_res.get_window(k).write_dword(MQNIC_CQ_CTRL_STATUS_REG, MQNIC_QUEUE_CMD_SET_ENABLE | 0)

        for k in range(self.txq_res.get_count()):
            await self.txq_res.get_window(k).write_dword(MQNIC_QUEUE_CTRL_STATUS_REG, MQNIC_QUEUE_CMD_SET_ENABLE | 0)

        for k in range(self.rxq_res.get_count()):
            await self.rxq_res.get_window(k).write_dword(MQNIC_QUEUE_CTRL_STATUS_REG, MQNIC_QUEUE_CMD_SET_ENABLE | 0)

        # create ports
        self.ports = []
        for k in range(self.port_count):
            rb = self.reg_blocks.find(MQNIC_RB_PORT_TYPE, MQNIC_RB_PORT_VER, index=k)

            p = Port(self, k, rb)
            await p.init()
            self.ports.append(p)

        # create schedulers
        self.sched_blocks = []
        for k in range(self.sched_block_count):
            rb = self.reg_blocks.find(MQNIC_RB_SCHED_BLOCK_TYPE, MQNIC_RB_SCHED_BLOCK_VER, index=k)

            s = SchedulerBlock(self, k, rb)
            await s.init()
            self.sched_blocks.append(s)

        assert self.sched_block_count == len(self.sched_blocks)

        # create EQs
        self.eq = []
        for k in range(self.eq_res.get_count()):
            eq = self.create_eq()
            await eq.open(self.index, 1024)
            self.eq.append(eq)
            await eq.arm()

        # create netdevs
        for port in self.ports:
            ndev = NetDev(self, port)
            await ndev.init()
            self.ndevs.append(ndev)

        # wait for all writes to complete
        await self.hw_regs.read_dword(0)

    async def set_mtu(self, mtu):
        await self.if_ctrl_rb.write_dword(MQNIC_RB_IF_CTRL_REG_TX_MTU, mtu)
        await self.if_ctrl_rb.write_dword(MQNIC_RB_IF_CTRL_REG_RX_MTU, mtu)

    async def get_rx_queue_map_rss_mask(self, port):
        return await self.rx_queue_map_rb.read_dword(MQNIC_RB_RX_QUEUE_MAP_CH_OFFSET +
            MQNIC_RB_RX_QUEUE_MAP_CH_STRIDE*port + MQNIC_RB_RX_QUEUE_MAP_CH_REG_RSS_MASK)

    async def set_rx_queue_map_rss_mask(self, port, val):
        await self.rx_queue_map_rb.write_dword(MQNIC_RB_RX_QUEUE_MAP_CH_OFFSET +
            MQNIC_RB_RX_QUEUE_MAP_CH_STRIDE*port + MQNIC_RB_RX_QUEUE_MAP_CH_REG_RSS_MASK, val)

    async def get_rx_queue_map_app_mask(self, port):
        return await self.rx_queue_map_rb.read_dword(MQNIC_RB_RX_QUEUE_MAP_CH_OFFSET +
            MQNIC_RB_RX_QUEUE_MAP_CH_STRIDE*port + MQNIC_RB_RX_QUEUE_MAP_CH_REG_APP_MASK)

    async def set_rx_queue_map_app_mask(self, port, val):
        await self.rx_queue_map_rb.write_dword(MQNIC_RB_RX_QUEUE_MAP_CH_OFFSET +
            MQNIC_RB_RX_QUEUE_MAP_CH_STRIDE*port + MQNIC_RB_RX_QUEUE_MAP_CH_REG_APP_MASK, val)

    async def get_rx_queue_map_indir_table(self, port, index):
        return await self.rx_queue_map_indir_table_regs[port].read_dword(index*4)

    async def set_rx_queue_map_indir_table(self, port, index, val):
        await self.rx_queue_map_indir_table_regs[port].write_dword(index*4, val)

    def create_eq(self):
        return Eq(self)

    def create_cq(self):
        return Cq(self)

    def create_txq(self):
        return Txq(self)

    def create_rxq(self):
        return Rxq(self)

    def register_sched_port(self, sched_port):
        self.sched_ports.append(sched_port)
        self.free_sched_ports.append(sched_port)

    def alloc_sched_port(self):
        return self.free_sched_ports.pop(0)

    def free_sched_port(self, sched_port):
        assert sched_port is not None
        assert sched_port in self.sched_ports
        assert sched_port not in self.free_sched_ports
        self.free_sched_ports.append(sched_port)


class Interrupt:
    def __init__(self, index, handler=None):
        self.index = index
        self.queue = Queue()
        self.handler = handler

        cocotb.start_soon(self._run())

    @classmethod
    def from_edge(cls, index, signal, handler=None):
        obj = cls(index, handler)
        obj.signal = signal
        cocotb.start_soon(obj._run_edge())
        return obj

    async def interrupt(self):
        self.queue.put_nowait(None)

    async def _run(self):
        while True:
            await self.queue.get()
            if self.handler:
                await self.handler(self.index)

    async def _run_edge(self):
        while True:
            await RisingEdge(self.signal)
            self.interrupt()


class Driver:
    def __init__(self):
        self.log = SimLog("cocotb.mqnic")

        self.dev = None
        self.pool = None

        self.hw_regs = None
        self.app_hw_regs = None
        self.ram_hw_regs = None

        self.irq_sig = None
        self.irq_list = []

        self.reg_blocks = RegBlockList()
        self.fw_id_rb = None
        self.if_rb = None
        self.phc_rb = None

        self.fpga_id = None
        self.fw_id = None
        self.fw_ver = None
        self.board_id = None
        self.board_ver = None
        self.build_date = None
        self.build_time = None
        self.git_hash = None
        self.rel_info = None

        self.app_id = None

        self.if_offset = None
        self.if_count = None
        self.if_stride = None
        self.if_csr_offset = None

        self.initialized = False
        self.interrupt_running = False

        self.if_count = 1
        self.interfaces = []

        self.pkt_buf_size = 16384
        self.allocated_packets = []
        self.free_packets = deque()

    async def init_pcie_dev(self, dev):
        assert not self.initialized
        self.initialized = True

        self.dev = dev

        self.pool = self.dev.rc.mem_pool

        await self.dev.enable_device()
        await self.dev.set_master()
        await self.dev.alloc_irq_vectors(1, MQNIC_MAX_EQ)

        self.hw_regs = self.dev.bar_window[0]
        self.app_hw_regs = self.dev.bar_window[2]
        self.ram_hw_regs = self.dev.bar_window[4]

        # set up MSI
        for index in range(32):
            irq = Interrupt(index, self.interrupt_handler)
            self.dev.request_irq(index, irq.interrupt)
            self.irq_list.append(irq)

        await self.init_common()

    async def init_axi_dev(self, pool, hw_regs, app_hw_regs=None, irq=None):
        assert not self.initialized
        self.initialized = True

        self.pool = pool

        self.hw_regs = hw_regs
        self.app_hw_regs = app_hw_regs

        # set up edge-triggered interrupts
        if irq:
            for index in range(len(irq)):
                self.irq_list.append(Interrupt(index, self.interrupt_handler))
            cocotb.start_soon(self._run_edge_interrupts(irq))

        await self.init_common()

    async def init_common(self):
        self.log.info("Control BAR size: %d", self.hw_regs.size)
        if self.app_hw_regs:
            self.log.info("Application BAR size: %d", self.app_hw_regs.size)
        if self.ram_hw_regs:
            self.log.info("RAM BAR size: %d", self.ram_hw_regs.size)

        # Enumerate registers
        await self.reg_blocks.enumerate_reg_blocks(self.hw_regs)

        # Read ID registers
        self.fw_id_rb = self.reg_blocks.find(MQNIC_RB_FW_ID_TYPE, MQNIC_RB_FW_ID_VER)

        self.fpga_id = await self.fw_id_rb.read_dword(MQNIC_RB_FW_ID_REG_FPGA_ID)
        self.log.info("FPGA JTAG ID: 0x%08x", self.fpga_id)
        self.fw_id = await self.fw_id_rb.read_dword(MQNIC_RB_FW_ID_REG_FW_ID)
        self.log.info("FW ID: 0x%08x", self.fw_id)
        self.fw_ver = await self.fw_id_rb.read_dword(MQNIC_RB_FW_ID_REG_FW_VER)
        self.log.info("FW version: %d.%d.%d.%d", *self.fw_ver.to_bytes(4, 'big'))
        self.board_id = await self.fw_id_rb.read_dword(MQNIC_RB_FW_ID_REG_BOARD_ID)
        self.log.info("Board ID: 0x%08x", self.board_id)
        self.board_ver = await self.fw_id_rb.read_dword(MQNIC_RB_FW_ID_REG_BOARD_VER)
        self.log.info("Board version: %d.%d.%d.%d", *self.board_ver.to_bytes(4, 'big'))
        self.build_date = await self.fw_id_rb.read_dword(MQNIC_RB_FW_ID_REG_BUILD_DATE)
        self.log.info("Build date: %s UTC (raw: 0x%08x)", datetime.datetime.utcfromtimestamp(self.build_date).isoformat(' '), self.build_date)
        self.git_hash = await self.fw_id_rb.read_dword(MQNIC_RB_FW_ID_REG_GIT_HASH)
        self.log.info("Git hash: %08x", self.git_hash)
        self.rel_info = await self.fw_id_rb.read_dword(MQNIC_RB_FW_ID_REG_REL_INFO)
        self.log.info("Release info: %d", self.rel_info)

        rb = self.reg_blocks.find(MQNIC_RB_APP_INFO_TYPE, MQNIC_RB_APP_INFO_VER)

        if rb:
            self.app_id = await rb.read_dword(MQNIC_RB_APP_INFO_REG_ID)
            self.log.info("Application ID: 0x%08x", self.app_id)

        self.phc_rb = self.reg_blocks.find(MQNIC_RB_PHC_TYPE, MQNIC_RB_PHC_VER)

        # Enumerate interfaces
        self.if_rb = self.reg_blocks.find(MQNIC_RB_IF_TYPE, MQNIC_RB_IF_VER)
        self.interfaces = []

        if self.if_rb:
            self.if_offset = await self.if_rb.read_dword(MQNIC_RB_IF_REG_OFFSET)
            self.log.info("IF offset: %d", self.if_offset)
            self.if_count = await self.if_rb.read_dword(MQNIC_RB_IF_REG_COUNT)
            self.log.info("IF count: %d", self.if_count)
            self.if_stride = await self.if_rb.read_dword(MQNIC_RB_IF_REG_STRIDE)
            self.log.info("IF stride: 0x%08x", self.if_stride)
            self.if_csr_offset = await self.if_rb.read_dword(MQNIC_RB_IF_REG_CSR_OFFSET)
            self.log.info("IF CSR offset: 0x%08x", self.if_csr_offset)

            for k in range(self.if_count):
                i = Interface(self, k, self.hw_regs.create_window(self.if_offset + k*self.if_stride, self.if_stride))
                await i.init()
                self.interfaces.append(i)

        else:
            self.log.warning("No interface block found")

    async def _run_edge_interrupts(self, signal):
        last_val = 0
        count = len(signal)
        while True:
            await Edge(signal)
            val = signal.value.integer
            edge = val & ~last_val
            for index in (x for x in range(count) if edge & (1 << x)):
                await self.irq_list[index].interrupt()

    async def interrupt_handler(self, index):
        self.log.info("Interrupt handler start (IRQ %d)", index)
        for i in self.interfaces:
            for eq in i.eq:
                if eq.irq == index:
                    await eq.process_eq()
                    await eq.arm()
        self.log.info("Interrupt handler end (IRQ %d)", index)

    def alloc_pkt(self):
        if self.free_packets:
            return self.free_packets.popleft()

        pkt = self.pool.alloc_region(self.pkt_buf_size)
        self.allocated_packets.append(pkt)
        return pkt

    def free_pkt(self, pkt):
        assert pkt is not None
        assert pkt in self.allocated_packets
        self.free_packets.append(pkt)
