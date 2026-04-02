`timescale 1ns / 1ps
`default_nettype none
`include "fpga/app/sync_dcn/rtl/common/sync_dcn_defs.vh"

/*
 * Single-direction compiled schedule executor core.
 *
 * This block owns one banked execution table and one local entry pointer.  It
 * does not decide when schedule banks flip; that policy lives in the wrapper
 * so TX and RX channels can switch banks together.  The core therefore focuses
 * only on:
 *   1. storing one channel's execution-table image
 *   2. decoding the current local entry against synchronized time
 *   3. opening and closing one channel-local execution window
 */
module sync_schedule_channel_core #(
    parameter integer ENTRY_INDEX_WIDTH = 10,
    parameter integer ENTRY_COUNT = 1024,
    parameter [7:0] CHANNEL_FLAG_MASK = `SYNC_DCN_FLAG_TX_ENABLE
) (
    input  wire                         clk,
    input  wire                         rst,

    input  wire                         i_enable,
    input  wire [63:0]                  i_ptp_time_ns,
    input  wire                         i_active_bank,
    input  wire                         i_restart_pulse,

    input  wire                         cfg_wr_en,
    input  wire                         cfg_wr_bank,
    input  wire [ENTRY_INDEX_WIDTH-1:0] cfg_wr_entry,
    input  wire [2:0]                   cfg_wr_word,
    input  wire [31:0]                  cfg_wr_data,

    input  wire                         cfg_rd_bank,
    input  wire [ENTRY_INDEX_WIDTH-1:0] cfg_rd_entry,
    input  wire [2:0]                   cfg_rd_word,
    output reg  [31:0]                  cfg_rd_data,

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

localparam integer ENTRY_WORDS = 8;
localparam integer MEM_WORDS = ENTRY_COUNT*ENTRY_WORDS*2;

localparam integer WORD_START_LO = 0;
localparam integer WORD_START_HI = 1;
localparam integer WORD_END_LO   = 2;
localparam integer WORD_END_HI   = 3;
localparam integer WORD_META     = 4;
localparam integer WORD_ROUTE    = 5;
localparam integer WORD_FLOW     = 6;

reg [31:0] exec_mem[0:MEM_WORDS-1];

reg initialized_reg = 1'b0;
reg window_active_reg = 1'b0;

integer idx;

function integer mem_index;
    input integer bank;
    input integer entry;
    input integer word;
    begin
        mem_index = ((bank*ENTRY_COUNT) + entry)*ENTRY_WORDS + word;
    end
endfunction

wire [31:0] eval_word_start_lo = exec_mem[mem_index(i_active_bank, o_current_entry_ptr, WORD_START_LO)];
wire [31:0] eval_word_start_hi = exec_mem[mem_index(i_active_bank, o_current_entry_ptr, WORD_START_HI)];
wire [31:0] eval_word_end_lo = exec_mem[mem_index(i_active_bank, o_current_entry_ptr, WORD_END_LO)];
wire [31:0] eval_word_end_hi = exec_mem[mem_index(i_active_bank, o_current_entry_ptr, WORD_END_HI)];
wire [31:0] eval_word_meta = exec_mem[mem_index(i_active_bank, o_current_entry_ptr, WORD_META)];
wire [31:0] eval_word_route = exec_mem[mem_index(i_active_bank, o_current_entry_ptr, WORD_ROUTE)];
wire [31:0] eval_word_flow = exec_mem[mem_index(i_active_bank, o_current_entry_ptr, WORD_FLOW)];

wire [63:0] eval_start_time_ns = {eval_word_start_hi, eval_word_start_lo};
wire [63:0] eval_end_time_ns = {eval_word_end_hi, eval_word_end_lo};
wire [7:0] eval_flags = eval_word_route[7:0];

wire eval_entry_valid = (eval_flags & `SYNC_DCN_FLAG_VALID) != 0;
wire eval_entry_channel_enable = (eval_flags & CHANNEL_FLAG_MASK) != 0;
wire eval_entry_armed = eval_entry_valid && eval_entry_channel_enable;
wire eval_entry_started = eval_entry_armed && i_ptp_time_ns >= eval_start_time_ns;
wire eval_entry_active = eval_entry_started && i_ptp_time_ns < eval_end_time_ns;
wire eval_entry_retire = eval_entry_armed && i_ptp_time_ns >= eval_end_time_ns;
wire eval_entry_completion_event = (eval_flags & `SYNC_DCN_FLAG_COMPLETION_EVENT) != 0;
wire [ENTRY_INDEX_WIDTH-1:0] next_entry_ptr = o_current_entry_ptr + 1'b1;

assign o_switch_safe = !window_active_reg || eval_entry_retire || !eval_entry_armed;

always @(*) begin
    if (cfg_rd_entry < ENTRY_COUNT) begin
        cfg_rd_data = exec_mem[mem_index(cfg_rd_bank, cfg_rd_entry, cfg_rd_word)];
    end else begin
        cfg_rd_data = 32'd0;
    end
end

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

        for (idx = 0; idx < MEM_WORDS; idx = idx + 1) begin
            exec_mem[idx] <= 32'd0;
        end
    end else begin
        o_window_open_pulse <= 1'b0;
        o_window_close_pulse <= 1'b0;
        o_commit_start_pulse <= 1'b0;

        if (cfg_wr_en && cfg_wr_entry < ENTRY_COUNT) begin
            exec_mem[mem_index(cfg_wr_bank, cfg_wr_entry, cfg_wr_word)] <= cfg_wr_data;
        end

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
            o_active_entry_meta <= eval_word_meta;
            o_active_entry_route <= eval_word_route;
            o_active_entry_flow <= eval_word_flow;

            if (!window_active_reg && eval_entry_active) begin
                window_active_reg <= 1'b1;
                o_current_window_id <= o_current_window_id + 1'b1;
                o_window_open_pulse <= 1'b1;
                o_exec_valid <= 1'b1;
                o_channel_allowed <= 1'b1;
                o_window_active <= 1'b1;
                o_target_port <= eval_word_route[15:8];
                o_target_queue <= eval_word_route[31:16];
                o_app_id <= {4'd0, eval_word_meta[3:0]};
                o_plane_id <= {4'd0, eval_word_meta[7:4]};
                o_opcode <= eval_word_meta[15:8];
                o_context_id <= eval_word_meta[31:16];
                o_dst_node_id <= eval_word_flow[31:16];
                o_flow_id <= eval_word_flow[15:0];
            end else if (window_active_reg && eval_entry_retire) begin
                window_active_reg <= 1'b0;
                o_window_close_pulse <= 1'b1;
                o_commit_start_pulse <= eval_entry_completion_event;
                o_exec_valid <= eval_entry_armed;
                o_channel_allowed <= 1'b0;
                o_window_active <= 1'b0;
                o_target_port <= eval_word_route[15:8];
                o_target_queue <= eval_word_route[31:16];
                o_app_id <= {4'd0, eval_word_meta[3:0]};
                o_plane_id <= {4'd0, eval_word_meta[7:4]};
                o_opcode <= eval_word_meta[15:8];
                o_context_id <= eval_word_meta[31:16];
                o_dst_node_id <= eval_word_flow[31:16];
                o_flow_id <= eval_word_flow[15:0];
                o_current_entry_ptr <= next_entry_ptr;
            end else begin
                o_exec_valid <= eval_entry_armed;
                o_channel_allowed <= window_active_reg && eval_entry_active;
                o_window_active <= window_active_reg && eval_entry_active;
                if (eval_entry_armed) begin
                    o_target_port <= eval_word_route[15:8];
                    o_target_queue <= eval_word_route[31:16];
                    o_app_id <= {4'd0, eval_word_meta[3:0]};
                    o_plane_id <= {4'd0, eval_word_meta[7:4]};
                    o_opcode <= eval_word_meta[15:8];
                    o_context_id <= eval_word_meta[31:16];
                    o_dst_node_id <= eval_word_flow[31:16];
                    o_flow_id <= eval_word_flow[15:0];
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
