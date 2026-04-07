"""Focused cocotb verification suite for the active Sync-DCN subsystem.

This testbench exercises the current sign-off boundary for the reworked
compiled-window design: `dni_subsystem`.

The suite intentionally validates the system at multiple levels:

- register-programming and table-window ABI checks
- focused AI TX / AI RX smoke tests
- one full AI lifecycle from high-level JSON input through completion
- one full consensus round from high-level JSON input through commit

The tests are written against the same AXI-Lite ABI and JSON compilation flow
used by the host-side helper scripts so the cocotb suite doubles as an
executable specification of the current programming contract.
"""

import cocotb
import sys
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.append(str(REPO_ROOT))

from host.control_plane.sync_dcn_program import (
    build_ai_trace_entries,
    build_execution_entries,
    load_schedule_file,
    split_execution_entries_for_hw,
)


FLAG_VALID = 0x01
FLAG_TX_ENABLE = 0x02
FLAG_RX_ENABLE = 0x04
FLAG_COMPLETION_EVENT = 0x20

APP_CONSENSUS = 1
APP_AI_REPLAY = 2
PLANE_EPS = 0
PLANE_OCS = 1
OP_CONS_TX = 0x10
OP_CONS_RX = 0x11
OP_AI_RX = 0x21
OP_AI_TX = 0x20

TX_EXEC_TABLE_BASE = 0x1000
RX_EXEC_TABLE_BASE = 0x5800
AI_TRACE_TABLE_BASE = 0x9000


def ptp_tod_from_ns(ns: int) -> int:
    return ns << 16


async def reset_dut(dut):
    dut.s_axil_app_ctrl_awaddr.value = 0
    dut.s_axil_app_ctrl_awprot.value = 0
    dut.s_axil_app_ctrl_awvalid.value = 0
    dut.s_axil_app_ctrl_wdata.value = 0
    dut.s_axil_app_ctrl_wstrb.value = 0
    dut.s_axil_app_ctrl_wvalid.value = 0
    dut.s_axil_app_ctrl_bready.value = 0
    dut.s_axil_app_ctrl_araddr.value = 0
    dut.s_axil_app_ctrl_arprot.value = 0
    dut.s_axil_app_ctrl_arvalid.value = 0
    dut.s_axil_app_ctrl_rready.value = 0

    dut.ptp_sync_ts_tod.value = 0

    dut.s_axis_if_tx_tdata.value = 0
    dut.s_axis_if_tx_tkeep.value = 0
    dut.s_axis_if_tx_tvalid.value = 0
    dut.s_axis_if_tx_tlast.value = 0
    dut.s_axis_if_tx_tid.value = 0
    dut.s_axis_if_tx_tdest.value = 0
    dut.s_axis_if_tx_tuser.value = 0
    dut.m_axis_if_tx_tready.value = 1

    dut.s_axis_if_rx_tdata.value = 0
    dut.s_axis_if_rx_tkeep.value = 0
    dut.s_axis_if_rx_tvalid.value = 0
    dut.s_axis_if_rx_tlast.value = 0
    dut.s_axis_if_rx_tid.value = 0
    dut.s_axis_if_rx_tdest.value = 0
    dut.s_axis_if_rx_tuser.value = 0
    dut.m_axis_if_rx_tready.value = 1

    dut.rst.value = 1
    for _ in range(5):
        await RisingEdge(dut.clk)
    dut.rst.value = 0
    for _ in range(2):
        await RisingEdge(dut.clk)


async def axil_write(dut, addr, data):
    dut.s_axil_app_ctrl_awaddr.value = addr
    dut.s_axil_app_ctrl_awvalid.value = 1
    dut.s_axil_app_ctrl_wdata.value = data
    dut.s_axil_app_ctrl_wstrb.value = 0xF
    dut.s_axil_app_ctrl_wvalid.value = 1
    dut.s_axil_app_ctrl_bready.value = 1

    for _ in range(64):
        await RisingEdge(dut.clk)
        if dut.s_axil_app_ctrl_awready.value and dut.s_axil_app_ctrl_wready.value:
            break
    else:
        raise AssertionError(f"Timed out waiting for AXI-Lite write address/data handshake at 0x{addr:04x}")

    dut.s_axil_app_ctrl_awvalid.value = 0
    dut.s_axil_app_ctrl_wvalid.value = 0

    for _ in range(64):
        await RisingEdge(dut.clk)
        if dut.s_axil_app_ctrl_bvalid.value:
            break
    else:
        raise AssertionError(f"Timed out waiting for AXI-Lite write response at 0x{addr:04x}")

    dut.s_axil_app_ctrl_bready.value = 0
    await RisingEdge(dut.clk)


