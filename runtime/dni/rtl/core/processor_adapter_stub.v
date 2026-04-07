`timescale 1ns / 1ps
`default_nettype none

/*
 * Minimal processor-adapter stub.
 *
 * This module is the first future-facing placeholder for a real local
 * processor backend below processor_runtime.  It intentionally does not
 * implement a DMA engine or command queue yet.  Instead, it provides:
 *
 * - the standardized runtime control ingress
 * - a placeholder launch/busy state machine
 * - a streaming TX/RX boundary compatible with the current app-slot contract
 * - a minimal completion/status surface
 *
 * The goal is to make the future processor-backed path concrete in RTL without
 * forcing the rest of the DNI architecture to depend on a specific processor
 * or DMA substrate too early.
 */
module processor_adapter_stub #(
    parameter integer AXIS_DATA_WIDTH = 512,
    parameter integer AXIS_KEEP_WIDTH = AXIS_DATA_WIDTH/8,
    parameter integer AXIS_TX_USER_WIDTH = 1,
    parameter integer AXIS_RX_USER_WIDTH = 1,
    parameter integer STATUS_WIDTH = 32
) (
    input  wire                                 clk,
    input  wire                                 rst,
    input  wire                                 i_enable,

    // Runtime control ingress
    input  wire [63:0]                          i_tx_window_id,
    input  wire                                 i_tx_window_open_pulse,
    input  wire                                 i_tx_window_close_pulse,
    input  wire                                 i_tx_commit_start_pulse,
    input  wire                                 i_tx_allowed,
    input  wire                                 i_tx_active,
    input  wire [7:0]                           i_tx_opcode,
    input  wire [15:0]                          i_tx_context_id,

    input  wire [63:0]                          i_rx_window_id,
    input  wire                                 i_rx_window_open_pulse,
    input  wire                                 i_rx_window_close_pulse,
    input  wire                                 i_rx_commit_start_pulse,
    input  wire                                 i_rx_enabled,
    input  wire                                 i_rx_active,
    input  wire [7:0]                           i_rx_opcode,
    input  wire [15:0]                          i_rx_context_id,

    // TX toward processor_runtime
    output reg  [AXIS_DATA_WIDTH-1:0]           m_axis_tx_tdata,
    output reg  [AXIS_KEEP_WIDTH-1:0]           m_axis_tx_tkeep,
    output reg                                  m_axis_tx_tvalid,
    input  wire                                 m_axis_tx_tready,
    output reg                                  m_axis_tx_tlast,
    output reg  [AXIS_TX_USER_WIDTH-1:0]        m_axis_tx_tuser,

    // RX from processor_runtime
    input  wire [AXIS_DATA_WIDTH-1:0]           s_axis_rx_tdata,
    input  wire [AXIS_KEEP_WIDTH-1:0]           s_axis_rx_tkeep,
    input  wire                                 s_axis_rx_tvalid,
    output reg                                  s_axis_rx_tready,
    input  wire                                 s_axis_rx_tlast,
    input  wire [AXIS_RX_USER_WIDTH-1:0]        s_axis_rx_tuser,

    // Completion / status
    output reg                                  o_done,
    output reg                                  o_error,
    output reg                                  o_halt,
    output reg                                  o_busy,
    output reg  [STATUS_WIDTH-1:0]              o_status
);

localparam [1:0]
    STATE_IDLE = 2'd0,
    STATE_TX   = 2'd1,
    STATE_RX   = 2'd2;

reg [1:0] state_reg = STATE_IDLE;

reg [63:0]  active_window_id_reg = 64'd0;
reg [15:0]  active_context_id_reg = 16'd0;
reg [7:0]   active_opcode_reg = 8'd0;
reg         active_tx_not_rx_reg = 1'b0;

wire tx_launch = i_enable && i_tx_window_open_pulse && i_tx_active && i_tx_allowed;
wire rx_launch = i_enable && i_rx_window_open_pulse && i_rx_active && i_rx_enabled;

