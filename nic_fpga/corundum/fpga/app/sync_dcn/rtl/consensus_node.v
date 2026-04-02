`timescale 1ns / 1ps

module consensus_node #(
    // node parameters
    parameter integer   P_NODE_ID               = 0,
    parameter integer   P_NODE_COUNT            = 3,    // we support up to 5 nodes

    // AXI & Ethernet parameters
    parameter integer   P_ETHERNET_TYPE         = 16'h88B5,
    parameter [47:0]    P_NODE_MAC_ADDR         = 48'h00_0a_35_06_50_94,
    parameter integer   P_LOG_ITEM_LEN          = 40,      // 40 bytes default
    parameter integer   P_NODE_ID_WIDTH         = 8,
    parameter integer   P_KV_WIDTH              = 8,
    parameter integer   P_AXIS_DATA_WIDTH       = 512,
    parameter integer   P_AXIS_KEEP_WIDTH       = P_AXIS_DATA_WIDTH/8,
    parameter integer   P_AXIS_TX_USER_WIDTH     = 1,
    parameter integer   P_AXIS_RX_USER_WIDTH     = 1,
    parameter integer   P_AXIS_USER_WIDTH        = P_AXIS_TX_USER_WIDTH, // For consensus core
    parameter integer   P_TX_TAG_WIDTH          = 16,
    
    // Header byte offsets (from start of Ethernet frame)
    parameter integer   P_HDR_ETHERTYPE_OFFSET  = 12,
    parameter integer   P_HDR_WINDOW_ID_OFFSET  = 14,
    parameter integer   P_HDR_NODE_ID_OFFSET    = 22,
    parameter integer   P_HDR_KV_OFFSET         = 23,
    parameter integer   P_HDR_PAYLOAD_OFFSET    = 24,
    // Destination MAC table (for up to 5 nodes) and broadcast MAC
    parameter [47:0]    P_DEST_MAC_0 = 48'h00_0a_35_06_50_94,
    parameter [47:0]    P_DEST_MAC_1 = 48'h00_0a_35_06_09_24,
    parameter [47:0]    P_DEST_MAC_2 = 48'h00_0a_35_06_0b_84,
    parameter [47:0]    P_DEST_MAC_3 = 48'h00_0a_35_06_09_3c,
    parameter [47:0]    P_DEST_MAC_4 = 48'h00_0a_35_06_0b_72,
    parameter [47:0]    P_BROADCAST_MAC = 48'hFF_FF_FF_FF_FF_FF
) (
    // clock and reset
    input wire                                          clk,
    input wire                                          rst_n,

    // External timing comes from the schedule executor so the consensus logic
    // no longer hardcodes a local window formula.  This is the hook we need for
    // compiled SDCN schedules and hitless schedule-table updates.
    //
    // Current implementation note:
    // one logical consensus round should currently be represented by one
    // execution window.  The TX/RX helpers encode and check
    // i_current_window_id on the wire, so splitting TX and RX across separate
    // execution windows would change the protocol round identifier.
    input wire                                          i_enable,           // enable the consensus module
    input wire                                          i_clear_halt,
    input wire [63:0]                                   i_current_window_id,
    input wire                                          i_window_open_pulse,
    input wire                                          i_commit_start_pulse,
    input wire                                          i_window_close_pulse,
    input wire                                          i_tx_allowed,
    input wire                                          i_rx_enabled,
    //----------------------------------------------------------------
    //      Network Interface (Consensus Traffic)
    //----------------------------------------------------------------
    // TX AXI Stream (Output to MAC)
    output wire [P_AXIS_DATA_WIDTH-1:0]                 m_axis_mac_tx_tdata,
    output wire [P_AXIS_KEEP_WIDTH-1:0]                 m_axis_mac_tx_tkeep,
    output wire                                         m_axis_mac_tx_tvalid,
    output wire                                         m_axis_mac_tx_tlast,
    output wire [P_AXIS_TX_USER_WIDTH-1:0]              m_axis_mac_tx_tuser,
    input wire                                          m_axis_mac_tx_tready,

    // RX AXI Stream (Input from MAC)
    input wire [P_AXIS_DATA_WIDTH-1:0]                  s_axis_mac_rx_tdata,
    input wire [P_AXIS_KEEP_WIDTH-1:0]                  s_axis_mac_rx_tkeep,
    input wire                                          s_axis_mac_rx_tvalid,
    input wire                                          s_axis_mac_rx_tlast,
    input wire [P_AXIS_RX_USER_WIDTH-1:0]               s_axis_mac_rx_tuser,
    output wire                                         s_axis_mac_rx_tready,

    //----------------------------------------------------------------
    //      Host Interface (Client Request and Commit Log Output)
    //----------------------------------------------------------------
    // Host request Input 
    input wire [P_AXIS_DATA_WIDTH-1:0]                  s_axis_host_req_data,
    input wire [P_AXIS_KEEP_WIDTH-1:0]                  s_axis_host_req_keep,
    input wire                                          s_axis_host_req_valid,
    input wire                                          s_axis_host_req_last,
    output wire                                         s_axis_host_req_ready,

    // Commit log Output
    output wire [P_AXIS_DATA_WIDTH-1:0]                 m_axis_host_commit_data,
    output wire [P_AXIS_KEEP_WIDTH-1:0]                 m_axis_host_commit_keep,
    output wire                                         m_axis_host_commit_valid,
    output wire                                         m_axis_host_commit_last,
    input wire                                          m_axis_host_commit_ready,
    output wire                                         o_system_halt,        // high when system halts
    output wire [3:0]                                   o_debug_state
);

// Host request/commit plumbing is intentionally left stubbed for now.
// The current milestone focuses on hardware-timed network execution first.
assign s_axis_host_req_ready = 1'b0;

// Tie off host commit outputs and debug state for now
assign m_axis_host_commit_data  = {P_AXIS_DATA_WIDTH{1'b0}};
assign m_axis_host_commit_keep  = {P_AXIS_KEEP_WIDTH{1'b0}};
assign m_axis_host_commit_valid = 1'b0;
assign m_axis_host_commit_last  = 1'b0;
assign o_debug_state            = 4'b0000;

//------------------------------------------------
//   1. Interface Interconnections
//------------------------------------------------

// RX -> Core
wire            w_rx_valid;
wire [P_NODE_ID_WIDTH-1:0]      w_rx_node_id;
wire [P_KV_WIDTH-1:0]           w_rx_knowledge_vec;
wire [P_LOG_ITEM_LEN*8-1:0]     w_rx_payload;

// Core -> TX
wire [P_NODE_COUNT-1:0]        w_tx_knowledge_vec;
wire [P_LOG_ITEM_LEN*8-1:0]    w_tx_propose;

// Core outputs
wire [P_LOG_ITEM_LEN*8*P_NODE_COUNT-1:0]    w_commit_log;
wire [P_NODE_COUNT-1:0]                     w_commit_valid;

// User -> TX
wire [P_NODE_COUNT-1:0]        i_tx_payload_vec; // user provided payload vector

//------------------------------------------------
//   2. Module Instantiations
//------------------------------------------------

// The consensus core consumes timing pulses that are already aligned to the
// global schedule.  This keeps protocol logic independent from PHC math and
// lets the same core run under different schedule policies later on.
consensus_core #(
    .P_NODE_ID(P_NODE_ID),
    .P_NODE_COUNT(P_NODE_COUNT),
    .P_LOG_ITEM_LEN(P_LOG_ITEM_LEN),
    .P_NODE_ID_WIDTH(P_NODE_ID_WIDTH),
    .P_KV_WIDTH(P_KV_WIDTH)
) consensus_core_inst (
    .clk(clk),
    .rst_n(rst_n),
    .i_clear_halt(i_clear_halt),
    .i_current_window_id(i_current_window_id),
    .i_window_open_pulse(i_window_open_pulse && i_enable),
    .i_commit_start_pulse(i_commit_start_pulse),
    .i_window_close_pulse(i_window_close_pulse && i_enable),

    .i_rx_valid(w_rx_valid),
    .i_rx_node_id(w_rx_node_id[P_NODE_ID_WIDTH-1:0]),
    .i_rx_knowledge_vec(w_rx_knowledge_vec[P_KV_WIDTH-1:0]),
    .i_rx_propose(w_rx_payload[P_LOG_ITEM_LEN*8-1:0]),

    .o_system_halt(o_system_halt),

    .o_tx_knowledge_vec(w_tx_knowledge_vec),
    .o_tx_propose(w_tx_propose),

    .o_commit_log(w_commit_log),
    .o_commit_valid(w_commit_valid)
);

// The packet generator only needs to know "which execution window am I in?" and
// "may I transmit now?".  The exact timing policy is fully delegated to the
// shared schedule executor.
consensus_tx #(
    .P_NODE_COUNT(P_NODE_COUNT),
    .P_NODE_ID(P_NODE_ID),

    .P_AXIS_DATA_WIDTH(P_AXIS_DATA_WIDTH),
    .P_AXIS_KEEP_WIDTH(P_AXIS_KEEP_WIDTH),
    .P_AXIS_USER_WIDTH(P_AXIS_TX_USER_WIDTH),

    .P_ETHERNET_TYPE(P_ETHERNET_TYPE),
    .P_LOG_ITEM_LEN(P_LOG_ITEM_LEN),
    .P_SRC_MAC(P_NODE_MAC_ADDR),
    .P_NODE_ID_WIDTH(P_NODE_ID_WIDTH),
    .P_KV_WIDTH(P_KV_WIDTH),
    .P_HDR_ETHERTYPE_OFFSET(P_HDR_ETHERTYPE_OFFSET),
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
) consensus_tx_inst (
    .clk(clk),
    .rst_n(rst_n),

    .i_timing_current_window_id(i_current_window_id),
    .i_timing_window_open_pulse(i_window_open_pulse && i_enable),
    .i_timing_tx_allowed(i_tx_allowed && i_enable),

    .i_core_knowledge_vec(w_tx_knowledge_vec),
    .i_core_propose(w_tx_propose),

    .m_axis_mac_tx_tdata(m_axis_mac_tx_tdata),
    .m_axis_mac_tx_tkeep(m_axis_mac_tx_tkeep),
    .m_axis_mac_tx_tvalid(m_axis_mac_tx_tvalid),
    .m_axis_mac_tx_tready(m_axis_mac_tx_tready),
    .m_axis_mac_tx_tlast(m_axis_mac_tx_tlast),
    .m_axis_mac_tx_tuser(m_axis_mac_tx_tuser)
);

// RX admission is also enforced by the shared schedule executor.  Packets that
// arrive outside the allowed receive window are dropped before they can
// perturb protocol state.
consensus_rx #(
    .P_NODE_COUNT(P_NODE_COUNT),
    .P_NODE_ID(P_NODE_ID),

    .P_AXIS_DATA_WIDTH(P_AXIS_DATA_WIDTH),
    .P_AXIS_KEEP_WIDTH(P_AXIS_KEEP_WIDTH),
    .P_AXIS_USER_WIDTH(P_AXIS_RX_USER_WIDTH),

    .P_ETHERNET_TYPE(P_ETHERNET_TYPE),
    .P_LOG_ITEM_LEN(P_LOG_ITEM_LEN),
    .P_KV_WIDTH(P_KV_WIDTH),
    .P_HDR_ETHERTYPE_OFFSET(P_HDR_ETHERTYPE_OFFSET),
    .P_HDR_WINDOW_ID_OFFSET(P_HDR_WINDOW_ID_OFFSET),
    .P_HDR_NODE_ID_OFFSET(P_HDR_NODE_ID_OFFSET),
    .P_HDR_KV_OFFSET(P_HDR_KV_OFFSET),
    .P_HDR_PAYLOAD_OFFSET(P_HDR_PAYLOAD_OFFSET)
) consensus_rx_inst (
    .clk(clk),
    .rst_n(rst_n),

    .i_timing_current_window_id(i_current_window_id),
    .i_timing_rx_enabled(i_rx_enabled && i_enable),

    .s_axis_mac_rx_tdata(s_axis_mac_rx_tdata),
    .s_axis_mac_rx_tkeep(s_axis_mac_rx_tkeep),
    .s_axis_mac_rx_tvalid(s_axis_mac_rx_tvalid),
    .s_axis_mac_rx_tlast(s_axis_mac_rx_tlast),
    .s_axis_mac_rx_tuser(s_axis_mac_rx_tuser),
    .s_axis_mac_rx_tready(s_axis_mac_rx_tready),

    .o_rx_valid(w_rx_valid),
    .o_rx_node_id(w_rx_node_id),
    .o_rx_knowledge_vec(w_rx_knowledge_vec),
    .o_rx_payload(w_rx_payload)
);

endmodule