async def axil_read(dut, addr):
    dut.s_axil_app_ctrl_araddr.value = addr
    dut.s_axil_app_ctrl_arvalid.value = 1
    dut.s_axil_app_ctrl_rready.value = 1

    for _ in range(64):
        await RisingEdge(dut.clk)
        if dut.s_axil_app_ctrl_arready.value:
            break
    else:
        raise AssertionError(f"Timed out waiting for AXI-Lite read address handshake at 0x{addr:04x}")

    dut.s_axil_app_ctrl_arvalid.value = 0

    for _ in range(64):
        await RisingEdge(dut.clk)
        if dut.s_axil_app_ctrl_rvalid.value:
            data = dut.s_axil_app_ctrl_rdata.value.to_unsigned()
            break
    else:
        raise AssertionError(f"Timed out waiting for AXI-Lite read data at 0x{addr:04x}")

    dut.s_axil_app_ctrl_rready.value = 0
    await RisingEdge(dut.clk)
    return data


def encode_exec_entry_words(start_ns, end_ns, context_id, opcode, plane_id, app_id, target_port, queue_id, flags):
    return [
        start_ns & 0xFFFFFFFF,
        (start_ns >> 32) & 0xFFFFFFFF,
        end_ns & 0xFFFFFFFF,
        (end_ns >> 32) & 0xFFFFFFFF,
        ((context_id & 0xFFFF) << 16) | ((opcode & 0xFF) << 8) | ((plane_id & 0xF) << 4) | (app_id & 0xF),
        ((queue_id & 0xFFFF) << 16) | ((target_port & 0xFF) << 8) | (flags & 0xFF),
        0,
        0,
    ]


async def write_exec_entry_to_base(dut, base_addr, entry, words):
    for word_index, word_value in enumerate(words):
        await axil_write(dut, base_addr + entry * 32 + word_index * 4, word_value)


async def write_exec_entry(dut, entry, start_ns, end_ns, context_id, opcode, plane_id, app_id, target_port, queue_id, flags):
    words = encode_exec_entry_words(
        start_ns, end_ns, context_id, opcode, plane_id, app_id, target_port, queue_id, flags
    )
    tx_written = False
    rx_written = False

    if flags & FLAG_TX_ENABLE or opcode in (OP_CONS_TX, OP_AI_TX):
        await write_exec_entry_to_base(dut, TX_EXEC_TABLE_BASE, entry, list(words))
        tx_written = True

    if flags & FLAG_RX_ENABLE or opcode in (OP_CONS_RX, OP_AI_RX):
        rx_words = list(words)
        rx_opcode = OP_CONS_RX if opcode == OP_CONS_TX else opcode
        rx_words[4] = ((context_id & 0xFFFF) << 16) | ((rx_opcode & 0xFF) << 8) | ((plane_id & 0xF) << 4) | (app_id & 0xF)
        await write_exec_entry_to_base(dut, RX_EXEC_TABLE_BASE, entry, rx_words)
        rx_written = True

    if not tx_written and not rx_written:
        raise AssertionError(f"Execution entry {entry} was not routed to TX or RX table")


async def write_ai_trace_entry(dut, entry, packet_count, packet_len, gap, dst_mac_lo, ethertype_mac_hi, flow_word, payload_seed):
    words = [
        ((packet_count & 0xFFFF) << 16) | (packet_len & 0xFFFF),
        gap,
        dst_mac_lo,
        ethertype_mac_hi,
        flow_word,
        payload_seed,
    ]

    for word_index, word_value in enumerate(words):
        await axil_write(dut, AI_TRACE_TABLE_BASE + entry * 32 + word_index * 4, word_value)


async def write_encoded_words(dut, base_addr, entry, words):
    """Write one pre-encoded table entry into either hardware table window."""
    for word_index, word_value in enumerate(words):
        await axil_write(dut, base_addr + entry * 32 + word_index * 4, word_value)


async def advance_ptp_to_ns(dut, time_ns, settle_cycles=4):
    """Drive the synthetic PTP time source to a target value and let the RTL settle."""
    dut.ptp_sync_ts_tod.value = ptp_tod_from_ns(time_ns)
    for _ in range(settle_cycles):
        await RisingEdge(dut.clk)


def build_single_beat_frame(frame_bytes, frame_len=64):
    """Pack a short Ethernet frame into the little-endian AXIS layout used by the DUT."""
    assert len(frame_bytes) <= frame_len
    payload = bytearray(frame_len)
    payload[:len(frame_bytes)] = frame_bytes
    return int.from_bytes(payload, byteorder="little")


def unpack_single_beat_frame(tdata, frame_len=64):
    """Expand one little-endian AXIS beat back into byte-addressable frame data."""
    return tdata.to_bytes(frame_len, byteorder="little")


def build_consensus_single_beat_frame(window_id, node_id, knowledge_vec, payload_bytes, frame_len=64):
    """Build one synthetic consensus frame that matches the current protocol format."""
    assert len(payload_bytes) == 40

    frame_bytes = bytearray(frame_len)
    frame_bytes[0:6] = bytes.fromhex("000A35065094")
    frame_bytes[6:12] = bytes.fromhex("020000000011")
    frame_bytes[12] = 0x88
    frame_bytes[13] = 0xB5
    frame_bytes[14:22] = int(window_id).to_bytes(8, byteorder="big")
    frame_bytes[22] = node_id & 0xFF
    frame_bytes[23] = knowledge_vec & 0xFF
    frame_bytes[24:64] = payload_bytes
    return build_single_beat_frame(frame_bytes, frame_len=frame_len)


