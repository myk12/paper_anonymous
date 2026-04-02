"""Minimal host-side programming helpers for the Sync-DCN subsystem.

This module mirrors the active subsystem ABI implemented in the RTL and the
focused subsystem testbench. It provides a small reusable layer that can be
shared by:

- bring-up scripts that poke BAR registers directly
- future user-space utilities
- higher-level offline schedule compilers that emit local execution programs

The helper does not depend on a specific device-access backend.  Instead, the
caller provides `read32` and `write32` callables that implement 32-bit MMIO
access to the Sync-DCN AXI-Lite register space.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable, List


Read32Fn = Callable[[int], int]
Write32Fn = Callable[[int, int], None]


class SyncDcnAppId:
    """Application ids shared with the RTL execution-table metadata."""

    NONE = 0x0
    CONSENSUS = 0x1
    AI_REPLAY = 0x2


class SyncDcnPlaneId:
    """Plane ids shared with the RTL execution-table metadata."""

    EPS = 0x0
    OCS = 0x1


class SyncDcnOpcode:
    """Current compiled execution opcodes."""

    IDLE = 0x00
    GUARD = 0x01
    CONS_TX = 0x10
    CONS_RX = 0x11
    AI_TX = 0x20
    AI_RX = 0x21
    RECONFIG = 0x30


class SyncDcnFlags:
    """Execution-entry behavior flags stored in word5[7:0]."""

    VALID = 0x01
    TX_ENABLE = 0x02
    RX_ENABLE = 0x04
    DROP_NONMATCHING = 0x08
    EXPECT_PACKET = 0x10
    COMPLETION_EVENT = 0x20


class SyncDcnRegister:
    """AXI-Lite register offsets for the active subsystem ABI."""

    ID = 0x0000
    VERSION = 0x0004
    CTRL = 0x0008
    CTRL_ETHERTYPE = 0x000C
    CTRL_STATUS = 0x0010
    EXEC_STATUS = 0x0014
    LEGACY_WINDOW_PERIOD = 0x0018
    ACTIVATE_TIME_LO = 0x001C
    ACTIVATE_TIME_HI = 0x0020
    ADMIN = 0x0024
    CURRENT_WINDOW_LO = 0x0028
    CURRENT_WINDOW_HI = 0x002C
    ACTIVE_TARGET = 0x0030
    WINDOW_STATUS = 0x0034
    CURRENT_ENTRY_PTR = 0x0038
    ACTIVE_START_TIME_LO = 0x003C
    ACTIVE_START_TIME_HI = 0x0040
    ACTIVE_END_TIME_LO = 0x0044
    ACTIVE_APP_INFO = 0x0048
    ACTIVE_CONTEXT = 0x004C
    ACTIVE_END_TIME_HI = 0x0050
    AI_ENABLE = 0x0054
    AI_PKT_SENT_COUNT = 0x0058
    ACTIVE_ENTRY_META = 0x005C
    BANK_STATUS = 0x0060
    PENDING_TIME_LO = 0x0064
    PENDING_TIME_HI = 0x0068
    AI_RX_PKT_COUNT = 0x006C
    AI_RX_BYTE_COUNT = 0x0070
    AI_RX_MATCH_COUNT = 0x0074
    AI_RX_DROP_COUNT = 0x0078
    CONSENSUS_CTRL = 0x007C
    CONSENSUS_STATUS = 0x008C


TX_EXEC_TABLE_BASE = 0x1000
RX_EXEC_TABLE_BASE = 0x5800
AI_TRACE_TABLE_BASE = 0x9000
ENTRY_STRIDE_BYTES = 32
EXEC_ENTRY_WORDS = 8
AI_TRACE_ENTRY_WORDS = 6
TX_EXEC_VISIBLE_ENTRY_COUNT = 576
RX_EXEC_VISIBLE_ENTRY_COUNT = 448
AI_TRACE_VISIBLE_ENTRY_COUNT = 896


def _split_u64(value: int) -> tuple[int, int]:
    """Split a Python integer into little-endian low/high 32-bit words."""

    if value < 0:
        raise ValueError("Expected an unsigned 64-bit value")
    return value & 0xFFFFFFFF, (value >> 32) & 0xFFFFFFFF


@dataclass(frozen=True)
class ExecutionEntry:
    """One local execution-table instruction for the schedule executor.

    The entry format matches the 8-word ABI consumed by
    `sync_schedule_executor`.
    """

    start_time_ns: int
    end_time_ns: int
    context_id: int
    opcode: int
    plane_id: int
    app_id: int
    target_port: int = 0
    queue_id: int = 0
    flags: int = 0
    dst_node_id: int = 0
    flow_id: int = 0
    reserved_word7: int = 0

    def encode_words(self) -> List[int]:
        """Encode the entry into the exact 8-word hardware table layout."""

        start_lo, start_hi = _split_u64(self.start_time_ns)
        end_lo, end_hi = _split_u64(self.end_time_ns)

        return [
            start_lo,
            start_hi,
            end_lo,
            end_hi,
            ((self.context_id & 0xFFFF) << 16)
            | ((self.opcode & 0xFF) << 8)
            | ((self.plane_id & 0xF) << 4)
            | (self.app_id & 0xF),
            ((self.queue_id & 0xFFFF) << 16)
            | ((self.target_port & 0xFF) << 8)
            | (self.flags & 0xFF),
            ((self.dst_node_id & 0xFFFF) << 16) | (self.flow_id & 0xFFFF),
            self.reserved_word7 & 0xFFFFFFFF,
        ]


@dataclass(frozen=True)
class AiTraceEntry:
    """One synthetic AI replay record for `ai_trace_replay`.

    The current engine uses a compact 6-word record.  The table still reserves
    32 bytes per entry to keep the addressing rule identical to the execution
    table.
    """

    packet_count: int
    packet_len: int
    gap_cycles: int
    dst_mac_lo: int
    ethertype: int
    dst_mac_hi: int
    dst_node_id: int
    flow_id: int
    payload_seed: int

    def encode_words(self) -> List[int]:
        """Encode the trace entry into the hardware's 6-word record format."""

        return [
            ((self.packet_count & 0xFFFF) << 16) | (self.packet_len & 0xFFFF),
            self.gap_cycles & 0xFFFFFFFF,
            self.dst_mac_lo & 0xFFFFFFFF,
            ((self.ethertype & 0xFFFF) << 16) | (self.dst_mac_hi & 0xFFFF),
            ((self.dst_node_id & 0xFFFF) << 16) | (self.flow_id & 0xFFFF),
            self.payload_seed & 0xFFFFFFFF,
        ]


