import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge


FLAG_VALID = 0x01
FLAG_TX_ENABLE = 0x02
FLAG_RX_ENABLE = 0x04
FLAG_COMPLETION_EVENT = 0x20


def bit(sig):
    return int(sig.value)


async def reset_dut(dut):
    dut.i_enable.value = 0
    dut.i_ptp_time_ns.value = 0
    dut.cfg_exec_enable.value = 0
    dut.cfg_set_pending_valid.value = 0
    dut.cfg_set_pending_bank.value = 0
    dut.cfg_set_pending_time_ns.value = 0

    for prefix in ("tx", "rx"):
        getattr(dut, f"i_{prefix}_word_start_lo").value = 0
        getattr(dut, f"i_{prefix}_word_start_hi").value = 0
        getattr(dut, f"i_{prefix}_word_end_lo").value = 0
        getattr(dut, f"i_{prefix}_word_end_hi").value = 0
        getattr(dut, f"i_{prefix}_word_meta").value = 0
        getattr(dut, f"i_{prefix}_word_route").value = 0
        getattr(dut, f"i_{prefix}_word_flow").value = 0

    dut.rst.value = 1
    for _ in range(5):
        await RisingEdge(dut.clk)
    dut.rst.value = 0
    for _ in range(2):
        await RisingEdge(dut.clk)


def drive_channel_entry(
    dut,
    prefix,
    *,
    start_ns,
    end_ns,
    context_id,
    opcode,
    plane_id,
    app_id,
    target_port,
    queue_id,
    dst_node_id,
    flow_id,
    flags,
):
    getattr(dut, f"i_{prefix}_word_start_lo").value = start_ns & 0xFFFFFFFF
    getattr(dut, f"i_{prefix}_word_start_hi").value = (start_ns >> 32) & 0xFFFFFFFF
    getattr(dut, f"i_{prefix}_word_end_lo").value = end_ns & 0xFFFFFFFF
    getattr(dut, f"i_{prefix}_word_end_hi").value = (end_ns >> 32) & 0xFFFFFFFF
    getattr(dut, f"i_{prefix}_word_meta").value = (
        ((context_id & 0xFFFF) << 16)
        | ((opcode & 0xFF) << 8)
        | ((plane_id & 0xF) << 4)
        | (app_id & 0xF)
    )
    getattr(dut, f"i_{prefix}_word_route").value = (
        ((queue_id & 0xFFFF) << 16)
        | ((target_port & 0xFF) << 8)
        | (flags & 0xFF)
    )
    getattr(dut, f"i_{prefix}_word_flow").value = (
        ((dst_node_id & 0xFFFF) << 16) | (flow_id & 0xFFFF)
    )


async def step_time(dut, time_ns, cycles=1):
    dut.i_ptp_time_ns.value = time_ns
    for _ in range(cycles):
        await RisingEdge(dut.clk)


async def settle_enabled_scheduler(dut):
    await RisingEdge(dut.clk)


async def wait_until(dut, predicate, *, cycles=4, msg="condition not met"):
    for _ in range(cycles):
        await RisingEdge(dut.clk)
        if predicate():
            return
    raise AssertionError(msg)


async def wait_at_time_until(dut, time_ns, predicate, *, cycles=4, msg="condition not met at time"):
    dut.i_ptp_time_ns.value = time_ns
    await wait_until(dut, predicate, cycles=cycles, msg=msg)


async def arm_pending_bank(dut, bank, activate_time_ns):
    dut.cfg_set_pending_bank.value = bank
    dut.cfg_set_pending_time_ns.value = activate_time_ns
    dut.cfg_set_pending_valid.value = 1
    await RisingEdge(dut.clk)
    dut.cfg_set_pending_valid.value = 0