async def inject_rx_frame(dut, tdata, frame_len=64, tuser=0):
    """Inject one single-beat frame into the MAC-facing RX interface."""
    dut.s_axis_if_rx_tdata.value = tdata
    dut.s_axis_if_rx_tkeep.value = (1 << frame_len) - 1
    dut.s_axis_if_rx_tvalid.value = 1
    dut.s_axis_if_rx_tlast.value = 1
    dut.s_axis_if_rx_tuser.value = tuser

    await RisingEdge(dut.clk)
    assert int(dut.s_axis_if_rx_tready.value) == 1, "The subsystem did not accept the injected RX frame"

    dut.s_axis_if_rx_tvalid.value = 0
    dut.s_axis_if_rx_tlast.value = 0
    dut.s_axis_if_rx_tuser.value = 0
    dut.s_axis_if_rx_tdata.value = 0
    dut.s_axis_if_rx_tkeep.value = 0
    await RisingEdge(dut.clk)


async def assert_no_tx_for_cycles(dut, cycles):
    """Confirm that no MAC TX beat is emitted during a short observation window."""
    for _ in range(cycles):
        await RisingEdge(dut.clk)
        assert not (
            dut.m_axis_if_tx_tvalid.value and dut.m_axis_if_tx_tready.value
        ), "Unexpected TX traffic was emitted during a no-TX observation window"


async def wait_for_active_instruction(dut, expected_app_id, expected_opcode, max_cycles=64):
    """Poll the mirrored status registers until a specific instruction becomes active."""
    last_active_app_info = 0
    last_window_status = 0
    last_exec_status = 0
    last_entry_ptr = 0

    for _ in range(max_cycles):
        last_active_app_info = await axil_read(dut, 0x048)
        last_window_status = await axil_read(dut, 0x034)
        last_exec_status = await axil_read(dut, 0x014)
        last_entry_ptr = await axil_read(dut, 0x038)

        if (
            ((last_active_app_info >> 16) & 0xFF) == expected_app_id
            and ((last_active_app_info >> 8) & 0xFF) == expected_opcode
            and (last_window_status & 0x1)
        ):
            return last_active_app_info, last_window_status

    raise AssertionError(
        f"Timed out waiting for active instruction app=0x{expected_app_id:02X}, "
        f"opcode=0x{expected_opcode:02X}; "
        f"last active_app_info=0x{last_active_app_info:08X}, "
        f"window_status=0x{last_window_status:08X}, "
        f"exec_status=0x{last_exec_status:08X}, "
        f"entry_ptr={last_entry_ptr}"
    )


async def wait_for_consensus_commit(dut, expected_mask, max_cycles=64):
    """Wait until the internal consensus core reports the requested commit mask."""
    core = dut.processor_runtime_inst.consensus_node_inst.consensus_core_inst

    for _ in range(max_cycles):
        await RisingEdge(dut.clk)
        if (int(core.o_commit_valid.value) & expected_mask) == expected_mask:
            return

    raise AssertionError(
        f"Timed out waiting for consensus commit mask 0x{expected_mask:X}; "
        f"last mask was 0x{int(core.o_commit_valid.value):X}"
    )


async def wait_for_consensus_halt_state(dut, expected_halt, max_cycles=64):
    """Poll the exposed consensus status register until halt matches expectation."""
    for _ in range(max_cycles):
        status = await axil_read(dut, 0x08C)
        if (status & 0x1) == expected_halt:
            return status

    raise AssertionError(
        f"Timed out waiting for consensus halt state {expected_halt}; "
        f"last status was 0x{status:08X}"
    )


async def program_consensus_round_entry(dut, admin_bank, activate_time_ns, start_ns, end_ns):
    """Program one full consensus round as one compiled execution window."""
    await axil_write(dut, 0x024, admin_bank & 0x1)
    await write_exec_entry(
        dut,
        entry=0,
        start_ns=start_ns,
        end_ns=end_ns,
        context_id=0,
        opcode=OP_CONS_TX,
        plane_id=PLANE_EPS,
        app_id=APP_CONSENSUS,
        target_port=0,
        queue_id=0,
        flags=FLAG_VALID | FLAG_TX_ENABLE | FLAG_RX_ENABLE | FLAG_COMPLETION_EVENT,
    )
    await axil_write(dut, 0x01C, activate_time_ns & 0xFFFFFFFF)
    await axil_write(dut, 0x020, (activate_time_ns >> 32) & 0xFFFFFFFF)
    await axil_write(dut, 0x024, (admin_bank & 0x1) | 0x2)


