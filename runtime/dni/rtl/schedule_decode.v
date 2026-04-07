`timescale 1ns / 1ps
`default_nettype none
`include "common/dni_defs.vh"

/*
 * Single-direction compiled schedule executor core.
 *
 * This block owns one local entry pointer and execution-window state for a
 * single direction.  It consumes the currently selected entry words from the
 * execution-table block and interprets them against synchronized time.
 * Banked table storage and host-side programming live in exec_table; bank flip
 * policy lives in tt_scheduler.
 */
module schedule_decode #(
    parameter integer ENTRY_INDEX_WIDTH = 10,
    parameter [7:0] CHANNEL_FLAG_MASK = `DNI_FLAG_TX_ENABLE
) (
    input  wire                         clk,
    input  wire                         rst,

    input  wire                         i_enable,
    input  wire [63:0]                  i_ptp_time_ns,
    input  wire                         i_restart_pulse,
    input  wire [31:0]                  i_word_start_lo,
    input  wire [31:0]                  i_word_start_hi,
    input  wire [31:0]                  i_word_end_lo,
    input  wire [31:0]                  i_word_end_hi,
    input  wire [31:0]                  i_word_meta,
    input  wire [31:0]                  i_word_route,
    input  wire [31:0]                  i_word_flow,

    output reg  [63:0]                  o_current_window_id,
    output reg  [ENTRY_INDEX_WIDTH-1:0] o_current_entry_ptr,
    output reg                          o_window_open_pulse,
    output reg                          o_window_close_pulse,
    output reg                          o_commit_start_pulse,
    output reg                          o_exec_valid,
    output reg                          o_channel_allowed,
    output reg                          o_window_active,
    output reg  [7:0]                   o_target_port,
    output reg  [15:0]                  o_target_queue,
    output reg  [7:0]                   o_app_id,
    output reg  [7:0]                   o_plane_id,
    output reg  [7:0]                   o_opcode,
    output reg  [15:0]                  o_context_id,
    output reg  [15:0]                  o_dst_node_id,
    output reg  [15:0]                  o_flow_id,
    output reg  [63:0]                  o_active_entry_start_time_ns,
    output reg  [63:0]                  o_active_entry_end_time_ns,
    output reg  [31:0]                  o_active_entry_meta,
    output reg  [31:0]                  o_active_entry_route,
    output reg  [31:0]                  o_active_entry_flow,
    output wire                         o_switch_safe
);

reg initialized_reg = 1'b0;
reg window_active_reg = 1'b0;

wire [63:0] eval_start_time_ns = {i_word_start_hi, i_word_start_lo};
wire [63:0] eval_end_time_ns = {i_word_end_hi, i_word_end_lo};
wire [7:0] eval_flags = i_word_route[7:0];

wire eval_entry_valid = (eval_flags & `DNI_FLAG_VALID) != 0;
wire eval_entry_channel_enable = (eval_flags & CHANNEL_FLAG_MASK) != 0;
wire eval_entry_armed = eval_entry_valid && eval_entry_channel_enable;
wire eval_entry_started = eval_entry_armed && i_ptp_time_ns >= eval_start_time_ns;
wire eval_entry_active = eval_entry_started && i_ptp_time_ns < eval_end_time_ns;
wire eval_entry_retire = eval_entry_armed && i_ptp_time_ns >= eval_end_time_ns;
wire eval_entry_completion_event = (eval_flags & `DNI_FLAG_COMPLETION_EVENT) != 0;
wire [ENTRY_INDEX_WIDTH-1:0] next_entry_ptr = o_current_entry_ptr + 1'b1;

assign o_switch_safe = !window_active_reg || eval_entry_retire || !eval_entry_armed;

