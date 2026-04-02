`timescale 1ns / 1ps
`default_nettype none
`include "fpga/app/sync_dcn/rtl/common/sync_dcn_defs.vh"

/*
 * Dual-channel compiled schedule executor for SDCN.
 *
 * The target NIC artifact now carries separate TX and RX schedules, so the
 * hardware execution model also exposes two independent schedule channels.
 * Both channels share one bank-selection state so a future bank switch becomes
 * visible to TX and RX together, but each channel keeps its own local table,
 * entry pointer, and execution-window state.
 *
 * Legacy note:
 * The original subsystem exposed a single execution-table path.  This wrapper
 * preserves the old TX-oriented outputs for compatibility while adding a
 * second RX-oriented status bundle and a second host programming window.
 */
module sync_schedule_executor #(
    parameter integer TX_ENTRY_INDEX_WIDTH = 10,
    parameter integer TX_ENTRY_COUNT = 1024,
    parameter integer RX_ENTRY_INDEX_WIDTH = 9,
    parameter integer RX_ENTRY_COUNT = 512
) (
    input  wire                            clk,
    input  wire                            rst,

    input  wire                            i_enable,
    input  wire [63:0]                     i_ptp_time_ns,

    input  wire                            cfg_exec_enable,
    input  wire                            cfg_set_pending_valid,
    input  wire                            cfg_set_pending_bank,
    input  wire [63:0]                     cfg_set_pending_time_ns,

    input  wire                            cfg_tx_wr_en,
    input  wire                            cfg_tx_wr_bank,
    input  wire [TX_ENTRY_INDEX_WIDTH-1:0] cfg_tx_wr_entry,
    input  wire [2:0]                      cfg_tx_wr_word,
    input  wire [31:0]                     cfg_tx_wr_data,
    input  wire                            cfg_tx_rd_bank,
    input  wire [TX_ENTRY_INDEX_WIDTH-1:0] cfg_tx_rd_entry,
    input  wire [2:0]                      cfg_tx_rd_word,
    output wire [31:0]                     cfg_tx_rd_data,

    input  wire                            cfg_rx_wr_en,
    input  wire                            cfg_rx_wr_bank,
    input  wire [RX_ENTRY_INDEX_WIDTH-1:0] cfg_rx_wr_entry,
    input  wire [2:0]                      cfg_rx_wr_word,
    input  wire [31:0]                     cfg_rx_wr_data,
    input  wire                            cfg_rx_rd_bank,
    input  wire [RX_ENTRY_INDEX_WIDTH-1:0] cfg_rx_rd_entry,
    input  wire [2:0]                      cfg_rx_rd_word,
    output wire [31:0]                     cfg_rx_rd_data,

    // TX execution outputs (legacy-visible status bundle)
    output wire [63:0]                     o_current_window_id,
    output wire [TX_ENTRY_INDEX_WIDTH-1:0] o_current_entry_ptr,
    output wire                            o_window_open_pulse,
    output wire                            o_window_close_pulse,
    output wire                            o_commit_start_pulse,
    output wire                            o_exec_valid,
    output wire                            o_tx_allowed,
    output wire                            o_window_active,
    output wire [7:0]                      o_target_port,
    output wire [15:0]                     o_target_queue,
    output wire [7:0]                      o_app_id,
    output wire [7:0]                      o_plane_id,
    output wire [7:0]                      o_opcode,
    output wire [15:0]                     o_context_id,
    output wire [15:0]                     o_dst_node_id,
    output wire [15:0]                     o_flow_id,

    // RX execution outputs
    output wire [63:0]                     o_rx_current_window_id,
    output wire [RX_ENTRY_INDEX_WIDTH-1:0] o_rx_current_entry_ptr,
    output wire                            o_rx_window_open_pulse,
    output wire                            o_rx_window_close_pulse,
    output wire                            o_rx_commit_start_pulse,
    output wire                            o_rx_exec_valid,
    output wire                            o_rx_enabled,
    output wire                            o_rx_window_active,
    output wire [7:0]                      o_rx_target_port,
    output wire [15:0]                     o_rx_target_queue,
    output wire [7:0]                      o_rx_app_id,
    output wire [7:0]                      o_rx_plane_id,
    output wire [7:0]                      o_rx_opcode,
    output wire [15:0]                     o_rx_context_id,
    output wire [15:0]                     o_rx_dst_node_id,
    output wire [15:0]                     o_rx_flow_id,

    output reg                             o_active_bank,
    output reg                             o_pending_valid,
    output reg                             o_pending_bank,
    output reg  [63:0]                     o_pending_time_ns,

    output wire [63:0]                     o_active_entry_start_time_ns,
    output wire [63:0]                     o_active_entry_end_time_ns,
    output wire [31:0]                     o_active_entry_meta,
    output wire [31:0]                     o_active_entry_route,
    output wire [31:0]                     o_active_entry_flow,

    output wire [63:0]                     o_rx_active_entry_start_time_ns,
    output wire [63:0]                     o_rx_active_entry_end_time_ns,
    output wire [31:0]                     o_rx_active_entry_meta,
    output wire [31:0]                     o_rx_active_entry_route,
    output wire [31:0]                     o_rx_active_entry_flow
);

wire tx_switch_safe;
wire rx_switch_safe;
wire channels_switch_safe = tx_switch_safe && rx_switch_safe;
wire pending_flip_due = o_pending_valid && i_ptp_time_ns >= o_pending_time_ns;
wire can_flip_bank = pending_flip_due && channels_switch_safe;

reg restart_pulse_reg = 1'b0;
wire core_enable = i_enable && cfg_exec_enable;

always @(posedge clk) begin
    if (rst) begin
        o_active_bank <= 1'b0;
        o_pending_valid <= 1'b0;
        o_pending_bank <= 1'b0;
        o_pending_time_ns <= 64'd0;
        restart_pulse_reg <= 1'b0;
    end else begin
        restart_pulse_reg <= 1'b0;

        if (cfg_set_pending_valid) begin
            o_pending_valid <= 1'b1;
            o_pending_bank <= cfg_set_pending_bank;
            o_pending_time_ns <= cfg_set_pending_time_ns;
        end

        if (can_flip_bank) begin
            o_active_bank <= o_pending_bank;
            o_pending_valid <= 1'b0;
            restart_pulse_reg <= 1'b1;
        end
    end
end

sync_schedule_channel_core #(
    .ENTRY_INDEX_WIDTH(TX_ENTRY_INDEX_WIDTH),
    .ENTRY_COUNT(TX_ENTRY_COUNT),
    .CHANNEL_FLAG_MASK(`SYNC_DCN_FLAG_TX_ENABLE)
)
tx_core_inst (
    .clk(clk),
    .rst(rst),
    .i_enable(core_enable),
    .i_ptp_time_ns(i_ptp_time_ns),
    .i_active_bank(o_active_bank),
    .i_restart_pulse(restart_pulse_reg),
    .cfg_wr_en(cfg_tx_wr_en),
    .cfg_wr_bank(cfg_tx_wr_bank),
    .cfg_wr_entry(cfg_tx_wr_entry),
    .cfg_wr_word(cfg_tx_wr_word),
    .cfg_wr_data(cfg_tx_wr_data),
    .cfg_rd_bank(cfg_tx_rd_bank),
    .cfg_rd_entry(cfg_tx_rd_entry),
    .cfg_rd_word(cfg_tx_rd_word),
    .cfg_rd_data(cfg_tx_rd_data),
    .o_current_window_id(o_current_window_id),
    .o_current_entry_ptr(o_current_entry_ptr),
    .o_window_open_pulse(o_window_open_pulse),
    .o_window_close_pulse(o_window_close_pulse),
    .o_commit_start_pulse(o_commit_start_pulse),
    .o_exec_valid(o_exec_valid),
    .o_channel_allowed(o_tx_allowed),
    .o_window_active(o_window_active),
    .o_target_port(o_target_port),
    .o_target_queue(o_target_queue),
    .o_app_id(o_app_id),
    .o_plane_id(o_plane_id),
    .o_opcode(o_opcode),
    .o_context_id(o_context_id),
    .o_dst_node_id(o_dst_node_id),
    .o_flow_id(o_flow_id),
    .o_active_entry_start_time_ns(o_active_entry_start_time_ns),
    .o_active_entry_end_time_ns(o_active_entry_end_time_ns),
    .o_active_entry_meta(o_active_entry_meta),
    .o_active_entry_route(o_active_entry_route),
    .o_active_entry_flow(o_active_entry_flow),
    .o_switch_safe(tx_switch_safe)
);