async def program_schedule_from_spec_file(dut, spec_path):
    """Compile a high-level JSON/YAML schedule and program it through AXI-Lite."""
    raw_spec = load_schedule_file(spec_path)
    raw_tx_entries, raw_rx_entries = split_execution_entries_for_hw(raw_spec.get("execution_entries", []))
    tx_execution_entries = build_execution_entries(raw_tx_entries)
    rx_execution_entries = build_execution_entries(raw_rx_entries)
    ai_trace_entries = build_ai_trace_entries(raw_spec.get("ai_trace_entries", []))
    admin_bank = int(raw_spec.get("admin_bank", 1))
    activate_time_ns = int(raw_spec.get("activate_time_ns", 0))
    enable_ai_replay = bool(raw_spec.get("enable_ai_replay", False))
    enable_consensus = bool(raw_spec.get("enable_consensus", False))
    enable_subsystem = bool(raw_spec.get("enable_subsystem", True))

    await axil_write(dut, 0x024, admin_bank & 0x1)

    for index, entry in enumerate(tx_execution_entries):
        await write_encoded_words(dut, TX_EXEC_TABLE_BASE, index, entry.encode_words())

    for index, entry in enumerate(rx_execution_entries):
        await write_encoded_words(dut, RX_EXEC_TABLE_BASE, index, entry.encode_words())

    for index, entry in enumerate(ai_trace_entries):
        await write_encoded_words(dut, AI_TRACE_TABLE_BASE, index, entry.encode_words())

    if enable_ai_replay:
        await axil_write(dut, 0x054, 0x1)

    if enable_consensus:
        await axil_write(dut, 0x07C, 0x1)

    await axil_write(dut, 0x01C, activate_time_ns & 0xFFFFFFFF)
    await axil_write(dut, 0x020, (activate_time_ns >> 32) & 0xFFFFFFFF)
    await axil_write(dut, 0x024, (admin_bank & 0x1) | 0x2)

    if enable_subsystem:
        await axil_write(dut, 0x008, 0x1)

    return raw_spec

async def wait_for_tx_frame(dut, max_cycles=64):
    """Wait until one MAC TX beat is emitted and return the observed beat."""
    for _ in range(max_cycles):
        await RisingEdge(dut.clk)
        if dut.m_axis_if_tx_tvalid.value and dut.m_axis_if_tx_tready.value:
            return {
                "tdata": dut.m_axis_if_tx_tdata.value.to_unsigned(),
                "tkeep": dut.m_axis_if_tx_tkeep.value.to_unsigned(),
                "tdest": dut.m_axis_if_tx_tdest.value.to_unsigned(),
                "tlast": int(dut.m_axis_if_tx_tlast.value),
                "tuser": int(dut.m_axis_if_tx_tuser.value),
            }

    raise AssertionError("Timed out waiting for an application TX frame")


@cocotb.test()
async def test_subsystem_register_programming_path(dut):
    cocotb.start_soon(Clock(dut.clk, 4, unit="ns").start())
    await reset_dut(dut)

    # Program inactive bank 1 with one consensus TX execution entry.
    await axil_write(dut, 0x024, 0x1)
    await write_exec_entry(
        dut,
        entry=0,
        start_ns=100,
        end_ns=150,
        context_id=0x33,
        opcode=OP_CONS_TX,
        plane_id=PLANE_EPS,
        app_id=APP_CONSENSUS,
        target_port=2,
        queue_id=9,
        flags=FLAG_VALID | FLAG_TX_ENABLE | FLAG_COMPLETION_EVENT,
    )

    # Program one AI trace record and verify readback path.
    await write_ai_trace_entry(
        dut,
        entry=0,
        packet_count=4,
        packet_len=128,
        gap=3,
        dst_mac_lo=0xAABBCCDD,
        ethertype_mac_hi=0x88B60011,
        flow_word=0x00010002,
        payload_seed=0xDEADBEEF,
    )

    trace_word0 = await axil_read(dut, AI_TRACE_TABLE_BASE)
    trace_word5 = await axil_read(dut, AI_TRACE_TABLE_BASE + 5 * 4)
    assert trace_word0 == ((4 << 16) | 128)
    assert trace_word5 == 0xDEADBEEF

    # Arm bank 1 for immediate activation and enable the subsystem.
    await axil_write(dut, 0x01C, 0)
    await axil_write(dut, 0x020, 0)
    await axil_write(dut, 0x024, 0x3)
    await axil_write(dut, 0x008, 0x1)

    # Check table readback path before time advances.
    entry_meta = await axil_read(dut, TX_EXEC_TABLE_BASE + 4 * 4)
    assert entry_meta == ((0x33 << 16) | (OP_CONS_TX << 8) | (APP_CONSENSUS & 0xF))

    # Move PHC time into the execution window.
    dut.ptp_sync_ts_tod.value = ptp_tod_from_ns(100)
    for _ in range(4):
        await RisingEdge(dut.clk)

    window_status = await axil_read(dut, 0x034)
    active_app_info = await axil_read(dut, 0x048)
    active_context = await axil_read(dut, 0x04C)
    active_target = await axil_read(dut, 0x030)

    assert window_status & 0x1
    assert window_status & 0x2
    assert ((active_app_info >> 16) & 0xFF) == APP_CONSENSUS
    assert ((active_app_info >> 8) & 0xFF) == OP_CONS_TX
    assert (active_target & 0xFF) == 2
    assert ((active_target >> 8) & 0xFFFF) == 9
    assert (active_context & 0xFFFF) == 0x33


