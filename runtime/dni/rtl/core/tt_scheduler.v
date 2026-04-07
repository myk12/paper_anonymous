`timescale 1ns / 1ps
`default_nettype none
`include "common/dni_defs.vh"

/*
 * Dual-channel time-triggered scheduler for the DNI.
 *
 * This block owns the runtime execution policy for the installed TX and RX
 * schedules. It is responsible for:
 *   1. maintaining the active/pending bank state shared by both directions
 *   2. deciding when a pending bank may become active
 *   3. advancing each direction's current entry pointer over time
 *   4. opening and closing execution windows and emitting commit events
 *
 * Execution-table storage and host-side programming live in the sibling
 * exec_table module. The schedule_decode helper instances below are internal
 * implementation details of this scheduler, not separate architectural blocks.
 *
 */
module tt_scheduler #(
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

    // Schedule evaluation inputs (current-entry word readout from exec_table)
    input  wire [31:0]                     i_tx_word_start_lo,
    input  wire [31:0]                     i_tx_word_start_hi,
    input  wire [31:0]                     i_tx_word_end_lo,
    input  wire [31:0]                     i_tx_word_end_hi,
    input  wire [31:0]                     i_tx_word_meta,
    input  wire [31:0]                     i_tx_word_route,
    input  wire [31:0]                     i_tx_word_flow,

    input  wire [31:0]                     i_rx_word_start_lo,
    input  wire [31:0]                     i_rx_word_start_hi,
    input  wire [31:0]                     i_rx_word_end_lo,
    input  wire [31:0]                     i_rx_word_end_hi,
    input  wire [31:0]                     i_rx_word_meta,
    input  wire [31:0]                     i_rx_word_route,
    input  wire [31:0]                     i_rx_word_flow,

    // TX execution outputs
    output reg  [63:0]                     o_tx_current_window_id,
    output reg  [TX_ENTRY_INDEX_WIDTH-1:0] o_tx_current_entry_ptr,
    output reg                             o_tx_window_open_pulse,
    output reg                             o_tx_window_close_pulse,
    output reg                             o_tx_commit_start_pulse,
    output reg                             o_tx_exec_valid,
    output reg                             o_tx_allowed,
    output reg                             o_tx_window_active,
    output reg  [7:0]                      o_tx_target_port,
    output reg  [15:0]                     o_tx_target_queue,
    output reg  [7:0]                      o_tx_app_id,
    output reg  [7:0]                      o_tx_plane_id,
    output reg  [7:0]                      o_tx_opcode,
    output reg  [15:0]                     o_tx_context_id,
    output reg  [15:0]                     o_tx_dst_node_id,
    output reg  [15:0]                     o_tx_flow_id,

    // RX execution outputs
    output reg  [63:0]                     o_rx_current_window_id,
    output reg  [RX_ENTRY_INDEX_WIDTH-1:0] o_rx_current_entry_ptr,
    output reg                             o_rx_window_open_pulse,
    output reg                             o_rx_window_close_pulse,
    output reg                             o_rx_commit_start_pulse,
    output reg                             o_rx_exec_valid,
    output reg                             o_rx_enabled,
    output reg                             o_rx_window_active,
    output reg  [7:0]                      o_rx_target_port,
    output reg  [15:0]                     o_rx_target_queue,
    output reg  [7:0]                      o_rx_app_id,
    output reg  [7:0]                      o_rx_plane_id,
    output reg  [7:0]                      o_rx_opcode,
    output reg  [15:0]                     o_rx_context_id,
    output reg  [15:0]                     o_rx_dst_node_id,
    output reg  [15:0]                     o_rx_flow_id,

    output reg                             o_active_bank,
    output reg                             o_pending_valid,
    output reg                             o_pending_bank,
    output reg  [63:0]                     o_pending_time_ns,

    output reg  [63:0]                     o_active_entry_start_time_ns,
    output reg  [63:0]                     o_active_entry_end_time_ns,
    output reg  [31:0]                     o_active_entry_meta,
    output reg  [31:0]                     o_active_entry_route,
    output reg  [31:0]                     o_active_entry_flow,

    output reg  [63:0]                     o_rx_active_entry_start_time_ns,
    output reg  [63:0]                     o_rx_active_entry_end_time_ns,
    output reg  [31:0]                     o_rx_active_entry_meta,
    output reg  [31:0]                     o_rx_active_entry_route,
    output reg  [31:0]                     o_rx_active_entry_flow
);

reg tx_initialized_reg = 1'b0;
reg tx_window_active_reg = 1'b0;
reg rx_initialized_reg = 1'b0;
reg rx_window_active_reg = 1'b0;

wire [63:0] tx_eval_start_time_ns = {i_tx_word_start_hi, i_tx_word_start_lo};
wire [63:0] tx_eval_end_time_ns = {i_tx_word_end_hi, i_tx_word_end_lo};
wire [7:0]  tx_eval_flags = i_tx_word_route[7:0];
wire        tx_eval_entry_valid = (tx_eval_flags & `DNI_FLAG_VALID) != 0;
wire        tx_eval_entry_channel_enable = (tx_eval_flags & `DNI_FLAG_TX_ENABLE) != 0;
wire        tx_eval_entry_armed = tx_eval_entry_valid && tx_eval_entry_channel_enable;
wire        tx_eval_entry_started = tx_eval_entry_armed && i_ptp_time_ns >= tx_eval_start_time_ns;
wire        tx_eval_entry_active = tx_eval_entry_started && i_ptp_time_ns < tx_eval_end_time_ns;
wire        tx_eval_entry_retire = tx_eval_entry_armed && i_ptp_time_ns >= tx_eval_end_time_ns;
wire        tx_eval_entry_completion_event = (tx_eval_flags & `DNI_FLAG_COMPLETION_EVENT) != 0;
wire [TX_ENTRY_INDEX_WIDTH-1:0] tx_next_entry_ptr = o_tx_current_entry_ptr + 1'b1;
wire tx_switch_safe = !tx_window_active_reg || tx_eval_entry_retire || !tx_eval_entry_armed;

