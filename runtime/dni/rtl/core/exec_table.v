`timescale 1ns / 1ps
`default_nettype none

/*
 * Banked execution-table storage for the DNI.
 *
 * This module owns the TX and RX execution-table images, host-side table
 * programming access, and current-entry word readout for the active bank.
 */

module exec_table #(
    parameter integer TX_ENTRY_INDEX_WIDTH = 10,
    parameter integer TX_ENTRY_COUNT = 1024,
    parameter integer RX_ENTRY_INDEX_WIDTH = 9,
    parameter integer RX_ENTRY_COUNT = 512,
    parameter integer ENTRY_WORDS = 8
) (
    input  wire                            clk,
    input  wire                            rst,

    input  wire                            i_active_bank,

    input  wire                            cfg_tx_wr_en,
    input  wire                            cfg_tx_wr_bank,
    input  wire [TX_ENTRY_INDEX_WIDTH-1:0] cfg_tx_wr_entry,
    input  wire [2:0]                      cfg_tx_wr_word,
    input  wire [31:0]                     cfg_tx_wr_data,
    input  wire                            cfg_tx_rd_bank,
    input  wire [TX_ENTRY_INDEX_WIDTH-1:0] cfg_tx_rd_entry,
    input  wire [2:0]                      cfg_tx_rd_word,
    output reg  [31:0]                     cfg_tx_rd_data,

    input  wire                            cfg_rx_wr_en,
    input  wire                            cfg_rx_wr_bank,
    input  wire [RX_ENTRY_INDEX_WIDTH-1:0] cfg_rx_wr_entry,
    input  wire [2:0]                      cfg_rx_wr_word,
    input  wire [31:0]                     cfg_rx_wr_data,
    input  wire                            cfg_rx_rd_bank,
    input  wire [RX_ENTRY_INDEX_WIDTH-1:0] cfg_rx_rd_entry,
    input  wire [2:0]                      cfg_rx_rd_word,
    output reg  [31:0]                     cfg_rx_rd_data,

    input  wire [TX_ENTRY_INDEX_WIDTH-1:0] i_tx_eval_entry,
    input  wire [RX_ENTRY_INDEX_WIDTH-1:0] i_rx_eval_entry,

    output wire [31:0]                     o_tx_word_start_lo,
    output wire [31:0]                     o_tx_word_start_hi,
    output wire [31:0]                     o_tx_word_end_lo,
    output wire [31:0]                     o_tx_word_end_hi,
    output wire [31:0]                     o_tx_word_meta,
    output wire [31:0]                     o_tx_word_route,
    output wire [31:0]                     o_tx_word_flow,

    output wire [31:0]                     o_rx_word_start_lo,
    output wire [31:0]                     o_rx_word_start_hi,
    output wire [31:0]                     o_rx_word_end_lo,
    output wire [31:0]                     o_rx_word_end_hi,
    output wire [31:0]                     o_rx_word_meta,
    output wire [31:0]                     o_rx_word_route,
    output wire [31:0]                     o_rx_word_flow
);

localparam integer TX_MEM_WORDS = TX_ENTRY_COUNT * ENTRY_WORDS * 2;
localparam integer RX_MEM_WORDS = RX_ENTRY_COUNT * ENTRY_WORDS * 2;

localparam integer WORD_START_LO = 0;
localparam integer WORD_START_HI = 1;
localparam integer WORD_END_LO   = 2;
localparam integer WORD_END_HI   = 3;
localparam integer WORD_META     = 4;
localparam integer WORD_ROUTE    = 5;
localparam integer WORD_FLOW     = 6;

reg [31:0] tx_exec_mem[0:TX_MEM_WORDS-1];
reg [31:0] rx_exec_mem[0:RX_MEM_WORDS-1];

integer idx;

function integer tx_mem_index;
    input integer bank;
    input integer entry;
    input integer word;
    begin
        tx_mem_index = ((bank * TX_ENTRY_COUNT) + entry) * ENTRY_WORDS + word;
    end
endfunction