@cocotb.test()
async def test_bank_flip_activates_ai_replay_path(dut):
    cocotb.start_soon(Clock(dut.clk, 4, unit="ns").start())
    await reset_dut(dut)
    dut.m_axis_if_tx_tready.value = 0

    # Keep bank 0 empty and program bank 1 with one AI TX execution entry.
    # The test therefore exercises the intended hitless model where software
    # prepares a future bank offline and then requests an activation flip.
    await axil_write(dut, 0x024, 0x1)
    await write_exec_entry(
        dut,
        entry=0,
        start_ns=50,
        end_ns=120,
        context_id=0x1,
        opcode=OP_AI_TX,
        plane_id=PLANE_OCS,
        app_id=APP_AI_REPLAY,
        target_port=0,
        queue_id=0,
        flags=FLAG_VALID | FLAG_TX_ENABLE,
    )

    # Trace entry 1 emits two synthetic packets.  The replay engine samples
    # this context when the execution window opens and then keeps using it
    # until the burst is complete.
    await write_ai_trace_entry(
        dut,
        entry=1,
        packet_count=2,
        packet_len=64,
        gap=1,
        dst_mac_lo=0xAABBCCDD,
        ethertype_mac_hi=0x88B61234,
        flow_word=0x00020007,
        payload_seed=0xCAFEBABE,
    )

    await axil_write(dut, 0x054, 0x1)
    # Use a future activation time so the test can explicitly observe the
    # pending-bank state before the executor is allowed to flip.
    await axil_write(dut, 0x01C, 40)
    await axil_write(dut, 0x020, 0)
    await axil_write(dut, 0x024, 0x3)
    await axil_write(dut, 0x008, 0x1)

    exec_status_before = await axil_read(dut, 0x014)
    assert ((exec_status_before >> 1) & 0x1) == 0
    assert ((exec_status_before >> 2) & 0x1) == 1

    # Jump directly into the scheduled window.  The executor should first flip
    # to bank 1 once the PTP time crosses the pending activation time and then
    # decode the AI execution entry as the active local instruction.
    dut.ptp_sync_ts_tod.value = ptp_tod_from_ns(50)
    for _ in range(6):
        await RisingEdge(dut.clk)

    exec_status_after = await axil_read(dut, 0x014)
    active_app_info = await axil_read(dut, 0x048)
    active_context = await axil_read(dut, 0x04C)
    active_target = await axil_read(dut, 0x030)

    assert ((exec_status_after >> 1) & 0x1) == 1
    assert ((exec_status_after >> 2) & 0x1) == 0
    assert ((active_app_info >> 24) & 0xFF) == PLANE_OCS
    assert ((active_app_info >> 16) & 0xFF) == APP_AI_REPLAY
    assert ((active_app_info >> 8) & 0xFF) == OP_AI_TX
    assert (active_context & 0xFFFF) == 0x1
    assert (active_target & 0xFF) == 0

    # Release the MAC TX interface only after the status checks so the
    # single-beat replay frames remain visible to the test instead of being
    # consumed while AXI-Lite reads are still in flight.
    dut.m_axis_if_tx_tready.value = 1
    first_frame = await wait_for_tx_frame(dut)
    second_frame = await wait_for_tx_frame(dut)

    # OCS traffic should carry plane metadata in the upper tdest bits while
    # preserving the physical egress-port selection in bit 0.
    assert first_frame["tdest"] == 0x3
    assert second_frame["tdest"] == 0x3
    assert first_frame["tlast"] == 1
    assert second_frame["tlast"] == 1
    assert first_frame["tkeep"] == (1 << 64) - 1
    assert second_frame["tkeep"] == (1 << 64) - 1

    # Validate the fixed Ethernet header produced by the synthetic AI engine.
    assert ((first_frame["tdata"] >> (0 * 8)) & 0xFF) == 0x12
    assert ((first_frame["tdata"] >> (1 * 8)) & 0xFF) == 0x34
    assert ((first_frame["tdata"] >> (2 * 8)) & 0xFF) == 0xAA
    assert ((first_frame["tdata"] >> (3 * 8)) & 0xFF) == 0xBB
    assert ((first_frame["tdata"] >> (4 * 8)) & 0xFF) == 0xCC
    assert ((first_frame["tdata"] >> (5 * 8)) & 0xFF) == 0xDD
    assert ((first_frame["tdata"] >> (12 * 8)) & 0xFFFF) == 0x88B6

    pkt_sent_count = await axil_read(dut, 0x058)
    assert pkt_sent_count == 2


