`timescale 1ns / 1ps

module consensus_tx #(
    parameter integer   P_NODE_ID = 0,
    parameter integer   P_NODE_COUNT = 3,

    parameter integer   P_AXIS_DATA_WIDTH = 512,
    parameter integer   P_AXIS_KEEP_WIDTH = P_AXIS_DATA_WIDTH / 8,
    parameter integer   P_AXIS_USER_WIDTH = 1,

    parameter integer   P_LOG_ITEM_LEN = 40, // bytes
    parameter [47:0]    P_SRC_MAC = 48'h02_00_00_00_00_00,
    parameter [15:0]    P_ETHERNET_TYPE = 16'h88B5,

    // Protocol field widths
    parameter integer   P_NODE_ID_WIDTH = 8,
    parameter integer   P_KV_WIDTH      = 8,

    // Header byte offsets (from start of Ethernet frame)
    parameter integer   P_HDR_ETHERTYPE_OFFSET = 12,
    parameter integer   P_HDR_WINDOW_ID_OFFSET = 14,
    parameter integer   P_HDR_NODE_ID_OFFSET   = 22,
    parameter integer   P_HDR_KV_OFFSET        = 23,
    parameter integer   P_HDR_PAYLOAD_OFFSET   = 24,

    // Destination MAC table (for up to 5 nodes)
    parameter [47:0]    P_DEST_MAC_0 = 48'h00_0a_35_06_50_94,
    parameter [47:0]    P_DEST_MAC_1 = 48'h00_0a_35_06_09_24,
    parameter [47:0]    P_DEST_MAC_2 = 48'h00_0a_35_06_0b_84,
    parameter [47:0]    P_DEST_MAC_3 = 48'h00_0a_35_06_09_3c,
    parameter [47:0]    P_DEST_MAC_4 = 48'h00_0a_35_06_0b_72,
    parameter [47:0]    P_BROADCAST_MAC = 48'hFF_FF_FF_FF_FF_FF
) (
    // clock and reset
    input wire                              clk,
    input wire                              rst_n,

    // Timing signals from the shared schedule executor
    input wire                              i_timing_tx_allowed,
    input wire                              i_timing_window_open_pulse,
    input wire [63:0]                       i_timing_current_window_id,

    // Data Inputs
    input wire [P_NODE_COUNT-1:0]           i_core_knowledge_vec,
    input wire [P_LOG_ITEM_LEN*8-1:0]       i_core_propose,

    // AXI Stream Master Output
    output reg [P_AXIS_DATA_WIDTH-1:0]      m_axis_mac_tx_tdata,
    output reg [P_AXIS_KEEP_WIDTH-1:0]      m_axis_mac_tx_tkeep,
    output reg                              m_axis_mac_tx_tvalid,
    output reg                              m_axis_mac_tx_tlast,
    output reg [P_AXIS_USER_WIDTH-1:0]      m_axis_mac_tx_tuser,
    input wire                              m_axis_mac_tx_tready
);

// Parameter checks (simulation-time)
initial begin
    if (P_NODE_COUNT > 5) begin
        $error("consensus_tx: P_NODE_COUNT > 5 requires extended MAC mapping");
    end
    if ((P_LOG_ITEM_LEN % 8) != 0) begin
        $error("consensus_tx: P_LOG_ITEM_LEN must be a multiple of 8 bytes for 64-bit endian swap");
    end
end

//------------------------------------------------
//         Endianess Conversion
//------------------------------------------------
// helper function for byte swapping
function [15:0] to_big_endian_16(input [15:0] in);
    to_big_endian_16 = {in[7:0], in[15:8]};
endfunction

function [63:0] to_big_endian_64(input [63:0] in);
    to_big_endian_64 = {in[7:0], in[15:8], in[23:16], in[31:24],
                       in[39:32], in[47:40], in[55:48], in[63:56]};
endfunction

//------------------------------------------------
//           parameter Definitions
//------------------------------------------------
localparam  S_IDLE           = 2'b00;
localparam  S_WAITING        = 2'b01;
localparam  S_BROADCAST      = 2'b10;
reg [1:0]   state;
reg [7:0]   r_target_node_id;

// MAC address
reg [47:0]  v_dest_mac;

always @(*) begin
    // Default value
    v_dest_mac = P_BROADCAST_MAC; // Broadcast MAC

    // Select destination MAC based on destination node ID
    case (r_target_node_id)
        0: v_dest_mac = P_DEST_MAC_0;
        1: v_dest_mac = P_DEST_MAC_1;
        2: v_dest_mac = P_DEST_MAC_2;
        3: v_dest_mac = P_DEST_MAC_3;
        4: v_dest_mac = P_DEST_MAC_4;
        default: v_dest_mac = P_BROADCAST_MAC; // Broadcast MAC
    endcase
end

//------------------------------------------------
//         Packet Construction (Single Cycle)
//------------------------------------------------
// Construct packet flit
// Packet format:
// [ Ethernet Header ]
//   - Destination MAC (48 bits)
//   - Source MAC (48 bits)
//   - Ethertype (16 bits)
// [ Consensus Header ]
//  - Window ID (64 bits)
//  - Node ID (8 bits)
//  - Knowledge Vector (8 bits)
//  - Payload (40 bytes)

reg [P_AXIS_DATA_WIDTH-1:0]      v_packet_flit;
always @(*) begin
    v_packet_flit = {P_AXIS_DATA_WIDTH{1'b0}};

    // ------- Ethernet Header -------
    v_packet_flit[0*8 +: 8]       = v_dest_mac[47:40];
    v_packet_flit[1*8 +: 8]       = v_dest_mac[39:32];
    v_packet_flit[2*8 +: 8]       = v_dest_mac[31:24];
    v_packet_flit[3*8 +: 8]       = v_dest_mac[23:16];
    v_packet_flit[4*8 +: 8]       = v_dest_mac[15:8];
    v_packet_flit[5*8 +: 8]       = v_dest_mac[7:0];

    v_packet_flit[6*8 +: 8]       = P_SRC_MAC[47:40];
    v_packet_flit[7*8 +: 8]       = P_SRC_MAC[39:32];
    v_packet_flit[8*8 +: 8]       = P_SRC_MAC[31:24];
    v_packet_flit[9*8 +: 8]       = P_SRC_MAC[23:16];
    v_packet_flit[10*8 +: 8]      = P_SRC_MAC[15:8];
    v_packet_flit[11*8 +: 8]      = P_SRC_MAC[7:0];

    v_packet_flit[P_HDR_ETHERTYPE_OFFSET*8 +: 16] = to_big_endian_16(P_ETHERNET_TYPE);

    // ------- Consensus Header -------
    v_packet_flit[P_HDR_WINDOW_ID_OFFSET*8 +: 64]  = to_big_endian_64(i_timing_current_window_id);
    v_packet_flit[P_HDR_NODE_ID_OFFSET*8 +: P_NODE_ID_WIDTH] = P_NODE_ID[P_NODE_ID_WIDTH-1:0];
    v_packet_flit[P_HDR_KV_OFFSET*8 +: P_KV_WIDTH]   = i_core_knowledge_vec;

    // ------- Payload -------
    v_packet_flit[P_HDR_PAYLOAD_OFFSET*8 +: P_LOG_ITEM_LEN*8] = i_core_propose;
end

//------------------------------------------------
//         State Machine
//------------------------------------------------
always @(posedge clk) begin
    if (!rst_n) begin
        state <= S_IDLE;
        m_axis_mac_tx_tdata <= {P_AXIS_DATA_WIDTH{1'b0}};
        m_axis_mac_tx_tkeep <= {P_AXIS_KEEP_WIDTH{1'b0}};
        m_axis_mac_tx_tvalid <= 1'b0;
        m_axis_mac_tx_tlast <= 1'b0;
        m_axis_mac_tx_tuser <= 1'b0;
        r_target_node_id <= 8'b0;
    end else begin
        case (state)
            S_IDLE: begin
                // clear outputs
                m_axis_mac_tx_tdata <= {P_AXIS_DATA_WIDTH{1'b0}};
                m_axis_mac_tx_tkeep <= {P_AXIS_KEEP_WIDTH{1'b0}};
                m_axis_mac_tx_tvalid <= 1'b0;
                m_axis_mac_tx_tlast <= 1'b0;
                m_axis_mac_tx_tuser <= 1'b0;

                r_target_node_id <= 0;

                if (i_timing_window_open_pulse) begin
                    // Start broadcasting to all nodes
                    state <= S_WAITING;
                end
            end
            S_WAITING: begin
                if (i_timing_tx_allowed) begin
                    state <= S_BROADCAST;
                end
            end

            S_BROADCAST: begin
                if (!i_timing_tx_allowed) begin
                    state <= S_IDLE; // Abort if not allowed
                    m_axis_mac_tx_tvalid <= 1'b0;
                    m_axis_mac_tx_tlast <= 1'b0;
                    m_axis_mac_tx_tuser <= {P_AXIS_USER_WIDTH{1'b0}};
                end
                else begin
                    if (!m_axis_mac_tx_tvalid || m_axis_mac_tx_tready) begin
                        // Check if this is the last node
                        m_axis_mac_tx_tdata <= {P_AXIS_DATA_WIDTH{1'b0}};
                        m_axis_mac_tx_tkeep <= {P_AXIS_KEEP_WIDTH{1'b0}};
                        m_axis_mac_tx_tvalid <= 1'b0;
                        m_axis_mac_tx_tlast <= 1'b0;
                        m_axis_mac_tx_tuser <= {P_AXIS_USER_WIDTH{1'b0}};

                        if (r_target_node_id >= P_NODE_COUNT) begin
                            // Finished broadcasting
                            state <= S_IDLE;
                        end else begin
                            // broadcast to all nodes except self
                            if (r_target_node_id != P_NODE_ID) begin
                                m_axis_mac_tx_tdata <= v_packet_flit;
                                m_axis_mac_tx_tkeep <= {P_AXIS_KEEP_WIDTH{1'b1}}; // All bytes valid
                                m_axis_mac_tx_tvalid <= 1'b1;
                                m_axis_mac_tx_tuser <= {P_AXIS_USER_WIDTH{1'b0}};
                                m_axis_mac_tx_tlast <= 1'b1; // Last flit for this transmission
                            end
                            r_target_node_id <= r_target_node_id + 1;
                        end
                    end
                end
            end
            default: state <= S_IDLE;
        endcase
    end 
end

endmodule