class SyncDcnHost:
    """Small reusable programming wrapper around the Sync-DCN AXI-Lite ABI.

    The wrapper deliberately stays thin: it performs only deterministic word
    packing and register sequencing.  Policy, schedule compilation, and device
    discovery remain outside this module.
    """

    def __init__(self, read32: Read32Fn, write32: Write32Fn):
        self._read32 = read32
        self._write32 = write32

    def read32(self, addr: int) -> int:
        """Read one 32-bit register or table word from the subsystem."""

        return self._read32(addr) & 0xFFFFFFFF

    def write32(self, addr: int, value: int) -> None:
        """Write one 32-bit register or table word to the subsystem."""

        self._write32(addr, value & 0xFFFFFFFF)

    def read64(self, addr_lo: int, addr_hi: int) -> int:
        """Read a little-endian 64-bit value exposed as two 32-bit registers."""

        lo = self.read32(addr_lo)
        hi = self.read32(addr_hi)
        return lo | (hi << 32)

    def write64(self, addr_lo: int, addr_hi: int, value: int) -> None:
        """Write a little-endian 64-bit value exposed as two 32-bit registers."""

        lo, hi = _split_u64(value)
        self.write32(addr_lo, lo)
        self.write32(addr_hi, hi)

    def set_admin_bank(self, bank: int) -> None:
        """Select which execution-table bank subsequent table writes target."""

        self.write32(SyncDcnRegister.ADMIN, bank & 0x1)

    def arm_bank_switch(self, bank: int, activate_time_ns: int) -> None:
        """Request a future hitless switch to the selected execution bank."""

        self.write64(
            SyncDcnRegister.ACTIVATE_TIME_LO,
            SyncDcnRegister.ACTIVATE_TIME_HI,
            activate_time_ns,
        )
        self.write32(SyncDcnRegister.ADMIN, (bank & 0x1) | 0x2)

    def enable_subsystem(self, enable: bool = True) -> None:
        """Enable or disable the top-level Sync-DCN subsystem."""

        self.write32(SyncDcnRegister.CTRL, 0x1 if enable else 0x0)

    def enable_ai_replay(self, enable: bool = True) -> None:
        """Enable or disable the synthetic AI replay engine."""

        self.write32(SyncDcnRegister.AI_ENABLE, 0x1 if enable else 0x0)

    def enable_consensus(self, enable: bool = True) -> None:
        """Enable or disable the consensus application engine."""

        self.write32(SyncDcnRegister.CONSENSUS_CTRL, 0x1 if enable else 0x0)

    def clear_consensus_halt(self) -> None:
        """Request the consensus core to leave halt state and reinitialize."""

        current_enable = self.read32(SyncDcnRegister.CONSENSUS_CTRL) & 0x1
        self.write32(SyncDcnRegister.CONSENSUS_CTRL, current_enable | 0x2)

    def write_tx_exec_entry(self, index: int, entry: ExecutionEntry) -> None:
        """Write one TX execution-table entry into the currently selected bank."""

        if index >= TX_EXEC_VISIBLE_ENTRY_COUNT:
            raise ValueError(
                f"TX execution entry index {index} exceeds visible table capacity "
                f"({TX_EXEC_VISIBLE_ENTRY_COUNT})"
            )
        self._write_table_words(TX_EXEC_TABLE_BASE, index, entry.encode_words())

    def write_tx_exec_entries(self, entries: Iterable[ExecutionEntry]) -> None:
        """Write a sequence of TX execution entries starting at entry index 0."""

        for index, entry in enumerate(entries):
            self.write_tx_exec_entry(index, entry)

    def write_rx_exec_entry(self, index: int, entry: ExecutionEntry) -> None:
        """Write one RX execution-table entry into the currently selected bank."""

        if index >= RX_EXEC_VISIBLE_ENTRY_COUNT:
            raise ValueError(
                f"RX execution entry index {index} exceeds visible table capacity "
                f"({RX_EXEC_VISIBLE_ENTRY_COUNT})"
            )
        self._write_table_words(RX_EXEC_TABLE_BASE, index, entry.encode_words())

    def write_rx_exec_entries(self, entries: Iterable[ExecutionEntry]) -> None:
        """Write a sequence of RX execution entries starting at entry index 0."""

        for index, entry in enumerate(entries):
            self.write_rx_exec_entry(index, entry)

    def write_exec_entry(self, index: int, entry: ExecutionEntry) -> None:
        """Backward-compatible alias for programming the TX execution table."""

        self.write_tx_exec_entry(index, entry)

    def write_exec_entries(self, entries: Iterable[ExecutionEntry]) -> None:
        """Backward-compatible alias for programming TX execution entries."""

        self.write_tx_exec_entries(entries)

    def write_ai_trace_entry(self, index: int, entry: AiTraceEntry) -> None:
        """Write one AI trace record into the local AI trace table."""

        if index >= AI_TRACE_VISIBLE_ENTRY_COUNT:
            raise ValueError(
                f"AI trace entry index {index} exceeds visible table capacity "
                f"({AI_TRACE_VISIBLE_ENTRY_COUNT})"
            )
        self._write_table_words(AI_TRACE_TABLE_BASE, index, entry.encode_words())

    def write_ai_trace_entries(self, entries: Iterable[AiTraceEntry]) -> None:
        """Write a sequence of AI trace records starting at entry index 0."""

        for index, entry in enumerate(entries):
            self.write_ai_trace_entry(index, entry)

    def read_exec_status(self) -> dict[str, int]:
        """Read the top-level executor/bank status in a decoded form."""

        raw = self.read32(SyncDcnRegister.EXEC_STATUS)
        return {
            "exec_enable": raw & 0x1,
            "active_bank": (raw >> 1) & 0x1,
            "pending_valid": (raw >> 2) & 0x1,
            "raw": raw,
        }

    def read_window_status(self) -> dict[str, int]:
        """Read the active execution-window status bits in a decoded form."""

        raw = self.read32(SyncDcnRegister.WINDOW_STATUS)
        return {
            "window_active": raw & 0x1,
            "tx_allowed": (raw >> 1) & 0x1,
            "rx_enabled": (raw >> 2) & 0x1,
            "exec_valid": (raw >> 3) & 0x1,
            "raw": raw,
        }

    def read_active_app_info(self) -> dict[str, int]:
        """Read the currently decoded plane/app/opcode triple."""

        raw = self.read32(SyncDcnRegister.ACTIVE_APP_INFO)
        return {
            "plane_id": (raw >> 24) & 0xFF,
            "app_id": (raw >> 16) & 0xFF,
            "opcode": (raw >> 8) & 0xFF,
            "raw": raw,
        }

    def read_active_context(self) -> int:
        """Read the current execution context id."""

        return self.read32(SyncDcnRegister.ACTIVE_CONTEXT) & 0xFFFF

    def read_current_entry_ptr(self) -> int:
        """Read the current local execution-table pointer."""

        return self.read32(SyncDcnRegister.CURRENT_ENTRY_PTR)

    def read_status_summary(self) -> dict[str, int]:
        """Collect the key bring-up status fields into one compact snapshot.

        This method intentionally returns the minimum fields needed to confirm
        that the subsystem is alive and that the intended compiled instruction
        is active:

        - active bank
        - whether a pending bank is still waiting
        - active app / opcode / plane
        - current context id
        - current entry pointer
        - basic window-open / tx / rx state
        """

        exec_status = self.read_exec_status()
        app_info = self.read_active_app_info()
        window_status = self.read_window_status()

        return {
            "active_bank": exec_status["active_bank"],
            "pending_valid": exec_status["pending_valid"],
            "exec_enable": exec_status["exec_enable"],
            "window_active": window_status["window_active"],
            "tx_allowed": window_status["tx_allowed"],
            "rx_enabled": window_status["rx_enabled"],
            "exec_valid": window_status["exec_valid"],
            "plane_id": app_info["plane_id"],
            "app_id": app_info["app_id"],
            "opcode": app_info["opcode"],
            "context_id": self.read_active_context(),
            "entry_ptr": self.read_current_entry_ptr(),
            "consensus_enable": self.read32(SyncDcnRegister.CONSENSUS_CTRL) & 0x1,
            "consensus_halt": self.read32(SyncDcnRegister.CONSENSUS_STATUS) & 0x1,
        }

    def read_consensus_status(self) -> dict[str, int]:
        """Read the consensus app's control and status registers."""

        ctrl = self.read32(SyncDcnRegister.CONSENSUS_CTRL)
        status = self.read32(SyncDcnRegister.CONSENSUS_STATUS)
        return {
            "enable": ctrl & 0x1,
            "system_halt": status & 0x1,
            "debug_state": (status >> 4) & 0xF,
            "ctrl_raw": ctrl,
            "status_raw": status,
        }

    def read_active_entry_summary(self) -> dict[str, int]:
        """Read the currently latched active execution entry registers.

        The subsystem mirrors the executor's active entry into dedicated status
        registers so software can correlate the live execution state with the
        compiled execution table that was previously programmed.
        """

        app_info = self.read_active_app_info()
        return {
            "entry_ptr": self.read_current_entry_ptr(),
            "start_time_ns": self.read64(
                SyncDcnRegister.ACTIVE_START_TIME_LO,
                SyncDcnRegister.ACTIVE_START_TIME_HI,
            ),
            "end_time_ns": self.read64(
                SyncDcnRegister.ACTIVE_END_TIME_LO,
                SyncDcnRegister.ACTIVE_END_TIME_HI,
            ),
            "plane_id": app_info["plane_id"],
            "app_id": app_info["app_id"],
            "opcode": app_info["opcode"],
            "context_id": self.read_active_context(),
            "target_raw": self.read32(SyncDcnRegister.ACTIVE_TARGET),
            "meta_raw": self.read32(SyncDcnRegister.ACTIVE_ENTRY_META),
        }

    def program_schedule_bank(
        self,
        bank: int,
        entries: Iterable[ExecutionEntry],
        activate_time_ns: int,
        enable_subsystem: bool = True,
    ) -> None:
        """Convenience flow for a complete schedule-bank update.

        This method mirrors the intended programming sequence:

        1. select the inactive bank
        2. write the local execution table image
        3. program the future activation time
        4. arm the bank switch
        5. optionally enable the subsystem
        """

        self.set_admin_bank(bank)
        self.write_exec_entries(entries)
        self.arm_bank_switch(bank, activate_time_ns)
        if enable_subsystem:
            self.enable_subsystem(True)

    def _write_table_words(self, base: int, index: int, words: Iterable[int]) -> None:
        """Write one table entry using the common 32-byte-per-entry ABI."""

        for word_index, word in enumerate(words):
            addr = base + index * ENTRY_STRIDE_BYTES + word_index * 4
            self.write32(addr, word)


__all__ = [
    "AI_TRACE_TABLE_BASE",
    "AI_TRACE_VISIBLE_ENTRY_COUNT",
    "RX_EXEC_VISIBLE_ENTRY_COUNT",
    "RX_EXEC_TABLE_BASE",
    "TX_EXEC_VISIBLE_ENTRY_COUNT",
    "TX_EXEC_TABLE_BASE",
    "AiTraceEntry",
    "ExecutionEntry",
    "SyncDcnAppId",
    "SyncDcnFlags",
    "SyncDcnHost",
    "SyncDcnOpcode",
    "SyncDcnPlaneId",
    "SyncDcnRegister",
]