wire [63:0] rx_eval_start_time_ns = {i_rx_word_start_hi, i_rx_word_start_lo};
wire [63:0] rx_eval_end_time_ns = {i_rx_word_end_hi, i_rx_word_end_lo};
wire [7:0]  rx_eval_flags = i_rx_word_route[7:0];
wire        rx_eval_entry_valid = (rx_eval_flags & `DNI_FLAG_VALID) != 0;
wire        rx_eval_entry_channel_enable = (rx_eval_flags & `DNI_FLAG_RX_ENABLE) != 0;
wire        rx_eval_entry_armed = rx_eval_entry_valid && rx_eval_entry_channel_enable;
wire        rx_eval_entry_started = rx_eval_entry_armed && i_ptp_time_ns >= rx_eval_start_time_ns;
wire        rx_eval_entry_active = rx_eval_entry_started && i_ptp_time_ns < rx_eval_end_time_ns;
wire        rx_eval_entry_retire = rx_eval_entry_armed && i_ptp_time_ns >= rx_eval_end_time_ns;
wire        rx_eval_entry_completion_event = (rx_eval_flags & `DNI_FLAG_COMPLETION_EVENT) != 0;
wire [RX_ENTRY_INDEX_WIDTH-1:0] rx_next_entry_ptr = o_rx_current_entry_ptr + 1'b1;
wire rx_switch_safe = !rx_window_active_reg || rx_eval_entry_retire || !rx_eval_entry_armed;

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

always @(posedge clk) begin
    if (rst) begin
        tx_initialized_reg <= 1'b0;
        tx_window_active_reg <= 1'b0;
        o_tx_current_window_id <= 64'd0;
        o_tx_current_entry_ptr <= {TX_ENTRY_INDEX_WIDTH{1'b0}};
        o_tx_window_open_pulse <= 1'b0;
        o_tx_window_close_pulse <= 1'b0;
        o_tx_commit_start_pulse <= 1'b0;
        o_tx_exec_valid <= 1'b0;
        o_tx_allowed <= 1'b0;
        o_tx_window_active <= 1'b0;
        o_tx_target_port <= 8'd0;
        o_tx_target_queue <= 16'd0;
        o_tx_app_id <= 8'd0;
        o_tx_plane_id <= 8'd0;
        o_tx_opcode <= 8'd0;
        o_tx_context_id <= 16'd0;
        o_tx_dst_node_id <= 16'd0;
        o_tx_flow_id <= 16'd0;
        o_active_entry_start_time_ns <= 64'd0;
        o_active_entry_end_time_ns <= 64'd0;
        o_active_entry_meta <= 32'd0;
        o_active_entry_route <= 32'd0;
        o_active_entry_flow <= 32'd0;
    end else begin
        o_tx_window_open_pulse <= 1'b0;
        o_tx_window_close_pulse <= 1'b0;
        o_tx_commit_start_pulse <= 1'b0;

        if (!core_enable || restart_pulse_reg) begin
            tx_initialized_reg <= restart_pulse_reg;
            tx_window_active_reg <= 1'b0;
            o_tx_current_entry_ptr <= {TX_ENTRY_INDEX_WIDTH{1'b0}};
            o_tx_exec_valid <= 1'b0;
            o_tx_allowed <= 1'b0;
            o_tx_window_active <= 1'b0;
            o_tx_target_port <= 8'd0;
            o_tx_target_queue <= 16'd0;
            o_tx_app_id <= 8'd0;
            o_tx_plane_id <= 8'd0;
            o_tx_opcode <= 8'd0;
            o_tx_context_id <= 16'd0;
            o_tx_dst_node_id <= 16'd0;
            o_tx_flow_id <= 16'd0;
            o_active_entry_start_time_ns <= 64'd0;
            o_active_entry_end_time_ns <= 64'd0;
            o_active_entry_meta <= 32'd0;
            o_active_entry_route <= 32'd0;
            o_active_entry_flow <= 32'd0;
        end else if (!tx_initialized_reg) begin
            tx_initialized_reg <= 1'b1;
            tx_window_active_reg <= 1'b0;
            o_tx_current_entry_ptr <= {TX_ENTRY_INDEX_WIDTH{1'b0}};
            o_tx_exec_valid <= 1'b0;
            o_tx_allowed <= 1'b0;
            o_tx_window_active <= 1'b0;
        end else begin
            o_active_entry_start_time_ns <= tx_eval_start_time_ns;
            o_active_entry_end_time_ns <= tx_eval_end_time_ns;
            o_active_entry_meta <= i_tx_word_meta;
            o_active_entry_route <= i_tx_word_route;
            o_active_entry_flow <= i_tx_word_flow;

            if (!tx_window_active_reg && tx_eval_entry_active) begin
                tx_window_active_reg <= 1'b1;
                o_tx_current_window_id <= o_tx_current_window_id + 1'b1;
                o_tx_window_open_pulse <= 1'b1;
                o_tx_exec_valid <= 1'b1;
                o_tx_allowed <= 1'b1;
                o_tx_window_active <= 1'b1;
                o_tx_target_port <= i_tx_word_route[15:8];
                o_tx_target_queue <= i_tx_word_route[31:16];
                o_tx_app_id <= {4'd0, i_tx_word_meta[3:0]};
                o_tx_plane_id <= {4'd0, i_tx_word_meta[7:4]};
                o_tx_opcode <= i_tx_word_meta[15:8];
                o_tx_context_id <= i_tx_word_meta[31:16];
                o_tx_dst_node_id <= i_tx_word_flow[31:16];
                o_tx_flow_id <= i_tx_word_flow[15:0];
            end else if (tx_window_active_reg && tx_eval_entry_retire) begin
                tx_window_active_reg <= 1'b0;
                o_tx_window_close_pulse <= 1'b1;
                o_tx_commit_start_pulse <= tx_eval_entry_completion_event;
                o_tx_exec_valid <= tx_eval_entry_armed;
                o_tx_allowed <= 1'b0;
                o_tx_window_active <= 1'b0;
                o_tx_target_port <= i_tx_word_route[15:8];
                o_tx_target_queue <= i_tx_word_route[31:16];
                o_tx_app_id <= {4'd0, i_tx_word_meta[3:0]};
                o_tx_plane_id <= {4'd0, i_tx_word_meta[7:4]};
                o_tx_opcode <= i_tx_word_meta[15:8];
                o_tx_context_id <= i_tx_word_meta[31:16];
                o_tx_dst_node_id <= i_tx_word_flow[31:16];
                o_tx_flow_id <= i_tx_word_flow[15:0];
                o_tx_current_entry_ptr <= tx_next_entry_ptr;
            end else begin
                o_tx_exec_valid <= tx_eval_entry_armed;
                o_tx_allowed <= tx_window_active_reg && tx_eval_entry_active;
                o_tx_window_active <= tx_window_active_reg && tx_eval_entry_active;
                if (tx_eval_entry_armed) begin
                    o_tx_target_port <= i_tx_word_route[15:8];
                    o_tx_target_queue <= i_tx_word_route[31:16];
                    o_tx_app_id <= {4'd0, i_tx_word_meta[3:0]};
                    o_tx_plane_id <= {4'd0, i_tx_word_meta[7:4]};
                    o_tx_opcode <= i_tx_word_meta[15:8];
                    o_tx_context_id <= i_tx_word_meta[31:16];
                    o_tx_dst_node_id <= i_tx_word_flow[31:16];
                    o_tx_flow_id <= i_tx_word_flow[15:0];
                end else begin
                    o_tx_target_port <= 8'd0;
                    o_tx_target_queue <= 16'd0;
                    o_tx_app_id <= 8'd0;
                    o_tx_plane_id <= 8'd0;
                    o_tx_opcode <= 8'd0;
                    o_tx_context_id <= 16'd0;
                    o_tx_dst_node_id <= 16'd0;
                    o_tx_flow_id <= 16'd0;
                end
            end
        end
    end
end

always @(posedge clk) begin
    if (rst) begin
        rx_initialized_reg <= 1'b0;
        rx_window_active_reg <= 1'b0;
        o_rx_current_window_id <= 64'd0;
        o_rx_current_entry_ptr <= {RX_ENTRY_INDEX_WIDTH{1'b0}};
        o_rx_window_open_pulse <= 1'b0;
        o_rx_window_close_pulse <= 1'b0;
        o_rx_commit_start_pulse <= 1'b0;
        o_rx_exec_valid <= 1'b0;
        o_rx_enabled <= 1'b0;
        o_rx_window_active <= 1'b0;
        o_rx_target_port <= 8'd0;
        o_rx_target_queue <= 16'd0;
        o_rx_app_id <= 8'd0;
        o_rx_plane_id <= 8'd0;
        o_rx_opcode <= 8'd0;
        o_rx_context_id <= 16'd0;
        o_rx_dst_node_id <= 16'd0;
        o_rx_flow_id <= 16'd0;
        o_rx_active_entry_start_time_ns <= 64'd0;
        o_rx_active_entry_end_time_ns <= 64'd0;
        o_rx_active_entry_meta <= 32'd0;
        o_rx_active_entry_route <= 32'd0;
        o_rx_active_entry_flow <= 32'd0;
    end else begin
        o_rx_window_open_pulse <= 1'b0;
        o_rx_window_close_pulse <= 1'b0;
        o_rx_commit_start_pulse <= 1'b0;

        if (!core_enable || restart_pulse_reg) begin
            rx_initialized_reg <= restart_pulse_reg;
            rx_window_active_reg <= 1'b0;
            o_rx_current_entry_ptr <= {RX_ENTRY_INDEX_WIDTH{1'b0}};
            o_rx_exec_valid <= 1'b0;
            o_rx_enabled <= 1'b0;
            o_rx_window_active <= 1'b0;
            o_rx_target_port <= 8'd0;
            o_rx_target_queue <= 16'd0;
            o_rx_app_id <= 8'd0;
            o_rx_plane_id <= 8'd0;
            o_rx_opcode <= 8'd0;
            o_rx_context_id <= 16'd0;
            o_rx_dst_node_id <= 16'd0;
            o_rx_flow_id <= 16'd0;
            o_rx_active_entry_start_time_ns <= 64'd0;
            o_rx_active_entry_end_time_ns <= 64'd0;
            o_rx_active_entry_meta <= 32'd0;
            o_rx_active_entry_route <= 32'd0;
            o_rx_active_entry_flow <= 32'd0;
        end else if (!rx_initialized_reg) begin
            rx_initialized_reg <= 1'b1;
            rx_window_active_reg <= 1'b0;
            o_rx_current_entry_ptr <= {RX_ENTRY_INDEX_WIDTH{1'b0}};
            o_rx_exec_valid <= 1'b0;
            o_rx_enabled <= 1'b0;
            o_rx_window_active <= 1'b0;
        end else begin
            o_rx_active_entry_start_time_ns <= rx_eval_start_time_ns;
            o_rx_active_entry_end_time_ns <= rx_eval_end_time_ns;
            o_rx_active_entry_meta <= i_rx_word_meta;
            o_rx_active_entry_route <= i_rx_word_route;
            o_rx_active_entry_flow <= i_rx_word_flow;

            if (!rx_window_active_reg && rx_eval_entry_active) begin
                rx_window_active_reg <= 1'b1;
                o_rx_current_window_id <= o_rx_current_window_id + 1'b1;
                o_rx_window_open_pulse <= 1'b1;
                o_rx_exec_valid <= 1'b1;
                o_rx_enabled <= 1'b1;
                o_rx_window_active <= 1'b1;
                o_rx_target_port <= i_rx_word_route[15:8];
                o_rx_target_queue <= i_rx_word_route[31:16];
                o_rx_app_id <= {4'd0, i_rx_word_meta[3:0]};
                o_rx_plane_id <= {4'd0, i_rx_word_meta[7:4]};
                o_rx_opcode <= i_rx_word_meta[15:8];
                o_rx_context_id <= i_rx_word_meta[31:16];
                o_rx_dst_node_id <= i_rx_word_flow[31:16];
                o_rx_flow_id <= i_rx_word_flow[15:0];
            end else if (rx_window_active_reg && rx_eval_entry_retire) begin
                rx_window_active_reg <= 1'b0;
                o_rx_window_close_pulse <= 1'b1;
                o_rx_commit_start_pulse <= rx_eval_entry_completion_event;
                o_rx_exec_valid <= rx_eval_entry_armed;
                o_rx_enabled <= 1'b0;
                o_rx_window_active <= 1'b0;
                o_rx_target_port <= i_rx_word_route[15:8];
                o_rx_target_queue <= i_rx_word_route[31:16];
                o_rx_app_id <= {4'd0, i_rx_word_meta[3:0]};
                o_rx_plane_id <= {4'd0, i_rx_word_meta[7:4]};
                o_rx_opcode <= i_rx_word_meta[15:8];
                o_rx_context_id <= i_rx_word_meta[31:16];
                o_rx_dst_node_id <= i_rx_word_flow[31:16];
                o_rx_flow_id <= i_rx_word_flow[15:0];
                o_rx_current_entry_ptr <= rx_next_entry_ptr;
            end else begin
                o_rx_exec_valid <= rx_eval_entry_armed;
                o_rx_enabled <= rx_window_active_reg && rx_eval_entry_active;
                o_rx_window_active <= rx_window_active_reg && rx_eval_entry_active;
                if (rx_eval_entry_armed) begin
                    o_rx_target_port <= i_rx_word_route[15:8];
                    o_rx_target_queue <= i_rx_word_route[31:16];
                    o_rx_app_id <= {4'd0, i_rx_word_meta[3:0]};
                    o_rx_plane_id <= {4'd0, i_rx_word_meta[7:4]};
                    o_rx_opcode <= i_rx_word_meta[15:8];
                    o_rx_context_id <= i_rx_word_meta[31:16];
                    o_rx_dst_node_id <= i_rx_word_flow[31:16];
                    o_rx_flow_id <= i_rx_word_flow[15:0];
                end else begin
                    o_rx_target_port <= 8'd0;
                    o_rx_target_queue <= 16'd0;
                    o_rx_app_id <= 8'd0;
                    o_rx_plane_id <= 8'd0;
                    o_rx_opcode <= 8'd0;
                    o_rx_context_id <= 16'd0;
                    o_rx_dst_node_id <= 16'd0;
                    o_rx_flow_id <= 16'd0;
                end
            end
        end
    end
end

endmodule

`default_nettype wire
