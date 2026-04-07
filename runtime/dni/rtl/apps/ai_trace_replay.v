`timescale 1ns / 1ps
`default_nettype none

/*
 * Minimal trace-driven AI replay engine.
 *
 * This is intentionally a low-complexity first implementation:
 * - The engine emits synthetic single-beat Ethernet frames.
 * - A scheduled AI window selects one replay context.
 * - The selected context defines a compact trace entry:
 *     packet count, packet length, inter-packet gap, destination node id,
 *     destination MAC, Ethertype, flow id, and payload seed.
 *
 * The goal of this block is not to model a real training stack.  The goal is
 * to provide a deterministic bulk-traffic source that is controlled by the same
 * compiled synchronous substrate as the consensus engine.
 *
 * The RX side is deliberately minimal.  It acts as a schedule-gated sink for
 * AI_RX windows and records whether received frames match the currently
 * selected trace context.  This keeps the initial AI receive path simple while
 * still exercising the same timing and demultiplexing structure as the TX side.
 */
module ai_trace_replay #(
    parameter integer AXIS_DATA_WIDTH = 512,
    parameter integer AXIS_KEEP_WIDTH = AXIS_DATA_WIDTH/8,
    parameter integer AXIS_TX_USER_WIDTH = 1,
    parameter integer AXIS_RX_USER_WIDTH = 1,
    parameter integer TRACE_INDEX_WIDTH = 10,
    parameter integer TRACE_ENTRY_COUNT = 2**TRACE_INDEX_WIDTH,
    parameter [47:0] P_SRC_MAC = 48'h02_00_00_00_00_01,
    parameter [15:0] P_DEFAULT_ETHERTYPE = 16'h88B6
) (
    input  wire                                 clk,
    input  wire                                 rst,

    input  wire                                 i_enable,
    input  wire [63:0]                          i_tx_current_window_id,
    input  wire                                 i_tx_window_open_pulse,
    input  wire                                 i_tx_window_active,
    input  wire                                 i_tx_allowed,
    input  wire [15:0]                          i_tx_context_id,
    input  wire [63:0]                          i_rx_current_window_id,
    input  wire                                 i_rx_window_open_pulse,
    input  wire                                 i_rx_window_active,
    input  wire                                 i_rx_enabled,
    input  wire [15:0]                          i_rx_context_id,

    input  wire                                 cfg_enable,
    input  wire                                 cfg_wr_en,
    input  wire [TRACE_INDEX_WIDTH-1:0]         cfg_wr_entry,
    input  wire [2:0]                           cfg_wr_word,
    input  wire [31:0]                          cfg_wr_data,
    input  wire [TRACE_INDEX_WIDTH-1:0]         cfg_rd_entry,
    input  wire [2:0]                           cfg_rd_word,
    output reg  [31:0]                          cfg_rd_data,

    output reg  [31:0]                          o_pkt_sent_count,
    output reg  [31:0]                          o_rx_pkt_count,
    output reg  [31:0]                          o_rx_byte_count,
    output reg  [31:0]                          o_rx_match_count,
    output reg  [31:0]                          o_rx_drop_count,

    output reg  [AXIS_DATA_WIDTH-1:0]           m_axis_tx_tdata,
    output reg  [AXIS_KEEP_WIDTH-1:0]           m_axis_tx_tkeep,
    output reg                                  m_axis_tx_tvalid,
    input  wire                                 m_axis_tx_tready,
    output reg                                  m_axis_tx_tlast,
    output reg  [AXIS_TX_USER_WIDTH-1:0]        m_axis_tx_tuser,

    input  wire [AXIS_DATA_WIDTH-1:0]           s_axis_rx_tdata,
    input  wire [AXIS_KEEP_WIDTH-1:0]           s_axis_rx_tkeep,
    input  wire                                 s_axis_rx_tvalid,
    input  wire                                 s_axis_rx_tlast,
    input  wire [AXIS_RX_USER_WIDTH-1:0]        s_axis_rx_tuser,
    output wire                                 s_axis_rx_tready
);

