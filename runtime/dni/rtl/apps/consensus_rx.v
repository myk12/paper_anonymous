`timescale 1ns / 1ps

module consensus_rx #(
    parameter P_NODE_COUNT  = 3,
    parameter P_NODE_ID     = 0,

    parameter P_AXIS_DATA_WIDTH = 512,
    parameter P_AXIS_KEEP_WIDTH = P_AXIS_DATA_WIDTH / 8,
    parameter P_AXIS_USER_WIDTH = 1,

    parameter P_ETHERNET_TYPE = 16'h88B5,

    // Protocol parameters
    parameter integer P_LOG_ITEM_LEN        = 40, // bytes
    parameter integer P_KV_WIDTH            = 8,

    // Header byte offsets (from start of Ethernet frame)
    parameter integer   P_HDR_ETHERTYPE_OFFSET = 12,
    parameter integer   P_HDR_WINDOW_ID_OFFSET = 14,
    parameter integer   P_HDR_NODE_ID_OFFSET   = 22,
    parameter integer   P_HDR_KV_OFFSET        = 23,
    parameter integer   P_HDR_PAYLOAD_OFFSET   = 24
) (
    // clock and reset
    input wire                              clk,
    input wire                              rst_n,

    // Control Signals from Scheduler
    input wire                              i_timing_rx_enabled,
    input wire [63:0]                       i_timing_current_window_id,

    // AXI Stream Slave Input
    input wire [P_AXIS_DATA_WIDTH-1:0]      s_axis_mac_rx_tdata,
    input wire [P_AXIS_KEEP_WIDTH-1:0]      s_axis_mac_rx_tkeep,
    input wire                              s_axis_mac_rx_tvalid,
    input wire [P_AXIS_USER_WIDTH-1:0]      s_axis_mac_rx_tuser,
    input wire                              s_axis_mac_rx_tlast,
    output wire                             s_axis_mac_rx_tready,

    // Parsed Output to Consensus Module
    output reg                              o_rx_valid,     // high when a valid packet is parsed
    output reg [7:0]                        o_rx_node_id,   // node ID extracted from packet
    output reg [P_KV_WIDTH-1:0]             o_rx_knowledge_vec, // knowledge vector extracted from packet
    output reg [P_LOG_ITEM_LEN*8-1:0]       o_rx_payload    // payload extracted from packet
);

//------------------------------------------------
//         Interface Logic
//------------------------------------------------
// The consensus model must run at the line rate of incoming packets.
// Therefore, we assume that the AXI Stream input is always ready to accept data.
assign s_axis_mac_rx_tready = 1'b1; // Always ready to accept data

//------------------------------------------------
//         Packet Parsing Logic
//------------------------------------------------
// swap helper functions
function [15:0] swap16(input [15:0] in);
    swap16 = {in[7:0], in[15:8]};
endfunction

function [63:0] swap64(input [63:0] in);
    swap64 = {in[7:0], in[15:8], in[23:16], in[31:24],
               in[39:32], in[47:40], in[55:48], in[63:56]};
endfunction

// Feilds
wire [15:0] w_ethertype_net = s_axis_mac_rx_tdata[P_HDR_ETHERTYPE_OFFSET*8 +: 16];
wire [15:0] w_ethertype     =   swap16(w_ethertype_net);

wire [63:0] w_window_id_net = s_axis_mac_rx_tdata[P_HDR_WINDOW_ID_OFFSET*8 +: 64];
wire [63:0] w_rx_window_id  = swap64(w_window_id_net);

wire [7:0] w_rx_node_id     = s_axis_mac_rx_tdata[P_HDR_NODE_ID_OFFSET*8 +: 8];

wire [P_KV_WIDTH-1:0] w_rx_knowledge_vec = s_axis_mac_rx_tdata[P_HDR_KV_OFFSET*8 +: P_KV_WIDTH];

localparam integer P_PAYLOAD_WIDTH = P_LOG_ITEM_LEN*8;
wire [P_PAYLOAD_WIDTH-1:0] w_rx_payload_net = s_axis_mac_rx_tdata[P_HDR_PAYLOAD_OFFSET*8 +: P_PAYLOAD_WIDTH];

wire [P_PAYLOAD_WIDTH-1:0] w_rx_payload;
generate
    if ((P_LOG_ITEM_LEN % 8) == 0) begin : payload_swap
        localparam integer NWORDS = P_LOG_ITEM_LEN/8;
        wire [P_PAYLOAD_WIDTH-1:0] w_swapped;
        genvar wi;
        for (wi = 0; wi < NWORDS; wi = wi + 1) begin : be_swap
            wire [63:0] word_in;
            wire [63:0] word_out;
            assign word_in = w_rx_payload_net[wi*64 +: 64];
            assign word_out = swap64(word_in);
            assign w_swapped[wi*64 +: 64] = word_out;
        end
        assign w_rx_payload = w_swapped;
    end else begin : payload_noswap
        // No swap for non-64-bit-multiple payloads
        assign w_rx_payload = w_rx_payload_net;
    end
endgenerate

//------------------------------------------------
//         Flitering Logic
//------------------------------------------------
reg r_packet_valid;

always @(*) begin
    r_packet_valid = 0;

    // Basic AXI Stream validity
    if (s_axis_mac_rx_tvalid && s_axis_mac_rx_tlast) begin
        // Check Ethertype
        if (w_ethertype == P_ETHERNET_TYPE) begin
            // Check that the frame belongs to the currently active window.
            if (w_rx_window_id == i_timing_current_window_id) begin
                // Check Node ID within range
                if (w_rx_node_id < P_NODE_COUNT) begin
                    r_packet_valid = 1'b1;
                end
            end
        end
    end
end

//------------------------------------------------
//         Output Logic
//------------------------------------------------
always @(posedge clk) begin
    if (!rst_n) begin
        o_rx_valid <= 0;
        o_rx_node_id <= 0;
        o_rx_knowledge_vec <= 0;
        o_rx_payload <= 0;
    end else if (!i_timing_rx_enabled) begin
        o_rx_valid <= 0;
        o_rx_node_id <= 0;
        o_rx_knowledge_vec <= 0;
        o_rx_payload <= 0;
    end else begin
        o_rx_valid <= r_packet_valid;
        if (r_packet_valid) begin
            o_rx_node_id <= w_rx_node_id;
            o_rx_knowledge_vec <= w_rx_knowledge_vec;
            o_rx_payload <= w_rx_payload;
        end else begin
            o_rx_node_id <= 0;
            o_rx_knowledge_vec <= 0;
            o_rx_payload <= 0;
        end
    end
end

endmodule