always @(posedge clk) begin
    if (rst) begin
        initialized_reg <= 1'b0;
        window_active_reg <= 1'b0;

        o_current_window_id <= 64'd0;
        o_current_entry_ptr <= {ENTRY_INDEX_WIDTH{1'b0}};
        o_window_open_pulse <= 1'b0;
        o_window_close_pulse <= 1'b0;
        o_commit_start_pulse <= 1'b0;
        o_exec_valid <= 1'b0;
        o_channel_allowed <= 1'b0;
        o_window_active <= 1'b0;
        o_target_port <= 8'd0;
        o_target_queue <= 16'd0;
        o_app_id <= 8'd0;
        o_plane_id <= 8'd0;
        o_opcode <= 8'd0;
        o_context_id <= 16'd0;
        o_dst_node_id <= 16'd0;
        o_flow_id <= 16'd0;
        o_active_entry_start_time_ns <= 64'd0;
        o_active_entry_end_time_ns <= 64'd0;
        o_active_entry_meta <= 32'd0;
        o_active_entry_route <= 32'd0;
        o_active_entry_flow <= 32'd0;
    end else begin
        o_window_open_pulse <= 1'b0;
        o_window_close_pulse <= 1'b0;
        o_commit_start_pulse <= 1'b0;

        if (!i_enable) begin
            initialized_reg <= 1'b0;
            window_active_reg <= 1'b0;
            o_current_entry_ptr <= {ENTRY_INDEX_WIDTH{1'b0}};
            o_exec_valid <= 1'b0;
            o_channel_allowed <= 1'b0;
            o_window_active <= 1'b0;
            o_target_port <= 8'd0;
            o_target_queue <= 16'd0;
            o_app_id <= 8'd0;
            o_plane_id <= 8'd0;
            o_opcode <= 8'd0;
            o_context_id <= 16'd0;
            o_dst_node_id <= 16'd0;
            o_flow_id <= 16'd0;
            o_active_entry_start_time_ns <= 64'd0;
            o_active_entry_end_time_ns <= 64'd0;
            o_active_entry_meta <= 32'd0;
            o_active_entry_route <= 32'd0;
            o_active_entry_flow <= 32'd0;
        end else if (i_restart_pulse) begin
            initialized_reg <= 1'b1;
            window_active_reg <= 1'b0;
            o_current_entry_ptr <= {ENTRY_INDEX_WIDTH{1'b0}};
            o_exec_valid <= 1'b0;
            o_channel_allowed <= 1'b0;
            o_window_active <= 1'b0;
            o_target_port <= 8'd0;
            o_target_queue <= 16'd0;
            o_app_id <= 8'd0;
            o_plane_id <= 8'd0;
            o_opcode <= 8'd0;
            o_context_id <= 16'd0;
            o_dst_node_id <= 16'd0;
            o_flow_id <= 16'd0;
            o_active_entry_start_time_ns <= 64'd0;
            o_active_entry_end_time_ns <= 64'd0;
            o_active_entry_meta <= 32'd0;
            o_active_entry_route <= 32'd0;
            o_active_entry_flow <= 32'd0;
        end else if (!initialized_reg) begin
            initialized_reg <= 1'b1;
            window_active_reg <= 1'b0;
            o_current_entry_ptr <= {ENTRY_INDEX_WIDTH{1'b0}};
            o_exec_valid <= 1'b0;
            o_channel_allowed <= 1'b0;
            o_window_active <= 1'b0;
        end else begin
            o_active_entry_start_time_ns <= eval_start_time_ns;
            o_active_entry_end_time_ns <= eval_end_time_ns;
            o_active_entry_meta <= i_word_meta;
            o_active_entry_route <= i_word_route;
            o_active_entry_flow <= i_word_flow;

            if (!window_active_reg && eval_entry_active) begin
                window_active_reg <= 1'b1;
                o_current_window_id <= o_current_window_id + 1'b1;
                o_window_open_pulse <= 1'b1;
                o_exec_valid <= 1'b1;
                o_channel_allowed <= 1'b1;
                o_window_active <= 1'b1;
                o_target_port <= i_word_route[15:8];
                o_target_queue <= i_word_route[31:16];
                o_app_id <= {4'd0, i_word_meta[3:0]};
                o_plane_id <= {4'd0, i_word_meta[7:4]};
                o_opcode <= i_word_meta[15:8];
                o_context_id <= i_word_meta[31:16];
                o_dst_node_id <= i_word_flow[31:16];
                o_flow_id <= i_word_flow[15:0];
            end else if (window_active_reg && eval_entry_retire) begin
                window_active_reg <= 1'b0;
                o_window_close_pulse <= 1'b1;
                o_commit_start_pulse <= eval_entry_completion_event;
                o_exec_valid <= eval_entry_armed;
                o_channel_allowed <= 1'b0;
                o_window_active <= 1'b0;
                o_target_port <= i_word_route[15:8];
                o_target_queue <= i_word_route[31:16];
                o_app_id <= {4'd0, i_word_meta[3:0]};
                o_plane_id <= {4'd0, i_word_meta[7:4]};
                o_opcode <= i_word_meta[15:8];
                o_context_id <= i_word_meta[31:16];
                o_dst_node_id <= i_word_flow[31:16];
                o_flow_id <= i_word_flow[15:0];
                o_current_entry_ptr <= next_entry_ptr;
            end else begin
                o_exec_valid <= eval_entry_armed;
                o_channel_allowed <= window_active_reg && eval_entry_active;
                o_window_active <= window_active_reg && eval_entry_active;
                if (eval_entry_armed) begin
                    o_target_port <= i_word_route[15:8];
                    o_target_queue <= i_word_route[31:16];
                    o_app_id <= {4'd0, i_word_meta[3:0]};
                    o_plane_id <= {4'd0, i_word_meta[7:4]};
                    o_opcode <= i_word_meta[15:8];
                    o_context_id <= i_word_meta[31:16];
                    o_dst_node_id <= i_word_flow[31:16];
                    o_flow_id <= i_word_flow[15:0];
                end else begin
                    o_target_port <= 8'd0;
                    o_target_queue <= 16'd0;
                    o_app_id <= 8'd0;
                    o_plane_id <= 8'd0;
                    o_opcode <= 8'd0;
                    o_context_id <= 16'd0;
                    o_dst_node_id <= 16'd0;
                    o_flow_id <= 16'd0;
                end
            end
        end
    end
end

endmodule

`default_nettype wire
