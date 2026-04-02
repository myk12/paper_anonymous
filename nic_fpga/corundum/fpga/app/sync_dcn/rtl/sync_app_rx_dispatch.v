`timescale 1ns / 1ps
`default_nettype none

/*
 * Shared application RX dispatcher.
 *
 * The datapath only distinguishes host traffic from application-owned traffic.
 * Once a frame has been classified as "app RX", this block performs the
 * second-stage demultiplexing inside the application cluster:
 * - consensus EtherType frames are delivered to the consensus app
 * - AI replay EtherType frames are delivered to the AI app
 *
 * Like the datapath, this dispatcher is frame-aware.  The route is decided on
 * the first beat of the frame and held until tlast so downstream apps always
 * see complete AXI-stream frames.
 */
module sync_app_rx_dispatch #(
    parameter integer AXIS_DATA_WIDTH = 512,
    parameter integer AXIS_KEEP_WIDTH = AXIS_DATA_WIDTH/8,
    parameter integer AXIS_RX_USER_WIDTH = 1,
    parameter [15:0] P_CONSENSUS_ETHERTYPE = 16'h88B5,
    parameter [15:0] P_AI_ETHERTYPE = 16'h88B6,
    parameter integer P_HDR_ETHERTYPE_OFFSET_BYTES = 12
) (
    input  wire                                 clk,
    input  wire                                 rst,

    input  wire [AXIS_DATA_WIDTH-1:0]           s_axis_app_rx_tdata,
    input  wire [AXIS_KEEP_WIDTH-1:0]           s_axis_app_rx_tkeep,
    input  wire                                 s_axis_app_rx_tvalid,
    input  wire                                 s_axis_app_rx_tlast,
    input  wire [AXIS_RX_USER_WIDTH-1:0]        s_axis_app_rx_tuser,
    output reg                                  s_axis_app_rx_tready,

    output reg  [AXIS_DATA_WIDTH-1:0]           m_axis_cons_rx_tdata,
    output reg  [AXIS_KEEP_WIDTH-1:0]           m_axis_cons_rx_tkeep,
    output reg                                  m_axis_cons_rx_tvalid,
    output reg                                  m_axis_cons_rx_tlast,
    output reg  [AXIS_RX_USER_WIDTH-1:0]        m_axis_cons_rx_tuser,
    input  wire                                 m_axis_cons_rx_tready,

    output reg  [AXIS_DATA_WIDTH-1:0]           m_axis_ai_rx_tdata,
    output reg  [AXIS_KEEP_WIDTH-1:0]           m_axis_ai_rx_tkeep,
    output reg                                  m_axis_ai_rx_tvalid,
    output reg                                  m_axis_ai_rx_tlast,
    output reg  [AXIS_RX_USER_WIDTH-1:0]        m_axis_ai_rx_tuser,
    input  wire                                 m_axis_ai_rx_tready
);

localparam [1:0] RX_ROUTE_DROP = 2'd0;
localparam [1:0] RX_ROUTE_CONS = 2'd1;
localparam [1:0] RX_ROUTE_AI   = 2'd2;

wire consensus_ethertype_match = s_axis_app_rx_tvalid &&
    (s_axis_app_rx_tdata[P_HDR_ETHERTYPE_OFFSET_BYTES*8 +: 16] === {P_CONSENSUS_ETHERTYPE[7:0], P_CONSENSUS_ETHERTYPE[15:8]});
wire ai_ethertype_match = s_axis_app_rx_tvalid &&
    (s_axis_app_rx_tdata[P_HDR_ETHERTYPE_OFFSET_BYTES*8 +: 16] === {P_AI_ETHERTYPE[7:0], P_AI_ETHERTYPE[15:8]});

reg rx_active_reg = 1'b0;
reg [1:0] rx_route_reg = RX_ROUTE_DROP;

wire [1:0] rx_route_eff = rx_active_reg ? rx_route_reg :
    (consensus_ethertype_match ? RX_ROUTE_CONS : (ai_ethertype_match ? RX_ROUTE_AI : RX_ROUTE_DROP));
wire rx_fire = s_axis_app_rx_tvalid && s_axis_app_rx_tready;

always @(posedge clk) begin
    if (rst) begin
        rx_active_reg <= 1'b0;
        rx_route_reg <= RX_ROUTE_DROP;
    end else begin
        if (!rx_active_reg) begin
            if (rx_fire) begin
                rx_active_reg <= !s_axis_app_rx_tlast;
                rx_route_reg <= rx_route_eff;
            end
        end else if (rx_fire && s_axis_app_rx_tlast) begin
            rx_active_reg <= 1'b0;
            rx_route_reg <= RX_ROUTE_DROP;
        end
    end
end

always @(*) begin
    m_axis_cons_rx_tdata  = {AXIS_DATA_WIDTH{1'b0}};
    m_axis_cons_rx_tkeep  = {AXIS_KEEP_WIDTH{1'b0}};
    m_axis_cons_rx_tvalid = 1'b0;
    m_axis_cons_rx_tlast  = 1'b0;
    m_axis_cons_rx_tuser  = {AXIS_RX_USER_WIDTH{1'b0}};
    m_axis_ai_rx_tdata    = {AXIS_DATA_WIDTH{1'b0}};
    m_axis_ai_rx_tkeep    = {AXIS_KEEP_WIDTH{1'b0}};
    m_axis_ai_rx_tvalid   = 1'b0;
    m_axis_ai_rx_tlast    = 1'b0;
    m_axis_ai_rx_tuser    = {AXIS_RX_USER_WIDTH{1'b0}};
    // The current application sinks are always-ready and only accept
    // single-beat frames.  Keep the shared app RX boundary permanently ready
    // so this dispatcher only performs semantic demultiplexing, not
    // backpressure management.
    s_axis_app_rx_tready  = 1'b1;

    case (rx_route_eff)
        RX_ROUTE_CONS: begin
            if (s_axis_app_rx_tvalid) begin
                m_axis_cons_rx_tdata = s_axis_app_rx_tdata;
                m_axis_cons_rx_tkeep = s_axis_app_rx_tkeep;
                m_axis_cons_rx_tvalid = s_axis_app_rx_tvalid;
                m_axis_cons_rx_tlast = s_axis_app_rx_tlast;
                m_axis_cons_rx_tuser = s_axis_app_rx_tuser;
            end
        end
        RX_ROUTE_AI: begin
            if (s_axis_app_rx_tvalid) begin
                m_axis_ai_rx_tdata = s_axis_app_rx_tdata;
                m_axis_ai_rx_tkeep = s_axis_app_rx_tkeep;
                m_axis_ai_rx_tvalid = s_axis_app_rx_tvalid;
                m_axis_ai_rx_tlast = s_axis_app_rx_tlast;
                m_axis_ai_rx_tuser = s_axis_app_rx_tuser;
            end
        end
        default: begin
            // Unknown application traffic is dropped at the app boundary.  The
            // datapath should only send app-owned traffic here, so this case is
            // mainly a guard against inconsistent configuration.
            s_axis_app_rx_tready = 1'b1;
        end
    endcase
end

endmodule

`default_nettype wire
