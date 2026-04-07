`timescale 1ns / 1ps
`default_nettype none

/*
 * Processor runtime boundary for the DNI.
 *
 * This module consumes scheduler-issued window control and presents the
 * endpoint-local processor side of the DNI.  It exposes:
 * - one unified processor TX boundary to the communication datapath
 * - one unified processor RX boundary from the communication datapath
 * - a standardized per-app control/data boundary for local stand-in engines
 *
 * The current prototype uses local FPGA engines as stand-ins for a future
 * processor-facing runtime contract.  Those engines live outside this module
 * and connect through the standardized app-slot interface below.
 */
module processor_runtime #(
    parameter integer AXIS_DATA_WIDTH = 512,
    parameter integer AXIS_KEEP_WIDTH = AXIS_DATA_WIDTH/8,
    parameter integer AXIS_TX_USER_WIDTH = 1,
    parameter integer AXIS_RX_USER_WIDTH = 1,
    parameter integer APP_COUNT = 1,
    parameter integer APP_SLOT_WIDTH = (APP_COUNT > 1 ? $clog2(APP_COUNT) : 1)
) (
    input  wire                                     clk,
    input  wire                                     rst,
    input  wire                                     i_enable,

    // Scheduler -> runtime control
    input  wire [63:0]                              i_tx_current_window_id,
    input  wire                                     i_tx_window_open_pulse,
    input  wire                                     i_tx_commit_start_pulse,
    input  wire                                     i_tx_window_close_pulse,
    input  wire                                     i_tx_allowed,
    input  wire [7:0]                               i_tx_app_id,
    input  wire [7:0]                               i_tx_opcode,
    input  wire [15:0]                              i_tx_context_id,

    input  wire [63:0]                              i_rx_current_window_id,
    input  wire                                     i_rx_window_open_pulse,
    input  wire                                     i_rx_commit_start_pulse,
    input  wire                                     i_rx_window_close_pulse,
    input  wire                                     i_rx_enabled,
    input  wire [7:0]                               i_rx_app_id,
    input  wire [7:0]                               i_rx_opcode,
    input  wire [15:0]                              i_rx_context_id,

    // Unified processor-owned RX stream from the communication datapath
    input  wire [AXIS_DATA_WIDTH-1:0]               s_axis_processor_rx_tdata,
    input  wire [AXIS_KEEP_WIDTH-1:0]               s_axis_processor_rx_tkeep,
    input  wire                                     s_axis_processor_rx_tvalid,
    input  wire                                     s_axis_processor_rx_tlast,
    input  wire [AXIS_RX_USER_WIDTH-1:0]            s_axis_processor_rx_tuser,
    output reg                                      s_axis_processor_rx_tready,

    // Unified processor-owned TX stream toward the communication datapath
    output reg  [AXIS_DATA_WIDTH-1:0]               m_axis_processor_tx_tdata,
    output reg  [AXIS_KEEP_WIDTH-1:0]               m_axis_processor_tx_tkeep,
    output reg                                      m_axis_processor_tx_tvalid,
    input  wire                                     m_axis_processor_tx_tready,
    output reg                                      m_axis_processor_tx_tlast,
    output reg  [AXIS_TX_USER_WIDTH-1:0]            m_axis_processor_tx_tuser,
    output wire                                     o_processor_tx_valid,

    // Standardized per-app control fanout
    output wire [APP_COUNT*64-1:0]                  o_app_tx_window_id,
    output wire [APP_COUNT-1:0]                     o_app_tx_window_open_pulse,
    output wire [APP_COUNT-1:0]                     o_app_tx_commit_start_pulse,
    output wire [APP_COUNT-1:0]                     o_app_tx_window_close_pulse,
    output wire [APP_COUNT-1:0]                     o_app_tx_allowed,
    output wire [APP_COUNT-1:0]                     o_app_tx_active,
    output wire [APP_COUNT*8-1:0]                   o_app_tx_opcode,
    output wire [APP_COUNT*16-1:0]                  o_app_tx_context_id,

    output wire [APP_COUNT*64-1:0]                  o_app_rx_window_id,
    output wire [APP_COUNT-1:0]                     o_app_rx_window_open_pulse,
    output wire [APP_COUNT-1:0]                     o_app_rx_commit_start_pulse,
    output wire [APP_COUNT-1:0]                     o_app_rx_window_close_pulse,
    output wire [APP_COUNT-1:0]                     o_app_rx_enabled,
    output wire [APP_COUNT-1:0]                     o_app_rx_active,
    output wire [APP_COUNT*8-1:0]                   o_app_rx_opcode,
    output wire [APP_COUNT*16-1:0]                  o_app_rx_context_id,

    // App -> runtime TX streams
    input  wire [APP_COUNT*AXIS_DATA_WIDTH-1:0]     s_axis_app_tx_tdata,
    input  wire [APP_COUNT*AXIS_KEEP_WIDTH-1:0]     s_axis_app_tx_tkeep,
    input  wire [APP_COUNT-1:0]                     s_axis_app_tx_tvalid,
    input  wire [APP_COUNT-1:0]                     s_axis_app_tx_tlast,
    input  wire [APP_COUNT*AXIS_TX_USER_WIDTH-1:0]  s_axis_app_tx_tuser,
    output reg  [APP_COUNT-1:0]                     s_axis_app_tx_tready,

    // Runtime -> app RX streams
    output reg  [APP_COUNT*AXIS_DATA_WIDTH-1:0]     m_axis_app_rx_tdata,
    output reg  [APP_COUNT*AXIS_KEEP_WIDTH-1:0]     m_axis_app_rx_tkeep,
    output reg  [APP_COUNT-1:0]                     m_axis_app_rx_tvalid,
    output reg  [APP_COUNT-1:0]                     m_axis_app_rx_tlast,
    output reg  [APP_COUNT*AXIS_RX_USER_WIDTH-1:0]  m_axis_app_rx_tuser,
    input  wire [APP_COUNT-1:0]                     m_axis_app_rx_tready
);

