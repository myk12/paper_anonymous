`timescale 1ns / 1ps
`default_nettype none
`include "fpga/app/sync_dcn/rtl/common/sync_dcn_defs.vh"

/*
 * Sync-DCN application cluster.
 *
 * This module is the first level of decomposition under the future SDCN
 * subsystem: it hosts the individual application engines as siblings and keeps
 * application-specific logic out of the generic datapath wrapper.
 *
 * Current contents:
 * - consensus_node: protocol engine for scheduled control traffic
 * - ai_trace_replay: synthetic AI bulk-traffic engine with a minimal RX sink
 * - sync_app_tx_dispatch: selects the active TX stream based on the compiled
 *   execution instruction produced by the schedule executor
 * - sync_app_rx_dispatch: demultiplexes generic app RX traffic inside the app
 *   cluster so the datapath only needs a clean host-vs-app boundary
 */
module sync_dcn_apps #(
    parameter               P_NODE_ID       = 0,
    parameter               PTP_TS_WIDTH    = 96,
    parameter               AXIS_DATA_WIDTH     = 512,
    parameter               AXIS_KEEP_WIDTH     = AXIS_DATA_WIDTH/8,
    parameter               AXIS_TX_USER_WIDTH  = 1,
    parameter               AXIS_RX_USER_WIDTH  = 1,
    parameter               AXIS_USER_WIDTH     = AXIS_TX_USER_WIDTH,
    parameter               TX_TAG_WIDTH        = 16,
    parameter [15:0]        P_CONSENSUS_ETHERTYPE        = 16'h88B5,
    parameter integer       P_HDR_ETHERTYPE_OFFSET_BYTES = 12,
    parameter integer       P_LOG_ITEM_LEN               = 40,
    parameter [47:0]        P_NODE_MAC_ADDR              = 48'h00_0a_35_06_50_94,
    parameter integer       P_NODE_ID_WIDTH              = 8,
    parameter integer       P_KV_WIDTH                   = 8,
    parameter integer       P_HDR_WINDOW_ID_OFFSET       = 14,
    parameter integer       P_HDR_NODE_ID_OFFSET         = 22,
    parameter integer       P_HDR_KV_OFFSET              = 23,
    parameter integer       P_HDR_PAYLOAD_OFFSET         = 24,
    parameter [47:0]        P_DEST_MAC_0                 = 48'h00_0a_35_06_50_94,
    parameter [47:0]        P_DEST_MAC_1                 = 48'h00_0a_35_06_09_24,
    parameter [47:0]        P_DEST_MAC_2                 = 48'h00_0a_35_06_0b_84,
    parameter [47:0]        P_DEST_MAC_3                 = 48'h00_0a_35_06_09_3c,
    parameter [47:0]        P_DEST_MAC_4                 = 48'h00_0a_35_06_0b_72,
    parameter [47:0]        P_BROADCAST_MAC              = 48'hFF_FF_FF_FF_FF_FF
) (
    // Global signals
    input  wire                                 clk,
    input  wire                                 rst,
    input  wire                                 i_enable,

    // TX-side application control/status signals from the schedule executor
    input  wire [63:0]                          i_tx_current_window_id,
    input  wire                                 i_tx_window_open_pulse,
    input  wire                                 i_tx_commit_start_pulse,
    input  wire                                 i_tx_window_close_pulse,
    input  wire                                 i_tx_allowed,
    input  wire [7:0]                           i_tx_app_id,
    input  wire [7:0]                           i_tx_opcode,
    input  wire [15:0]                          i_tx_context_id,

    // RX-side application control/status signals from the schedule executor
    input  wire [63:0]                          i_rx_current_window_id,
    input  wire                                 i_rx_window_open_pulse,
    input  wire                                 i_rx_commit_start_pulse,
    input  wire                                 i_rx_window_close_pulse,
    input  wire                                 i_rx_enabled,
    input  wire [7:0]                           i_rx_app_id,
    input  wire [7:0]                           i_rx_opcode,
    input  wire [15:0]                          i_rx_context_id,

    // Consensus app configuration and status
    input  wire                                 i_consensus_enable,
    input  wire                                 i_consensus_clear_halt,
    output wire                                 o_consensus_system_halt,
    output wire [3:0]                           o_consensus_debug_state,

    // AI trace replay configuration interface
    input  wire                                 i_ai_enable,
    input  wire                                 i_ai_cfg_wr_en,
    input  wire [9:0]                           i_ai_cfg_wr_entry,
    input  wire [2:0]                           i_ai_cfg_wr_word,
    input  wire [31:0]                          i_ai_cfg_wr_data,
    input  wire [9:0]                           i_ai_cfg_rd_entry,
    input  wire [2:0]                           i_ai_cfg_rd_word,
    output wire [31:0]                          o_ai_cfg_rd_data,
    output wire [31:0]                          o_ai_pkt_sent_count,
    output wire [31:0]                          o_ai_rx_pkt_count,
    output wire [31:0]                          o_ai_rx_byte_count,
    output wire [31:0]                          o_ai_rx_match_count,
    output wire [31:0]                          o_ai_rx_drop_count,

    // Generic RX stream for all application-owned traffic.  The app cluster
    // demultiplexes this stream internally so the datapath does not need to
    // know about individual apps.
    input  wire [AXIS_DATA_WIDTH-1:0]           s_axis_app_rx_tdata,
    input  wire [AXIS_KEEP_WIDTH-1:0]           s_axis_app_rx_tkeep,
    input  wire                                 s_axis_app_rx_tvalid,
    input  wire                                 s_axis_app_rx_tlast,
    input  wire [AXIS_RX_USER_WIDTH-1:0]        s_axis_app_rx_tuser,
    output wire                                 s_axis_app_rx_tready,

    // Selected TX stream from the currently active app.
    output wire [AXIS_DATA_WIDTH-1:0]           m_axis_app_tx_tdata,
    output wire [AXIS_KEEP_WIDTH-1:0]           m_axis_app_tx_tkeep,
    output wire                                 m_axis_app_tx_tvalid,
    input  wire                                 m_axis_app_tx_tready,
    output wire                                 m_axis_app_tx_tlast,
    output wire [AXIS_TX_USER_WIDTH-1:0]        m_axis_app_tx_tuser,
    output wire                                 o_app_tx_valid
);

wire [AXIS_DATA_WIDTH-1:0]      consensus_tx_tdata;
wire [AXIS_KEEP_WIDTH-1:0]      consensus_tx_tkeep;
wire                            consensus_tx_tvalid;
wire                            consensus_tx_tlast;
wire [AXIS_TX_USER_WIDTH-1:0]   consensus_tx_tuser;
wire                            consensus_tx_tready;

wire [AXIS_DATA_WIDTH-1:0]      ai_replay_tx_tdata;
wire [AXIS_KEEP_WIDTH-1:0]      ai_replay_tx_tkeep;
wire                            ai_replay_tx_tvalid;
wire                            ai_replay_tx_tlast;
wire [AXIS_TX_USER_WIDTH-1:0]   ai_replay_tx_tuser;
wire                            ai_replay_tx_tready;
wire [AXIS_DATA_WIDTH-1:0]      consensus_rx_tdata;
wire [AXIS_KEEP_WIDTH-1:0]      consensus_rx_tkeep;
wire                            consensus_rx_tvalid;
wire                            consensus_rx_tlast;
wire [AXIS_RX_USER_WIDTH-1:0]   consensus_rx_tuser;
wire                            consensus_rx_tready;
wire [AXIS_DATA_WIDTH-1:0]      ai_replay_rx_tdata;
wire [AXIS_KEEP_WIDTH-1:0]      ai_replay_rx_tkeep;
wire                            ai_replay_rx_tvalid;
wire                            ai_replay_rx_tlast;
wire [AXIS_RX_USER_WIDTH-1:0]   ai_replay_rx_tuser;
wire                            ai_replay_rx_tready;

wire [AXIS_DATA_WIDTH-1:0]      host_req_data = {AXIS_DATA_WIDTH{1'b0}};
wire [AXIS_KEEP_WIDTH-1:0]      host_req_keep = {AXIS_KEEP_WIDTH{1'b0}};
wire                            host_req_valid = 1'b0;
wire                            host_req_last = 1'b0;
wire                            host_req_ready;

wire [AXIS_DATA_WIDTH-1:0]      host_commit_data;
wire [AXIS_KEEP_WIDTH-1:0]      host_commit_keep;
wire                            host_commit_valid;
wire                            host_commit_last;
wire                            host_commit_ready = 1'b0;
    sync_app_rx_dispatch #(
        .AXIS_DATA_WIDTH(AXIS_DATA_WIDTH),
        .AXIS_KEEP_WIDTH(AXIS_KEEP_WIDTH),
        .AXIS_RX_USER_WIDTH(AXIS_RX_USER_WIDTH),
        .P_CONSENSUS_ETHERTYPE(P_CONSENSUS_ETHERTYPE),
        .P_AI_ETHERTYPE(16'h88B6),
        .P_HDR_ETHERTYPE_OFFSET_BYTES(P_HDR_ETHERTYPE_OFFSET_BYTES)
    )
    sync_app_rx_dispatch_inst (
        .clk(clk),
        .rst(rst),
        .s_axis_app_rx_tdata(s_axis_app_rx_tdata),
        .s_axis_app_rx_tkeep(s_axis_app_rx_tkeep),
        .s_axis_app_rx_tvalid(s_axis_app_rx_tvalid),
        .s_axis_app_rx_tlast(s_axis_app_rx_tlast),
        .s_axis_app_rx_tuser(s_axis_app_rx_tuser),
        .s_axis_app_rx_tready(s_axis_app_rx_tready),
        .m_axis_cons_rx_tdata(consensus_rx_tdata),
        .m_axis_cons_rx_tkeep(consensus_rx_tkeep),
        .m_axis_cons_rx_tvalid(consensus_rx_tvalid),
        .m_axis_cons_rx_tlast(consensus_rx_tlast),
        .m_axis_cons_rx_tuser(consensus_rx_tuser),
        .m_axis_cons_rx_tready(consensus_rx_tready),
        .m_axis_ai_rx_tdata(ai_replay_rx_tdata),
        .m_axis_ai_rx_tkeep(ai_replay_rx_tkeep),
        .m_axis_ai_rx_tvalid(ai_replay_rx_tvalid),
        .m_axis_ai_rx_tlast(ai_replay_rx_tlast),
        .m_axis_ai_rx_tuser(ai_replay_rx_tuser),
        .m_axis_ai_rx_tready(ai_replay_rx_tready)
    );

    consensus_node #(
        .P_NODE_ID(P_NODE_ID),
        .P_NODE_COUNT(3),
        .P_ETHERNET_TYPE(P_CONSENSUS_ETHERTYPE),
        .P_NODE_MAC_ADDR(P_NODE_MAC_ADDR),
        .P_LOG_ITEM_LEN(P_LOG_ITEM_LEN),
        .P_AXIS_DATA_WIDTH(AXIS_DATA_WIDTH),
        .P_AXIS_KEEP_WIDTH(AXIS_KEEP_WIDTH),
        .P_AXIS_TX_USER_WIDTH(AXIS_TX_USER_WIDTH),
        .P_AXIS_RX_USER_WIDTH(AXIS_RX_USER_WIDTH),
        .P_NODE_ID_WIDTH(P_NODE_ID_WIDTH),
        .P_KV_WIDTH(P_KV_WIDTH),
        .P_HDR_ETHERTYPE_OFFSET(P_HDR_ETHERTYPE_OFFSET_BYTES),
        .P_HDR_WINDOW_ID_OFFSET(P_HDR_WINDOW_ID_OFFSET),
        .P_HDR_NODE_ID_OFFSET(P_HDR_NODE_ID_OFFSET),
        .P_HDR_KV_OFFSET(P_HDR_KV_OFFSET),
        .P_HDR_PAYLOAD_OFFSET(P_HDR_PAYLOAD_OFFSET),
        .P_DEST_MAC_0(P_DEST_MAC_0),
        .P_DEST_MAC_1(P_DEST_MAC_1),
        .P_DEST_MAC_2(P_DEST_MAC_2),
        .P_DEST_MAC_3(P_DEST_MAC_3),
        .P_DEST_MAC_4(P_DEST_MAC_4),
        .P_BROADCAST_MAC(P_BROADCAST_MAC)
    )
    consensus_node_inst (
        .clk(clk),
        .rst_n(!rst),
        .i_enable(i_enable && i_consensus_enable),
        .i_clear_halt(i_consensus_clear_halt),
        .i_current_window_id(i_tx_current_window_id),
        .i_window_open_pulse(i_tx_window_open_pulse),
        .i_commit_start_pulse(i_tx_commit_start_pulse),
        .i_window_close_pulse(i_tx_window_close_pulse),
        .i_tx_allowed(i_tx_allowed),
        .i_rx_enabled(i_rx_enabled && i_rx_app_id == `SYNC_DCN_APP_CONSENSUS),
        .m_axis_mac_tx_tdata(consensus_tx_tdata),
        .m_axis_mac_tx_tkeep(consensus_tx_tkeep),
        .m_axis_mac_tx_tvalid(consensus_tx_tvalid),
        .m_axis_mac_tx_tlast(consensus_tx_tlast),
        .m_axis_mac_tx_tuser(consensus_tx_tuser),
        .m_axis_mac_tx_tready(consensus_tx_tready),
        .s_axis_mac_rx_tdata(consensus_rx_tdata),
        .s_axis_mac_rx_tkeep(consensus_rx_tkeep),
        .s_axis_mac_rx_tvalid(consensus_rx_tvalid),
        .s_axis_mac_rx_tlast(consensus_rx_tlast),
        .s_axis_mac_rx_tuser(consensus_rx_tuser),
        .s_axis_mac_rx_tready(consensus_rx_tready),
        .s_axis_host_req_data(host_req_data),
        .s_axis_host_req_keep(host_req_keep),
        .s_axis_host_req_valid(host_req_valid),
        .s_axis_host_req_last(host_req_last),
        .s_axis_host_req_ready(host_req_ready),
        .m_axis_host_commit_data(host_commit_data),
        .m_axis_host_commit_keep(host_commit_keep),
        .m_axis_host_commit_valid(host_commit_valid),
        .m_axis_host_commit_last(host_commit_last),
        .m_axis_host_commit_ready(host_commit_ready),
        .o_system_halt(o_consensus_system_halt),
        .o_debug_state(o_consensus_debug_state)
    );

    ai_trace_replay #(
        .AXIS_DATA_WIDTH(AXIS_DATA_WIDTH),
        .AXIS_KEEP_WIDTH(AXIS_KEEP_WIDTH),
        .AXIS_TX_USER_WIDTH(AXIS_TX_USER_WIDTH),
        .AXIS_RX_USER_WIDTH(AXIS_RX_USER_WIDTH),
        .TRACE_INDEX_WIDTH(10),
        .P_SRC_MAC(P_NODE_MAC_ADDR)
    )
    ai_trace_replay_inst (
        .clk(clk),
        .rst(rst),
        .i_enable(i_enable),
        .i_tx_current_window_id(i_tx_current_window_id),
        .i_tx_window_open_pulse(i_tx_window_open_pulse),
        .i_tx_window_active(i_tx_app_id == `SYNC_DCN_APP_AI_REPLAY && i_tx_opcode == `SYNC_DCN_OP_AI_TX),
        .i_tx_allowed(i_tx_allowed),
        .i_tx_context_id(i_tx_context_id),
        .i_rx_current_window_id(i_rx_current_window_id),
        .i_rx_window_open_pulse(i_rx_window_open_pulse),
        .i_rx_window_active(i_rx_app_id == `SYNC_DCN_APP_AI_REPLAY && i_rx_opcode == `SYNC_DCN_OP_AI_RX),
        .i_rx_enabled(i_rx_enabled),
        .i_rx_context_id(i_rx_context_id),
        .cfg_enable(i_ai_enable),
        .cfg_wr_en(i_ai_cfg_wr_en),
        .cfg_wr_entry(i_ai_cfg_wr_entry),
        .cfg_wr_word(i_ai_cfg_wr_word),
        .cfg_wr_data(i_ai_cfg_wr_data),
        .cfg_rd_entry(i_ai_cfg_rd_entry),
        .cfg_rd_word(i_ai_cfg_rd_word),
        .cfg_rd_data(o_ai_cfg_rd_data),
        .o_pkt_sent_count(o_ai_pkt_sent_count),
        .o_rx_pkt_count(o_ai_rx_pkt_count),
        .o_rx_byte_count(o_ai_rx_byte_count),
        .o_rx_match_count(o_ai_rx_match_count),
        .o_rx_drop_count(o_ai_rx_drop_count),
        .m_axis_tx_tdata(ai_replay_tx_tdata),
        .m_axis_tx_tkeep(ai_replay_tx_tkeep),
        .m_axis_tx_tvalid(ai_replay_tx_tvalid),
        .m_axis_tx_tready(ai_replay_tx_tready),
        .m_axis_tx_tlast(ai_replay_tx_tlast),
        .m_axis_tx_tuser(ai_replay_tx_tuser),
        .s_axis_rx_tdata(ai_replay_rx_tdata),
        .s_axis_rx_tkeep(ai_replay_rx_tkeep),
        .s_axis_rx_tvalid(ai_replay_rx_tvalid),
        .s_axis_rx_tlast(ai_replay_rx_tlast),
        .s_axis_rx_tuser(ai_replay_rx_tuser),
        .s_axis_rx_tready(ai_replay_rx_tready)
    );

    sync_app_tx_dispatch #(
        .AXIS_DATA_WIDTH(AXIS_DATA_WIDTH),
        .AXIS_KEEP_WIDTH(AXIS_KEEP_WIDTH),
        .AXIS_TX_USER_WIDTH(AXIS_TX_USER_WIDTH)
    )
    sync_app_tx_dispatch_inst (
        .i_app_valid(i_tx_allowed),
        .i_app_id(i_tx_app_id),
        .i_opcode(i_tx_opcode),
        .s_axis_cons_tx_tdata(consensus_tx_tdata),
        .s_axis_cons_tx_tkeep(consensus_tx_tkeep),
        .s_axis_cons_tx_tvalid(consensus_tx_tvalid),
        .s_axis_cons_tx_tlast(consensus_tx_tlast),
        .s_axis_cons_tx_tuser(consensus_tx_tuser),
        .s_axis_cons_tx_tready(consensus_tx_tready),
        .s_axis_ai_tx_tdata(ai_replay_tx_tdata),
        .s_axis_ai_tx_tkeep(ai_replay_tx_tkeep),
        .s_axis_ai_tx_tvalid(ai_replay_tx_tvalid),
        .s_axis_ai_tx_tlast(ai_replay_tx_tlast),
        .s_axis_ai_tx_tuser(ai_replay_tx_tuser),
        .s_axis_ai_tx_tready(ai_replay_tx_tready),
        .m_axis_tx_tdata(m_axis_app_tx_tdata),
        .m_axis_tx_tkeep(m_axis_app_tx_tkeep),
        .m_axis_tx_tvalid(m_axis_app_tx_tvalid),
        .m_axis_tx_tlast(m_axis_app_tx_tlast),
        .m_axis_tx_tuser(m_axis_app_tx_tuser),
        .m_axis_tx_tready(m_axis_app_tx_tready),
        .o_app_tx_valid(o_app_tx_valid)
    );

endmodule

`default_nettype wire