always @(posedge clk) begin
    if (rst) begin
        state_reg <= STATE_IDLE;
        active_window_id_reg <= 64'd0;
        active_context_id_reg <= 16'd0;
        active_opcode_reg <= 8'd0;
        active_tx_not_rx_reg <= 1'b0;
        o_done <= 1'b0;
        o_error <= 1'b0;
        o_halt <= 1'b0;
        o_busy <= 1'b0;
        o_status <= {STATUS_WIDTH{1'b0}};
    end else begin
        o_done <= 1'b0;
        o_error <= 1'b0;

        if (!i_enable) begin
            state_reg <= STATE_IDLE;
            active_window_id_reg <= 64'd0;
            active_context_id_reg <= 16'd0;
            active_opcode_reg <= 8'd0;
            active_tx_not_rx_reg <= 1'b0;
            o_busy <= 1'b0;
            o_halt <= 1'b0;
            o_status <= {STATUS_WIDTH{1'b0}};
        end else begin
            case (state_reg)
                STATE_IDLE: begin
                    o_busy <= 1'b0;

                    if (tx_launch) begin
                        state_reg <= STATE_TX;
                        active_window_id_reg <= i_tx_window_id;
                        active_context_id_reg <= i_tx_context_id;
                        active_opcode_reg <= i_tx_opcode;
                        active_tx_not_rx_reg <= 1'b1;
                        o_busy <= 1'b1;
                    end else if (rx_launch) begin
                        state_reg <= STATE_RX;
                        active_window_id_reg <= i_rx_window_id;
                        active_context_id_reg <= i_rx_context_id;
                        active_opcode_reg <= i_rx_opcode;
                        active_tx_not_rx_reg <= 1'b0;
                        o_busy <= 1'b1;
                    end
                end

                STATE_TX: begin
                    o_busy <= 1'b1;

                    if (m_axis_tx_tvalid && m_axis_tx_tready && m_axis_tx_tlast) begin
                        state_reg <= STATE_IDLE;
                        o_busy <= 1'b0;
                        o_done <= 1'b1;
                    end else if (i_tx_window_close_pulse || i_tx_commit_start_pulse) begin
                        state_reg <= STATE_IDLE;
                        o_busy <= 1'b0;
                        o_done <= 1'b1;
                    end
                end

                STATE_RX: begin
                    o_busy <= 1'b1;

                    if (s_axis_rx_tvalid && s_axis_rx_tready && s_axis_rx_tlast) begin
                        state_reg <= STATE_IDLE;
                        o_busy <= 1'b0;
                        o_done <= 1'b1;
                    end else if (i_rx_window_close_pulse || i_rx_commit_start_pulse) begin
                        state_reg <= STATE_IDLE;
                        o_busy <= 1'b0;
                        o_done <= 1'b1;
                    end
                end

                default: begin
                    state_reg <= STATE_IDLE;
                    o_busy <= 1'b0;
                    o_error <= 1'b1;
                end
            endcase

            // Minimal status encoding for early debug:
            // [1:0]   state
            // [2]     tx_not_rx
            // [10:3]  opcode
            // [26:11] context_id
            o_status <= {STATUS_WIDTH{1'b0}};
            o_status[1:0] <= state_reg;
            o_status[2] <= active_tx_not_rx_reg;
            o_status[10:3] <= active_opcode_reg;
            o_status[26:11] <= active_context_id_reg;
        end
    end
end

always @(*) begin
    m_axis_tx_tdata = {AXIS_DATA_WIDTH{1'b0}};
    m_axis_tx_tkeep = {AXIS_KEEP_WIDTH{1'b0}};
    m_axis_tx_tvalid = 1'b0;
    m_axis_tx_tlast = 1'b1;
    m_axis_tx_tuser = {AXIS_TX_USER_WIDTH{1'b0}};

    // The first stub does not generate real traffic yet.  It only presents a
    // single-beat placeholder packet while a TX window is active.
    if (state_reg == STATE_TX) begin
        m_axis_tx_tdata[63:0] = active_window_id_reg;
        m_axis_tx_tkeep = {AXIS_KEEP_WIDTH{1'b1}};
        m_axis_tx_tvalid = 1'b1;
        m_axis_tx_tlast = 1'b1;
        m_axis_tx_tuser = {AXIS_TX_USER_WIDTH{1'b0}};
    end
end

always @(*) begin
    // The RX stub simply accepts frames while the RX state is active.
    s_axis_rx_tready = (state_reg == STATE_RX);
end

endmodule

`default_nettype wire