@cocotb.test()
async def test_tt_scheduler_bank_flip_waits_for_safe_point(dut):
    cocotb.start_soon(Clock(dut.clk, 4, unit="ns").start())
    await reset_dut(dut)

    drive_channel_entry(
        dut,
        "tx",
        start_ns=100,
        end_ns=180,
        context_id=1,
        opcode=0x10,
        plane_id=0,
        app_id=1,
        target_port=2,
        queue_id=3,
        dst_node_id=4,
        flow_id=5,
        flags=FLAG_VALID | FLAG_TX_ENABLE | FLAG_COMPLETION_EVENT,
    )
    drive_channel_entry(
        dut,
        "rx",
        start_ns=100,
        end_ns=220,
        context_id=6,
        opcode=0x11,
        plane_id=1,
        app_id=2,
        target_port=7,
        queue_id=8,
        dst_node_id=9,
        flow_id=10,
        flags=FLAG_VALID | FLAG_RX_ENABLE,
    )

    dut.i_enable.value = 1
    dut.cfg_exec_enable.value = 1
    await settle_enabled_scheduler(dut)

    await wait_at_time_until(
        dut,
        120,
        lambda: bit(dut.o_tx_window_open_pulse) == 1 and bit(dut.o_rx_window_open_pulse) == 1,
        msg="TX/RX windows did not open at the expected active time",
    )
    assert bit(dut.o_tx_allowed) == 1
    assert bit(dut.o_rx_enabled) == 1
    assert bit(dut.o_active_bank) == 0

    await arm_pending_bank(dut, bank=1, activate_time_ns=150)
    await wait_until(
        dut,
        lambda: bit(dut.o_pending_valid) == 1,
        msg="Pending bank request was not latched",
    )

    await step_time(dut, 160)
    assert bit(dut.o_active_bank) == 0
    assert bit(dut.o_pending_valid) == 1

    await step_time(dut, 190)
    assert bit(dut.o_active_bank) == 0
    assert bit(dut.o_pending_valid) == 1

    await wait_at_time_until(
        dut,
        220,
        lambda: bit(dut.o_active_bank) == 1,
        msg="Pending bank did not flip once both channels became safe",
    )
    assert bit(dut.o_active_bank) == 1
    assert bit(dut.o_pending_valid) == 0


@cocotb.test()
async def test_tt_scheduler_tx_rx_progress_independently_between_flips(dut):
    cocotb.start_soon(Clock(dut.clk, 4, unit="ns").start())
    await reset_dut(dut)

    drive_channel_entry(
        dut,
        "tx",
        start_ns=50,
        end_ns=90,
        context_id=0x1234,
        opcode=0x20,
        plane_id=0x1,
        app_id=0x2,
        target_port=0x33,
        queue_id=0x4444,
        dst_node_id=0x5555,
        flow_id=0x6666,
        flags=FLAG_VALID | FLAG_TX_ENABLE | FLAG_COMPLETION_EVENT,
    )
    drive_channel_entry(
        dut,
        "rx",
        start_ns=60,
        end_ns=140,
        context_id=0xAAAA,
        opcode=0x21,
        plane_id=0x2,
        app_id=0x3,
        target_port=0x12,
        queue_id=0x3456,
        dst_node_id=0x789A,
        flow_id=0xBCDE,
        flags=FLAG_VALID | FLAG_RX_ENABLE,
    )

    dut.i_enable.value = 1
    dut.cfg_exec_enable.value = 1
    await settle_enabled_scheduler(dut)

    await wait_at_time_until(
        dut,
        70,
        lambda: bit(dut.o_tx_window_open_pulse) == 1 and bit(dut.o_rx_window_open_pulse) == 1,
        msg="TX/RX windows did not open for the expected entries",
    )
    assert dut.o_tx_current_entry_ptr.value.to_unsigned() == 0
    assert dut.o_rx_current_entry_ptr.value.to_unsigned() == 0
    assert dut.o_tx_target_port.value.to_unsigned() == 0x33
    assert dut.o_rx_target_port.value.to_unsigned() == 0x12

    await wait_at_time_until(
        dut,
        95,
        lambda: bit(dut.o_tx_window_close_pulse) == 1 and bit(dut.o_tx_commit_start_pulse) == 1,
        msg="TX channel did not retire and emit completion at the expected time",
    )
    assert dut.o_tx_current_entry_ptr.value.to_unsigned() == 1
    assert dut.o_rx_current_entry_ptr.value.to_unsigned() == 0

    await wait_at_time_until(
        dut,
        145,
        lambda: bit(dut.o_rx_window_close_pulse) == 1,
        msg="RX channel did not retire at the expected time",
    )
    assert dut.o_rx_current_entry_ptr.value.to_unsigned() == 1