@cocotb.test()
async def test_ai_rx_accepts_matching_frame(dut):
    cocotb.start_soon(Clock(dut.clk, 4, unit="ns").start())
    await reset_dut(dut)

    await axil_write(dut, 0x024, 0x1)
    await write_exec_entry(
        dut,
        entry=0,
        start_ns=50,
        end_ns=120,
        context_id=0x1,
        opcode=OP_AI_RX,
        plane_id=PLANE_OCS,
        app_id=APP_AI_REPLAY,
        target_port=0,
        queue_id=0,
        flags=FLAG_VALID | FLAG_RX_ENABLE,
    )
    await write_ai_trace_entry(
        dut,
        entry=1,
        packet_count=0,
        packet_len=64,
        gap=0,
        dst_mac_lo=0xAABBCCDD,
        ethertype_mac_hi=0x88B61234,
        flow_word=0x00020007,
        payload_seed=0x10203040,
    )

    await axil_write(dut, 0x054, 0x1)
    await axil_write(dut, 0x01C, 40)
    await axil_write(dut, 0x020, 0)
    await axil_write(dut, 0x024, 0x3)
    await axil_write(dut, 0x008, 0x1)

    await advance_ptp_to_ns(dut, 50, settle_cycles=6)

    active_app_info = await axil_read(dut, 0x048)
    window_status = await axil_read(dut, 0x034)
    assert ((active_app_info >> 16) & 0xFF) == APP_AI_REPLAY
    assert ((active_app_info >> 8) & 0xFF) == OP_AI_RX
    assert window_status & 0x1
    assert window_status & 0x4

    frame_bytes = bytearray(64)
    frame_bytes[0:6] = bytes.fromhex("1234AABBCCDD")
    frame_bytes[6:12] = bytes.fromhex("020000000001")
    frame_bytes[12] = 0x88
    frame_bytes[13] = 0xB6
    frame_bytes[24] = 0x07
    frame_bytes[25] = 0x00
    frame_bytes[26] = 0x02
    frame_bytes[27] = 0x00
    await inject_rx_frame(dut, build_single_beat_frame(frame_bytes))

    ai_rx_pkt_count = await axil_read(dut, 0x006C)
    ai_rx_byte_count = await axil_read(dut, 0x0070)
    ai_rx_match_count = await axil_read(dut, 0x0074)
    ai_rx_drop_count = await axil_read(dut, 0x0078)

    assert ai_rx_pkt_count == 1
    assert ai_rx_byte_count == 64
    assert ai_rx_match_count == 1
    assert ai_rx_drop_count == 0


@cocotb.test()
async def test_ai_rx_drops_outside_window(dut):
    cocotb.start_soon(Clock(dut.clk, 4, unit="ns").start())
    await reset_dut(dut)

    await axil_write(dut, 0x024, 0x1)
    await write_exec_entry(
        dut,
        entry=0,
        start_ns=50,
        end_ns=120,
        context_id=0x1,
        opcode=OP_AI_RX,
        plane_id=PLANE_OCS,
        app_id=APP_AI_REPLAY,
        target_port=0,
        queue_id=0,
        flags=FLAG_VALID | FLAG_RX_ENABLE,
    )
    await write_ai_trace_entry(
        dut,
        entry=1,
        packet_count=0,
        packet_len=64,
        gap=0,
        dst_mac_lo=0xAABBCCDD,
        ethertype_mac_hi=0x88B61234,
        flow_word=0x00020007,
        payload_seed=0x55667788,
    )

    await axil_write(dut, 0x054, 0x1)
    await axil_write(dut, 0x01C, 0)
    await axil_write(dut, 0x020, 0)
    await axil_write(dut, 0x024, 0x3)
    await axil_write(dut, 0x008, 0x1)

    await advance_ptp_to_ns(dut, 20, settle_cycles=6)

    frame_bytes = bytearray(64)
    frame_bytes[0:6] = bytes.fromhex("1234AABBCCDD")
    frame_bytes[6:12] = bytes.fromhex("020000000001")
    frame_bytes[12] = 0x88
    frame_bytes[13] = 0xB6
    frame_bytes[24] = 0x07
    frame_bytes[25] = 0x00
    frame_bytes[26] = 0x02
    frame_bytes[27] = 0x00
    await inject_rx_frame(dut, build_single_beat_frame(frame_bytes))

    ai_rx_pkt_count = await axil_read(dut, 0x006C)
    ai_rx_byte_count = await axil_read(dut, 0x0070)
    ai_rx_match_count = await axil_read(dut, 0x0074)
    ai_rx_drop_count = await axil_read(dut, 0x0078)

    assert ai_rx_pkt_count == 0
    assert ai_rx_byte_count == 0
    assert ai_rx_match_count == 0
    assert ai_rx_drop_count == 1


