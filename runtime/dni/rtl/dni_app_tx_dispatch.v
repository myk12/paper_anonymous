`timescale 1ns / 1ps
`default_nettype none
`include "common/dni_defs.vh"

/*
 * Shared application TX dispatcher.
 *
 * The compiled schedule activates exactly one application window at a time.
 * This block converts the active app id into a single AXI-stream source for the
 * downstream wrapper arbitration logic.
 */
module dni_app_tx_dispatch #(
    parameter integer AXIS_DATA_WIDTH = 512,
    parameter integer AXIS_KEEP_WIDTH = AXIS_DATA_WIDTH/8,
    parameter integer AXIS_TX_USER_WIDTH = 1
) (
    input  wire                                 i_app_valid,
    input  wire [7:0]                           i_app_id,
    input  wire [7:0]                           i_opcode,

    input  wire [AXIS_DATA_WIDTH-1:0]           s_axis_cons_tx_tdata,
    input  wire [AXIS_KEEP_WIDTH-1:0]           s_axis_cons_tx_tkeep,
    input  wire                                 s_axis_cons_tx_tvalid,
    input  wire                                 s_axis_cons_tx_tlast,
    input  wire [AXIS_TX_USER_WIDTH-1:0]        s_axis_cons_tx_tuser,
    output reg                                  s_axis_cons_tx_tready,

    input  wire [AXIS_DATA_WIDTH-1:0]           s_axis_ai_tx_tdata,
    input  wire [AXIS_KEEP_WIDTH-1:0]           s_axis_ai_tx_tkeep,
    input  wire                                 s_axis_ai_tx_tvalid,
    input  wire                                 s_axis_ai_tx_tlast,
    input  wire [AXIS_TX_USER_WIDTH-1:0]        s_axis_ai_tx_tuser,
    output reg                                  s_axis_ai_tx_tready,

    output reg  [AXIS_DATA_WIDTH-1:0]           m_axis_tx_tdata,
    output reg  [AXIS_KEEP_WIDTH-1:0]           m_axis_tx_tkeep,
    output reg                                  m_axis_tx_tvalid,
    output reg                                  m_axis_tx_tlast,
    output reg  [AXIS_TX_USER_WIDTH-1:0]        m_axis_tx_tuser,
    input  wire                                 m_axis_tx_tready,

    output wire                                 o_app_tx_valid
);

assign o_app_tx_valid = (i_app_valid && i_app_id == `DNI_APP_CONSENSUS && i_opcode == `DNI_OP_CONS_TX && s_axis_cons_tx_tvalid) ||
    (i_app_valid && i_app_id == `DNI_APP_AI_REPLAY && i_opcode == `DNI_OP_AI_TX && s_axis_ai_tx_tvalid);

always @(*) begin
    m_axis_tx_tdata = {AXIS_DATA_WIDTH{1'b0}};
    m_axis_tx_tkeep = {AXIS_KEEP_WIDTH{1'b0}};
    m_axis_tx_tvalid = 1'b0;
    m_axis_tx_tlast = 1'b0;
    m_axis_tx_tuser = {AXIS_TX_USER_WIDTH{1'b0}};

    s_axis_cons_tx_tready = 1'b0;
    s_axis_ai_tx_tready = 1'b0;

    case (i_opcode)
        `DNI_OP_CONS_TX: begin
            m_axis_tx_tdata = s_axis_cons_tx_tdata;
            m_axis_tx_tkeep = s_axis_cons_tx_tkeep;
            m_axis_tx_tvalid = i_app_valid && i_app_id == `DNI_APP_CONSENSUS && s_axis_cons_tx_tvalid;
            m_axis_tx_tlast = s_axis_cons_tx_tlast;
            m_axis_tx_tuser = s_axis_cons_tx_tuser;
            s_axis_cons_tx_tready = i_app_valid && i_app_id == `DNI_APP_CONSENSUS && m_axis_tx_tready;
        end
        `DNI_OP_AI_TX: begin
            m_axis_tx_tdata = s_axis_ai_tx_tdata;
            m_axis_tx_tkeep = s_axis_ai_tx_tkeep;
            m_axis_tx_tvalid = i_app_valid && i_app_id == `DNI_APP_AI_REPLAY && s_axis_ai_tx_tvalid;
            m_axis_tx_tlast = s_axis_ai_tx_tlast;
            m_axis_tx_tuser = s_axis_ai_tx_tuser;
            s_axis_ai_tx_tready = i_app_valid && i_app_id == `DNI_APP_AI_REPLAY && m_axis_tx_tready;
        end
        default: begin
            // Non-TX opcodes such as guard or RX-expect intentionally produce
            // no output stream on the transmit side.
        end
    endcase
end

endmodule

`default_nettype wire