wire tx_app_selected = i_enable && i_tx_allowed && (i_tx_app_id != 8'd0) && (i_tx_app_id <= APP_COUNT);
wire rx_app_selected = i_enable && i_rx_enabled && (i_rx_app_id != 8'd0) && (i_rx_app_id <= APP_COUNT);

wire [APP_SLOT_WIDTH-1:0] tx_app_slot = i_tx_app_id[APP_SLOT_WIDTH-1:0] - 1'b1;
wire [APP_SLOT_WIDTH-1:0] rx_app_slot = i_rx_app_id[APP_SLOT_WIDTH-1:0] - 1'b1;

assign o_processor_tx_valid = m_axis_processor_tx_tvalid;

genvar app_idx;
generate
    for (app_idx = 0; app_idx < APP_COUNT; app_idx = app_idx + 1) begin : app_slots
        assign o_app_tx_window_id[app_idx*64 +: 64] = i_tx_current_window_id;
        assign o_app_tx_window_open_pulse[app_idx] = i_tx_window_open_pulse && tx_app_selected && (tx_app_slot == app_idx);
        assign o_app_tx_commit_start_pulse[app_idx] = i_tx_commit_start_pulse && tx_app_selected && (tx_app_slot == app_idx);
        assign o_app_tx_window_close_pulse[app_idx] = i_tx_window_close_pulse && tx_app_selected && (tx_app_slot == app_idx);
        assign o_app_tx_allowed[app_idx] = i_tx_allowed && tx_app_selected && (tx_app_slot == app_idx);
        assign o_app_tx_active[app_idx] = tx_app_selected && (tx_app_slot == app_idx);
        assign o_app_tx_opcode[app_idx*8 +: 8] = i_tx_opcode;
        assign o_app_tx_context_id[app_idx*16 +: 16] = i_tx_context_id;

        assign o_app_rx_window_id[app_idx*64 +: 64] = i_rx_current_window_id;
        assign o_app_rx_window_open_pulse[app_idx] = i_rx_window_open_pulse && rx_app_selected && (rx_app_slot == app_idx);
        assign o_app_rx_commit_start_pulse[app_idx] = i_rx_commit_start_pulse && rx_app_selected && (rx_app_slot == app_idx);
        assign o_app_rx_window_close_pulse[app_idx] = i_rx_window_close_pulse && rx_app_selected && (rx_app_slot == app_idx);
        assign o_app_rx_enabled[app_idx] = i_rx_enabled && rx_app_selected && (rx_app_slot == app_idx);
        assign o_app_rx_active[app_idx] = rx_app_selected && (rx_app_slot == app_idx);
        assign o_app_rx_opcode[app_idx*8 +: 8] = i_rx_opcode;
        assign o_app_rx_context_id[app_idx*16 +: 16] = i_rx_context_id;
    end
endgenerate

always @(*) begin
    m_axis_processor_tx_tdata = {AXIS_DATA_WIDTH{1'b0}};
    m_axis_processor_tx_tkeep = {AXIS_KEEP_WIDTH{1'b0}};
    m_axis_processor_tx_tvalid = 1'b0;
    m_axis_processor_tx_tlast = 1'b0;
    m_axis_processor_tx_tuser = {AXIS_TX_USER_WIDTH{1'b0}};
    s_axis_app_tx_tready = {APP_COUNT{1'b0}};

    if (tx_app_selected) begin
        m_axis_processor_tx_tdata = s_axis_app_tx_tdata[tx_app_slot*AXIS_DATA_WIDTH +: AXIS_DATA_WIDTH];
        m_axis_processor_tx_tkeep = s_axis_app_tx_tkeep[tx_app_slot*AXIS_KEEP_WIDTH +: AXIS_KEEP_WIDTH];
        m_axis_processor_tx_tvalid = s_axis_app_tx_tvalid[tx_app_slot];
        m_axis_processor_tx_tlast = s_axis_app_tx_tlast[tx_app_slot];
        m_axis_processor_tx_tuser = s_axis_app_tx_tuser[tx_app_slot*AXIS_TX_USER_WIDTH +: AXIS_TX_USER_WIDTH];
        s_axis_app_tx_tready[tx_app_slot] = m_axis_processor_tx_tready;
    end
end

always @(*) begin
    m_axis_app_rx_tdata = {APP_COUNT*AXIS_DATA_WIDTH{1'b0}};
    m_axis_app_rx_tkeep = {APP_COUNT*AXIS_KEEP_WIDTH{1'b0}};
    m_axis_app_rx_tvalid = {APP_COUNT{1'b0}};
    m_axis_app_rx_tlast = {APP_COUNT{1'b0}};
    m_axis_app_rx_tuser = {APP_COUNT*AXIS_RX_USER_WIDTH{1'b0}};
    s_axis_processor_rx_tready = 1'b1;

    if (rx_app_selected) begin
        m_axis_app_rx_tdata[rx_app_slot*AXIS_DATA_WIDTH +: AXIS_DATA_WIDTH] = s_axis_processor_rx_tdata;
        m_axis_app_rx_tkeep[rx_app_slot*AXIS_KEEP_WIDTH +: AXIS_KEEP_WIDTH] = s_axis_processor_rx_tkeep;
        m_axis_app_rx_tvalid[rx_app_slot] = s_axis_processor_rx_tvalid;
        m_axis_app_rx_tlast[rx_app_slot] = s_axis_processor_rx_tlast;
        m_axis_app_rx_tuser[rx_app_slot*AXIS_RX_USER_WIDTH +: AXIS_RX_USER_WIDTH] = s_axis_processor_rx_tuser;
        s_axis_processor_rx_tready = m_axis_app_rx_tready[rx_app_slot];
    end
end

endmodule

`default_nettype wire