@cocotb.test()
async def test_compiled_program_lifecycle_end_to_end(dut):
    """Run a full local-program lifecycle from high-level input spec to completion."""
    cocotb.start_soon(Clock(dut.clk, 4, unit="ns").start())
    await reset_dut(dut)
    dut.m_axis_if_tx_tready.value = 1

    spec_path = Path(__file__).resolve().parent / "fixtures" / "comprehensive_ai_lifecycle.json"
    spec = await program_schedule_from_spec_file(dut, spec_path)

    exec_status_before = await axil_read(dut, 0x014)
    assert ((exec_status_before >> 1) & 0x1) == 0
    assert ((exec_status_before >> 2) & 0x1) == 1

    # 1. Enter the compiled AI_RX window and inject the matching application
    # frame that the offline schedule associated with context 1.
    await advance_ptp_to_ns(dut, 50, settle_cycles=6)
    active_app_info, _ = await wait_for_active_instruction(dut, APP_AI_REPLAY, OP_AI_RX)

    frame_bytes = bytearray(64)
    frame_bytes[0:6] = bytes.fromhex("1234AABBCCDD")
    frame_bytes[6:12] = bytes.fromhex("020000000001")
    frame_bytes[12] = 0x88
    frame_bytes[13] = 0xB6
    frame_bytes[24] = 0x07
    frame_bytes[25] = 0x00
    frame_bytes[26] = 0x02
    frame_bytes[27] = 0x00
    await inject_rx_frame(dut, build_single_beat_frame(frame_bytes))

    assert await axil_read(dut, 0x006C) == 1
    assert await axil_read(dut, 0x0070) == 64
    assert await axil_read(dut, 0x0074) == 1
    assert await axil_read(dut, 0x0078) == 0

    # 2. Move into the compiled guard window.  The program should advance to a
    # no-transmit instruction, and the MAC TX path must stay quiet.
    await advance_ptp_to_ns(dut, 125, settle_cycles=6)
    active_app_info, _ = await wait_for_active_instruction(dut, 0, 0x01)
    assert ((active_app_info >> 16) & 0xFF) == 0
    assert ((active_app_info >> 8) & 0xFF) == 0x01
    await assert_no_tx_for_cycles(dut, 6)

    # 3. Move into the compiled AI_TX window associated with context 2 and
    # verify that the synthetic replay engine emits the programmed burst.
    dut.m_axis_if_tx_tready.value = 0
    await advance_ptp_to_ns(dut, 150, settle_cycles=6)
    active_app_info, _ = await wait_for_active_instruction(dut, APP_AI_REPLAY, OP_AI_TX)

    dut.m_axis_if_tx_tready.value = 1
    first_frame = await wait_for_tx_frame(dut)
    second_frame = await wait_for_tx_frame(dut)

    assert first_frame["tdest"] == 0x3
    assert second_frame["tdest"] == 0x3
    assert first_frame["tlast"] == 1
    assert second_frame["tlast"] == 1
    assert ((first_frame["tdata"] >> (12 * 8)) & 0xFFFF) == 0x88B6
    assert await axil_read(dut, 0x058) == 2

    # 4. Jump beyond the final window and confirm that the executor retires
    # the compiled program and leaves the datapath idle.
    await advance_ptp_to_ns(dut, 260, settle_cycles=8)
    window_status = await axil_read(dut, 0x034)
    current_entry_ptr = await axil_read(dut, 0x038)
    exec_status_after = await axil_read(dut, 0x014)

    assert (window_status & 0x1) == 0
    assert current_entry_ptr == len(spec["execution_entries"])
    assert ((exec_status_after >> 1) & 0x1) == 1
    assert ((exec_status_after >> 2) & 0x1) == 0


@cocotb.test()
async def test_compiled_consensus_round_end_to_end(dut):
    """Run one full compiled consensus round from JSON input through commit."""
    cocotb.start_soon(Clock(dut.clk, 4, unit="ns").start())
    await reset_dut(dut)
    dut.m_axis_if_tx_tready.value = 0

    spec_path = Path(__file__).resolve().parent / "fixtures" / "comprehensive_consensus_round.json"
    spec = await program_schedule_from_spec_file(dut, spec_path)

    exec_status_before = await axil_read(dut, 0x014)
    assert ((exec_status_before >> 1) & 0x1) == 0
    assert ((exec_status_before >> 2) & 0x1) == 1

    # Enter the compiled consensus round window.  In the current RTL, one
    # logical consensus round must use one execution window so the protocol's
    # round identifier stays constant across both TX and RX activity.
    await advance_ptp_to_ns(dut, 50, settle_cycles=6)
    active_app_info, window_status = await wait_for_active_instruction(dut, APP_CONSENSUS, OP_CONS_TX)
    assert ((active_app_info >> 16) & 0xFF) == APP_CONSENSUS
    assert ((active_app_info >> 8) & 0xFF) == OP_CONS_TX
    assert window_status & 0x2
    assert window_status & 0x4

    # Release TX only after the status check so the local replica's broadcast
    # packets remain available for waveform inspection and explicit assertions.
    dut.m_axis_if_tx_tready.value = 1
    first_frame = await wait_for_tx_frame(dut)
    second_frame = await wait_for_tx_frame(dut)

    first_bytes = unpack_single_beat_frame(first_frame["tdata"])
    second_bytes = unpack_single_beat_frame(second_frame["tdata"])

    assert first_frame["tlast"] == 1
    assert second_frame["tlast"] == 1
    assert first_bytes[12:14] == bytes.fromhex("88B5")
    assert second_bytes[12:14] == bytes.fromhex("88B5")
    assert first_bytes[14:22] == (1).to_bytes(8, byteorder="big")
    assert second_bytes[14:22] == (1).to_bytes(8, byteorder="big")
    assert first_bytes[22] == 0
    assert second_bytes[22] == 0
    assert first_bytes[23] == 0x07
    assert second_bytes[23] == 0x07
    assert first_bytes[0:6] == bytes.fromhex("000A35060924")
    assert second_bytes[0:6] == bytes.fromhex("000A35060B84")

    # Inject one matching round message from each of the other two replicas.
    await inject_rx_frame(
        dut,
        build_consensus_single_beat_frame(
            window_id=1,
            node_id=1,
            knowledge_vec=0x07,
            payload_bytes=bytes([0x01] * 40),
        ),
    )
    await inject_rx_frame(
        dut,
        build_consensus_single_beat_frame(
            window_id=1,
            node_id=2,
            knowledge_vec=0x07,
            payload_bytes=bytes([0x02] * 40),
        ),
    )

    # Retire the round window.  The completion_event flag on the compiled
    # instruction generates the commit pulse that advances the core from
    # collect -> fail_detect -> commit.
    await advance_ptp_to_ns(dut, 220, settle_cycles=8)
    await wait_for_consensus_commit(dut, expected_mask=0x6)

    consensus_status = await axil_read(dut, 0x08C)
    current_entry_ptr = await axil_read(dut, 0x038)
    window_status = await axil_read(dut, 0x034)

    assert (consensus_status & 0x1) == 0
    assert (window_status & 0x1) == 0
    assert current_entry_ptr == len(spec["execution_entries"])

    core = dut.processor_runtime_inst.consensus_node_inst.consensus_core_inst
    commit_valid_mask = int(core.o_commit_valid.value)
    commit_log_flat = int(core.o_commit_log.value)
    node1_commit = (commit_log_flat >> (1 * 40 * 8)) & ((1 << (40 * 8)) - 1)
    node2_commit = (commit_log_flat >> (2 * 40 * 8)) & ((1 << (40 * 8)) - 1)

    assert (commit_valid_mask & 0x6) == 0x6
    assert node1_commit == int.from_bytes(bytes([0x01] * 40), byteorder="little")
    assert node2_commit == int.from_bytes(bytes([0x02] * 40), byteorder="little")