@cocotb.test()
async def test_tt_scheduler_invalid_entries_do_not_open_windows(dut):
    cocotb.start_soon(Clock(dut.clk, 4, unit="ns").start())
    await reset_dut(dut)

    drive_channel_entry(
        dut,
        "tx",
        start_ns=10,
        end_ns=100,
        context_id=1,
        opcode=2,
        plane_id=0,
        app_id=0,
        target_port=1,
        queue_id=2,
        dst_node_id=3,
        flow_id=4,
        flags=0,
    )

    dut.i_enable.value = 1
    dut.cfg_exec_enable.value = 1
    await settle_enabled_scheduler(dut)

    await step_time(dut, 50)
    assert bit(dut.o_tx_exec_valid) == 0
    assert bit(dut.o_tx_allowed) == 0
    assert bit(dut.o_tx_window_open_pulse) == 0
    assert bit(dut.o_tx_window_active) == 0


@cocotb.test()
async def test_tt_scheduler_restart_resets_idle_channel_state(dut):
    cocotb.start_soon(Clock(dut.clk, 4, unit="ns").start())
    await reset_dut(dut)

    # Keep both channels unarmed so a bank flip can happen immediately once the
    # requested activation time is reached. This isolates the scheduler's
    # restart behavior from exec-table banking semantics, which are tested
    # separately with exec_table.
    drive_channel_entry(
        dut,
        "tx",
        start_ns=10,
        end_ns=40,
        context_id=0x1111,
        opcode=0x22,
        plane_id=0x1,
        app_id=0x2,
        target_port=0x44,
        queue_id=0x5555,
        dst_node_id=0x6666,
        flow_id=0x7777,
        flags=0,
    )
    drive_channel_entry(
        dut,
        "rx",
        start_ns=10,
        end_ns=40,
        context_id=0xAAAA,
        opcode=0x11,
        plane_id=0x2,
        app_id=0x3,
        target_port=0x12,
        queue_id=0x2222,
        dst_node_id=0x3333,
        flow_id=0x4444,
        flags=0,
    )

    dut.i_enable.value = 1
    dut.cfg_exec_enable.value = 1
    await settle_enabled_scheduler(dut)

    await arm_pending_bank(dut, bank=1, activate_time_ns=25)
    await wait_at_time_until(
        dut,
        30,
        lambda: bit(dut.o_active_bank) == 1,
        msg="Bank flip did not complete during restart test",
    )
    assert bit(dut.o_active_bank) == 1
    assert dut.o_tx_current_entry_ptr.value.to_unsigned() == 0
    assert dut.o_rx_current_entry_ptr.value.to_unsigned() == 0
    assert bit(dut.o_tx_window_active) == 0
    assert bit(dut.o_tx_allowed) == 0
    assert bit(dut.o_rx_window_active) == 0
    assert bit(dut.o_rx_enabled) == 0


@cocotb.test()
async def test_tt_scheduler_completion_pulse_only_when_flagged(dut):
    cocotb.start_soon(Clock(dut.clk, 4, unit="ns").start())
    await reset_dut(dut)

    drive_channel_entry(
        dut,
        "tx",
        start_ns=10,
        end_ns=30,
        context_id=0x1,
        opcode=0x2,
        plane_id=0x0,
        app_id=0x1,
        target_port=0x2,
        queue_id=0x3,
        dst_node_id=0x4,
        flow_id=0x5,
        flags=FLAG_VALID | FLAG_TX_ENABLE,
    )

    dut.i_enable.value = 1
    dut.cfg_exec_enable.value = 1
    await settle_enabled_scheduler(dut)

    await wait_at_time_until(
        dut,
        15,
        lambda: bit(dut.o_tx_window_open_pulse) == 1,
        msg="TX window did not open for completion-flag test",
    )
    await wait_at_time_until(
        dut,
        30,
        lambda: bit(dut.o_tx_window_close_pulse) == 1,
        msg="TX window did not close for completion-flag test",
    )
    assert bit(dut.o_tx_commit_start_pulse) == 0