sync_schedule_channel_core #(
    .ENTRY_INDEX_WIDTH(RX_ENTRY_INDEX_WIDTH),
    .ENTRY_COUNT(RX_ENTRY_COUNT),
    .CHANNEL_FLAG_MASK(`SYNC_DCN_FLAG_RX_ENABLE)
)
rx_core_inst (
    .clk(clk),
    .rst(rst),
    .i_enable(core_enable),
    .i_ptp_time_ns(i_ptp_time_ns),
    .i_active_bank(o_active_bank),
    .i_restart_pulse(restart_pulse_reg),
    .cfg_wr_en(cfg_rx_wr_en),
    .cfg_wr_bank(cfg_rx_wr_bank),
    .cfg_wr_entry(cfg_rx_wr_entry),
    .cfg_wr_word(cfg_rx_wr_word),
    .cfg_wr_data(cfg_rx_wr_data),
    .cfg_rd_bank(cfg_rx_rd_bank),
    .cfg_rd_entry(cfg_rx_rd_entry),
    .cfg_rd_word(cfg_rx_rd_word),
    .cfg_rd_data(cfg_rx_rd_data),
    .o_current_window_id(o_rx_current_window_id),
    .o_current_entry_ptr(o_rx_current_entry_ptr),
    .o_window_open_pulse(o_rx_window_open_pulse),
    .o_window_close_pulse(o_rx_window_close_pulse),
    .o_commit_start_pulse(o_rx_commit_start_pulse),
    .o_exec_valid(o_rx_exec_valid),
    .o_channel_allowed(o_rx_enabled),
    .o_window_active(o_rx_window_active),
    .o_target_port(o_rx_target_port),
    .o_target_queue(o_rx_target_queue),
    .o_app_id(o_rx_app_id),
    .o_plane_id(o_rx_plane_id),
    .o_opcode(o_rx_opcode),
    .o_context_id(o_rx_context_id),
    .o_dst_node_id(o_rx_dst_node_id),
    .o_flow_id(o_rx_flow_id),
    .o_active_entry_start_time_ns(o_rx_active_entry_start_time_ns),
    .o_active_entry_end_time_ns(o_rx_active_entry_end_time_ns),
    .o_active_entry_meta(o_rx_active_entry_meta),
    .o_active_entry_route(o_rx_active_entry_route),
    .o_active_entry_flow(o_rx_active_entry_flow),
    .o_switch_safe(rx_switch_safe)
);

endmodule

`default_nettype wire