@cocotb.test()
async def test_consensus_quorum_fail_then_clear_halt_and_recover(dut):
    """Trigger halt with insufficient quorum, then clear halt and complete a later round."""
    cocotb.start_soon(Clock(dut.clk, 4, unit="ns").start())
    await reset_dut(dut)
    dut.m_axis_if_tx_tready.value = 0

    spec_path = Path(__file__).resolve().parent / "fixtures" / "comprehensive_consensus_round.json"
    spec = await program_schedule_from_spec_file(dut, spec_path)

    await advance_ptp_to_ns(dut, 50, settle_cycles=6)
    await wait_for_active_instruction(dut, APP_CONSENSUS, OP_CONS_TX)

    # Allow the local broadcast to drain so the round really executes, but do
    # not inject any remote peer replies.  The current core counts the local
    # replica in r_rx_mask, so with three replicas and quorum=2 a single remote
    # reply would still satisfy quorum.  Keeping both remote replies absent
    # exercises the true "insufficient quorum" halt path.
    dut.m_axis_if_tx_tready.value = 1
    await wait_for_tx_frame(dut)
    await wait_for_tx_frame(dut)

    await advance_ptp_to_ns(dut, 220, settle_cycles=8)
    await wait_for_consensus_halt_state(dut, expected_halt=1)

    core = dut.processor_runtime_inst.consensus_node_inst.consensus_core_inst
    assert int(core.o_commit_valid.value) == 0

    # Clear halt through the exposed control ABI, then prepare the next bank
    # with another compiled consensus round in the future.
    await axil_write(dut, 0x07C, 0x3)
    await wait_for_consensus_halt_state(dut, expected_halt=0)

    await program_consensus_round_entry(
        dut,
        admin_bank=0,
        activate_time_ns=290,
        start_ns=300,
        end_ns=430,
    )

    dut.m_axis_if_tx_tready.value = 0
    await advance_ptp_to_ns(dut, 300, settle_cycles=6)
    await wait_for_active_instruction(dut, APP_CONSENSUS, OP_CONS_TX)

    dut.m_axis_if_tx_tready.value = 1
    await wait_for_tx_frame(dut)
    await wait_for_tx_frame(dut)

    await inject_rx_frame(
        dut,
        build_consensus_single_beat_frame(
            window_id=2,
            node_id=1,
            knowledge_vec=0x07,
            payload_bytes=bytes([0x01] * 40),
        ),
    )
    await inject_rx_frame(
        dut,
        build_consensus_single_beat_frame(
            window_id=2,
            node_id=2,
            knowledge_vec=0x07,
            payload_bytes=bytes([0x02] * 40),
        ),
    )

    await advance_ptp_to_ns(dut, 460, settle_cycles=8)
    await wait_for_consensus_commit(dut, expected_mask=0x6)
    await wait_for_consensus_halt_state(dut, expected_halt=0)

    current_entry_ptr = await axil_read(dut, 0x038)
    assert current_entry_ptr == len(spec["execution_entries"])