function integer rx_mem_index;
    input integer bank;
    input integer entry;
    input integer word;
    begin
        rx_mem_index = ((bank * RX_ENTRY_COUNT) + entry) * ENTRY_WORDS + word;
    end
endfunction

always @(*) begin
    if (cfg_tx_rd_entry < TX_ENTRY_COUNT) begin
        cfg_tx_rd_data = tx_exec_mem[tx_mem_index(cfg_tx_rd_bank, cfg_tx_rd_entry, cfg_tx_rd_word)];
    end else begin
        cfg_tx_rd_data = 32'd0;
    end
end

always @(*) begin
    if (cfg_rx_rd_entry < RX_ENTRY_COUNT) begin
        cfg_rx_rd_data = rx_exec_mem[rx_mem_index(cfg_rx_rd_bank, cfg_rx_rd_entry, cfg_rx_rd_word)];
    end else begin
        cfg_rx_rd_data = 32'd0;
    end
end

assign o_tx_word_start_lo = tx_exec_mem[tx_mem_index(i_active_bank, i_tx_eval_entry, WORD_START_LO)];
assign o_tx_word_start_hi = tx_exec_mem[tx_mem_index(i_active_bank, i_tx_eval_entry, WORD_START_HI)];
assign o_tx_word_end_lo   = tx_exec_mem[tx_mem_index(i_active_bank, i_tx_eval_entry, WORD_END_LO)];
assign o_tx_word_end_hi   = tx_exec_mem[tx_mem_index(i_active_bank, i_tx_eval_entry, WORD_END_HI)];
assign o_tx_word_meta     = tx_exec_mem[tx_mem_index(i_active_bank, i_tx_eval_entry, WORD_META)];
assign o_tx_word_route    = tx_exec_mem[tx_mem_index(i_active_bank, i_tx_eval_entry, WORD_ROUTE)];
assign o_tx_word_flow     = tx_exec_mem[tx_mem_index(i_active_bank, i_tx_eval_entry, WORD_FLOW)];

assign o_rx_word_start_lo = rx_exec_mem[rx_mem_index(i_active_bank, i_rx_eval_entry, WORD_START_LO)];
assign o_rx_word_start_hi = rx_exec_mem[rx_mem_index(i_active_bank, i_rx_eval_entry, WORD_START_HI)];
assign o_rx_word_end_lo   = rx_exec_mem[rx_mem_index(i_active_bank, i_rx_eval_entry, WORD_END_LO)];
assign o_rx_word_end_hi   = rx_exec_mem[rx_mem_index(i_active_bank, i_rx_eval_entry, WORD_END_HI)];
assign o_rx_word_meta     = rx_exec_mem[rx_mem_index(i_active_bank, i_rx_eval_entry, WORD_META)];
assign o_rx_word_route    = rx_exec_mem[rx_mem_index(i_active_bank, i_rx_eval_entry, WORD_ROUTE)];
assign o_rx_word_flow     = rx_exec_mem[rx_mem_index(i_active_bank, i_rx_eval_entry, WORD_FLOW)];

always @(posedge clk) begin
    if (rst) begin
        for (idx = 0; idx < TX_MEM_WORDS; idx = idx + 1) begin
            tx_exec_mem[idx] <= 32'd0;
        end
        for (idx = 0; idx < RX_MEM_WORDS; idx = idx + 1) begin
            rx_exec_mem[idx] <= 32'd0;
        end
    end else begin
        if (cfg_tx_wr_en && cfg_tx_wr_entry < TX_ENTRY_COUNT) begin
            tx_exec_mem[tx_mem_index(cfg_tx_wr_bank, cfg_tx_wr_entry, cfg_tx_wr_word)] <= cfg_tx_wr_data;
        end
        if (cfg_rx_wr_en && cfg_rx_wr_entry < RX_ENTRY_COUNT) begin
            rx_exec_mem[rx_mem_index(cfg_rx_wr_bank, cfg_rx_wr_entry, cfg_rx_wr_word)] <= cfg_rx_wr_data;
        end
    end
end

endmodule

`default_nettype wire