localparam integer TRACE_WORDS = 6;
localparam integer TRACE_MEM_WORDS = TRACE_ENTRY_COUNT*TRACE_WORDS;
localparam [1:0]
    STATE_IDLE  = 2'd0,
    STATE_WAIT  = 2'd1,
    STATE_SEND  = 2'd2;

// word 0: [31:16] packet count, [15:0] packet length in bytes
// word 1: [31:0]  inter-packet gap in clk cycles
// word 2: [31:0]  destination MAC low 32 bits
// word 3: [31:16] Ethertype, [15:0] destination MAC high 16 bits
// word 4: [31:16] destination node id, [15:0] flow id
// word 5: [31:0]  payload seed
reg [31:0] trace_mem[0:TRACE_MEM_WORDS-1];

reg [1:0] state_reg = STATE_IDLE;
reg [TRACE_INDEX_WIDTH-1:0] active_trace_reg = {TRACE_INDEX_WIDTH{1'b0}};
reg [15:0] remaining_pkt_reg = 16'd0;
reg [31:0] gap_count_reg = 32'd0;
reg [63:0] window_id_reg = 64'd0;
reg [15:0] flow_id_reg = 16'd0;
reg [15:0] dst_node_id_reg = 16'd0;
reg [31:0] payload_seed_reg = 32'd0;
reg [15:0] rx_expected_flow_id_reg = 16'd0;
reg [15:0] rx_expected_dst_node_id_reg = 16'd0;
reg [15:0] rx_expected_ethertype_reg = P_DEFAULT_ETHERTYPE;
reg tx_window_pending_reg = 1'b0;

wire [TRACE_INDEX_WIDTH-1:0] tx_trace_index = i_tx_context_id[TRACE_INDEX_WIDTH-1:0];
wire [TRACE_INDEX_WIDTH-1:0] rx_trace_index = i_rx_context_id[TRACE_INDEX_WIDTH-1:0];
wire [31:0] selected_tx_word0 = trace_mem[tx_trace_index*TRACE_WORDS + 0];
wire [31:0] selected_tx_word1 = trace_mem[tx_trace_index*TRACE_WORDS + 1];
wire [31:0] selected_tx_word2 = trace_mem[tx_trace_index*TRACE_WORDS + 2];
wire [31:0] selected_tx_word3 = trace_mem[tx_trace_index*TRACE_WORDS + 3];
wire [31:0] selected_tx_word4 = trace_mem[tx_trace_index*TRACE_WORDS + 4];
wire [31:0] selected_tx_word5 = trace_mem[tx_trace_index*TRACE_WORDS + 5];
wire [31:0] selected_rx_word0 = trace_mem[rx_trace_index*TRACE_WORDS + 0];
wire [31:0] selected_rx_word1 = trace_mem[rx_trace_index*TRACE_WORDS + 1];
wire [31:0] selected_rx_word2 = trace_mem[rx_trace_index*TRACE_WORDS + 2];
wire [31:0] selected_rx_word3 = trace_mem[rx_trace_index*TRACE_WORDS + 3];
wire [31:0] selected_rx_word4 = trace_mem[rx_trace_index*TRACE_WORDS + 4];
wire [31:0] selected_rx_word5 = trace_mem[rx_trace_index*TRACE_WORDS + 5];
wire [31:0] trace_word0 = trace_mem[active_trace_reg*TRACE_WORDS + 0];
wire [31:0] trace_word1 = trace_mem[active_trace_reg*TRACE_WORDS + 1];
wire [31:0] trace_word2 = trace_mem[active_trace_reg*TRACE_WORDS + 2];
wire [31:0] trace_word3 = trace_mem[active_trace_reg*TRACE_WORDS + 3];
wire [31:0] trace_word4 = trace_mem[active_trace_reg*TRACE_WORDS + 4];
wire [31:0] trace_word5 = trace_mem[active_trace_reg*TRACE_WORDS + 5];

wire [15:0] selected_tx_packet_count = selected_tx_word0[31:16];
wire [15:0] selected_tx_packet_len = selected_tx_word0[15:0];
wire [31:0] selected_tx_gap_cycles = selected_tx_word1;
wire [15:0] selected_tx_dst_node_id = selected_tx_word4[31:16];
wire [15:0] selected_tx_flow_id = selected_tx_word4[15:0];
wire [31:0] selected_tx_payload_seed = selected_tx_word5;
wire [15:0] selected_rx_dst_node_id = selected_rx_word4[31:16];
wire [15:0] selected_rx_flow_id = selected_rx_word4[15:0];

wire [15:0] trace_packet_count = trace_word0[31:16];
wire [15:0] trace_packet_len = trace_word0[15:0];
wire [31:0] trace_gap_cycles = trace_word1;
wire [47:0] trace_dst_mac = {trace_word3[15:0], trace_word2};
wire [15:0] trace_ethertype = trace_word3[31:16] ? trace_word3[31:16] : P_DEFAULT_ETHERTYPE;
wire [15:0] trace_dst_node_id = trace_word4[31:16];
wire [15:0] trace_flow_id = trace_word4[15:0];
wire [31:0] trace_payload_seed = trace_word5;

wire [15:0] selected_rx_ethertype = selected_rx_word3[31:16] ? selected_rx_word3[31:16] : P_DEFAULT_ETHERTYPE;

wire [15:0] rx_ethertype_raw = s_axis_rx_tdata[12*8 +: 16];
wire [15:0] rx_ethertype = {rx_ethertype_raw[7:0], rx_ethertype_raw[15:8]};
wire [15:0] rx_flow_id = s_axis_rx_tdata[24*8 +: 16];
wire [15:0] rx_dst_node_id = s_axis_rx_tdata[26*8 +: 16];
wire rx_frame_fire = s_axis_rx_tvalid && s_axis_rx_tready && s_axis_rx_tlast;
wire rx_window_accept = i_rx_window_active && i_rx_enabled;
wire rx_flow_match = rx_flow_id == rx_expected_flow_id_reg;
wire rx_dst_node_match = rx_dst_node_id == rx_expected_dst_node_id_reg;
wire rx_ethertype_match = rx_ethertype == rx_expected_ethertype_reg;
wire rx_frame_match = rx_ethertype_match && rx_flow_match && rx_dst_node_match;

integer i;
integer keep_index;
integer cfg_index;
reg [AXIS_KEEP_WIDTH-1:0] keep_mask;
reg [AXIS_DATA_WIDTH-1:0] packet_data;

function integer count_keep_ones;
    input [AXIS_KEEP_WIDTH-1:0] keep_vec;
    integer byte_idx;
    begin
        count_keep_ones = 0;
        for (byte_idx = 0; byte_idx < AXIS_KEEP_WIDTH; byte_idx = byte_idx + 1) begin
            if (keep_vec[byte_idx]) begin
                count_keep_ones = count_keep_ones + 1;
            end
        end
    end
endfunction

assign s_axis_rx_tready = 1'b1;

always @(*) begin
    cfg_index = cfg_rd_entry*TRACE_WORDS + cfg_rd_word;
    if (cfg_rd_word < TRACE_WORDS) begin
        cfg_rd_data = trace_mem[cfg_index];
    end else begin
        cfg_rd_data = 32'd0;
    end

    keep_mask = {AXIS_KEEP_WIDTH{1'b0}};
    for (keep_index = 0; keep_index < AXIS_KEEP_WIDTH; keep_index = keep_index + 1) begin
        if (keep_index < trace_packet_len) begin
            keep_mask[keep_index] = 1'b1;
        end
    end

    packet_data = {AXIS_DATA_WIDTH{1'b0}};

    // Standard Ethernet header.
    packet_data[0*8 +: 8] = trace_dst_mac[47:40];
    packet_data[1*8 +: 8] = trace_dst_mac[39:32];
    packet_data[2*8 +: 8] = trace_dst_mac[31:24];
    packet_data[3*8 +: 8] = trace_dst_mac[23:16];
    packet_data[4*8 +: 8] = trace_dst_mac[15:8];
    packet_data[5*8 +: 8] = trace_dst_mac[7:0];
    packet_data[6*8 +: 8] = P_SRC_MAC[47:40];
    packet_data[7*8 +: 8] = P_SRC_MAC[39:32];
    packet_data[8*8 +: 8] = P_SRC_MAC[31:24];
    packet_data[9*8 +: 8] = P_SRC_MAC[23:16];
    packet_data[10*8 +: 8] = P_SRC_MAC[15:8];
    packet_data[11*8 +: 8] = P_SRC_MAC[7:0];
    packet_data[12*8 +: 8] = trace_ethertype[7:0];
    packet_data[13*8 +: 8] = trace_ethertype[15:8];

    // The payload is synthetic on purpose.  It still carries stable trace
    // metadata so each replay burst corresponds to an explicit trace contract
    // rather than an anonymous burst of packets.
    packet_data[14*8 +: 64] = {
        window_id_reg[7:0], window_id_reg[15:8], window_id_reg[23:16], window_id_reg[31:24],
        window_id_reg[39:32], window_id_reg[47:40], window_id_reg[55:48], window_id_reg[63:56]
    };
    packet_data[22*8 +: 16] = remaining_pkt_reg;
    packet_data[24*8 +: 16] = flow_id_reg;
    packet_data[26*8 +: 16] = dst_node_id_reg;
    packet_data[28*8 +: 32] = payload_seed_reg;
  end

always @(posedge clk) begin
    if (rst) begin
        state_reg <= STATE_IDLE;
        active_trace_reg <= {TRACE_INDEX_WIDTH{1'b0}};
        remaining_pkt_reg <= 16'd0;
        gap_count_reg <= 32'd0;
        window_id_reg <= 64'd0;
        flow_id_reg <= 16'd0;
        dst_node_id_reg <= 16'd0;
        payload_seed_reg <= 32'd0;
        tx_window_pending_reg <= 1'b0;
        o_pkt_sent_count <= 32'd0;
        o_rx_pkt_count <= 32'd0;
        o_rx_byte_count <= 32'd0;
        o_rx_match_count <= 32'd0;
        o_rx_drop_count <= 32'd0;
        rx_expected_flow_id_reg <= 16'd0;
        rx_expected_dst_node_id_reg <= 16'd0;
        rx_expected_ethertype_reg <= P_DEFAULT_ETHERTYPE;

        m_axis_tx_tdata <= {AXIS_DATA_WIDTH{1'b0}};
        m_axis_tx_tkeep <= {AXIS_KEEP_WIDTH{1'b0}};
        m_axis_tx_tvalid <= 1'b0;
        m_axis_tx_tlast <= 1'b0;
        m_axis_tx_tuser <= {AXIS_TX_USER_WIDTH{1'b0}};

        for (i = 0; i < TRACE_MEM_WORDS; i = i + 1) begin
            trace_mem[i] <= 32'd0;
        end
    end else begin
        if (cfg_wr_en && cfg_wr_word < TRACE_WORDS) begin
            trace_mem[cfg_wr_entry*TRACE_WORDS + cfg_wr_word] <= cfg_wr_data;
        end

        // The RX side is intentionally simpler than the TX side.  It acts as a
        // schedule-scoped sink that only accepts packets during AI_RX windows
        // and checks that they match the currently selected trace metadata.
        if (rx_frame_fire) begin
            if (rx_window_accept && rx_frame_match) begin
                o_rx_pkt_count <= o_rx_pkt_count + 1;
                o_rx_byte_count <= o_rx_byte_count + count_keep_ones(s_axis_rx_tkeep);
                o_rx_match_count <= o_rx_match_count + 1;
            end else begin
                o_rx_drop_count <= o_rx_drop_count + 1;
            end
        end

        if (!i_enable || !cfg_enable) begin
            state_reg <= STATE_IDLE;
            remaining_pkt_reg <= 16'd0;
            gap_count_reg <= 32'd0;
            tx_window_pending_reg <= 1'b0;
            m_axis_tx_tvalid <= 1'b0;
            m_axis_tx_tlast <= 1'b0;
            m_axis_tx_tuser <= {AXIS_TX_USER_WIDTH{1'b0}};
        end else begin
            if (i_tx_window_open_pulse) begin
                // Arm exactly one transmit burst for each scheduled AI_TX
                // window.  The engine may wait for tx_allowed, but it must not
                // restart the same burst again until the next window opens.
                tx_window_pending_reg <= i_tx_window_active;
            end else if (!i_tx_window_active) begin
                tx_window_pending_reg <= 1'b0;
            end

            // Capture the receive-side expectation at the start of every AI_RX
            // window.  The sink then keeps those values stable for the full
            // window so that software can reason about one compiled trace
            // instruction at a time.
            if (i_rx_window_open_pulse && i_rx_window_active) begin
                rx_expected_flow_id_reg <= selected_rx_flow_id;
                rx_expected_dst_node_id_reg <= selected_rx_dst_node_id;
                rx_expected_ethertype_reg <= selected_rx_ethertype;
            end

            case (state_reg)
                STATE_IDLE: begin
                    m_axis_tx_tvalid <= 1'b0;
                    m_axis_tx_tlast <= 1'b0;
                    // Sample the context selected by the current schedule entry
                    // exactly when the replay window opens.  Afterwards the
                    // engine remains pinned to that trace record until the
                    // burst completes or the window closes.
                    if (tx_window_pending_reg && i_tx_window_active && i_tx_allowed && selected_tx_packet_count != 0) begin
                        active_trace_reg <= tx_trace_index;
                        flow_id_reg <= selected_tx_flow_id;
                        dst_node_id_reg <= selected_tx_dst_node_id;
                        payload_seed_reg <= selected_tx_payload_seed;
                        remaining_pkt_reg <= selected_tx_packet_count;
                        gap_count_reg <= 32'd0;
                        window_id_reg <= i_tx_current_window_id;
                        tx_window_pending_reg <= 1'b0;
                        state_reg <= STATE_WAIT;
                    end
                end
                STATE_WAIT: begin
                    m_axis_tx_tvalid <= 1'b0;
                    m_axis_tx_tlast <= 1'b0;

                    if (!i_tx_window_active || !i_tx_allowed || remaining_pkt_reg == 0) begin
                        state_reg <= STATE_IDLE;
                    end else if (gap_count_reg != 0) begin
                        gap_count_reg <= gap_count_reg - 1;
                    end else begin
                        state_reg <= STATE_SEND;
                        m_axis_tx_tdata <= packet_data;
                        m_axis_tx_tkeep <= keep_mask;
                        m_axis_tx_tvalid <= 1'b1;
                        m_axis_tx_tlast <= 1'b1;
                        m_axis_tx_tuser <= {AXIS_TX_USER_WIDTH{1'b0}};
                    end
                end
                STATE_SEND: begin
                    if (m_axis_tx_tvalid && m_axis_tx_tready) begin
                        m_axis_tx_tvalid <= 1'b0;
                        m_axis_tx_tlast <= 1'b0;
                        o_pkt_sent_count <= o_pkt_sent_count + 1;

                        if (remaining_pkt_reg > 1 && i_tx_window_active && i_tx_allowed) begin
                            remaining_pkt_reg <= remaining_pkt_reg - 1;
                            gap_count_reg <= trace_gap_cycles;
                            state_reg <= STATE_WAIT;
                        end else begin
                            remaining_pkt_reg <= 16'd0;
                            state_reg <= STATE_IDLE;
                        end
                    end
                end
                default: begin
                    state_reg <= STATE_IDLE;
                end
            endcase
        end
    end
end

endmodule

`default_nettype wire
