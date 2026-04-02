`timescale 1ns / 1ps
`default_nettype none

/*
 * Sync-DCN datapath wrapper.
 *
 * This module is intentionally application-agnostic on the TX side: it accepts
 * one already-dispatched application stream and arbitrates it against host DMA.
 * On the RX side, the wrapper only distinguishes host traffic from
 * application-owned traffic:
 * - frames that belong to the Sync-DCN app space are delivered to one generic
 *   app RX stream
 * - everything else is returned to the host RX path
 *
 * The classification is frame-aware.  Once the first beat of a frame is
 * routed, the datapath keeps the whole frame on that same destination until
 * tlast.
 */
module sync_dcn_datapath #(
    parameter integer AXIS_DATA_WIDTH = 512,
    parameter integer AXIS_KEEP_WIDTH = AXIS_DATA_WIDTH/8,
    parameter integer AXIS_TX_USER_WIDTH = 1,
    parameter integer AXIS_RX_USER_WIDTH = 1,
    parameter [15:0] P_CONSENSUS_ETHERTYPE = 16'h88B5,
    parameter [15:0] P_AI_ETHERTYPE = 16'h88B6,
    parameter integer P_HDR_ETHERTYPE_OFFSET_BYTES = 12
) (
    // Global signals
    input  wire                                 clk,
    input  wire                                 rst,
    input  wire                                 i_enable,

    // AXI Host DMA TX interface (to MAC)
    input  wire [AXIS_DATA_WIDTH-1:0]           s_axis_dma_tx_tdata,
    input  wire [AXIS_KEEP_WIDTH-1:0]           s_axis_dma_tx_tkeep,
    input  wire                                 s_axis_dma_tx_tvalid,
    input  wire                                 s_axis_dma_tx_tlast,
    input  wire [AXIS_TX_USER_WIDTH-1:0]        s_axis_dma_tx_tuser,
    output reg                                  s_axis_dma_tx_tready,

    // AXI App TX interface (to MAC)
    input  wire [AXIS_DATA_WIDTH-1:0]           s_axis_app_tx_tdata,
    input  wire [AXIS_KEEP_WIDTH-1:0]           s_axis_app_tx_tkeep,
    input  wire                                 s_axis_app_tx_tvalid,
    input  wire                                 s_axis_app_tx_tlast,
    input  wire [AXIS_TX_USER_WIDTH-1:0]        s_axis_app_tx_tuser,
    output reg                                  s_axis_app_tx_tready,
    input  wire                                 i_app_tx_valid,
    output wire                                 o_app_tx_selected,

    // AXI MAC RX/TX interface
    output reg  [AXIS_DATA_WIDTH-1:0]           m_axis_mac_tx_tdata,
    output reg  [AXIS_KEEP_WIDTH-1:0]           m_axis_mac_tx_tkeep,
    output reg                                  m_axis_mac_tx_tvalid,
    output reg                                  m_axis_mac_tx_tlast,
    output reg  [AXIS_TX_USER_WIDTH-1:0]        m_axis_mac_tx_tuser,
    input  wire                                 m_axis_mac_tx_tready,

    // AXI MAC RX/TX interface
    input  wire [AXIS_DATA_WIDTH-1:0]           s_axis_mac_rx_tdata,
    input  wire [AXIS_KEEP_WIDTH-1:0]           s_axis_mac_rx_tkeep,
    input  wire                                 s_axis_mac_rx_tvalid,
    input  wire                                 s_axis_mac_rx_tlast,
    input  wire [AXIS_RX_USER_WIDTH-1:0]        s_axis_mac_rx_tuser,
    output reg                                  s_axis_mac_rx_tready,

    // AXI DMA RX interface (from MAC -> to host)
    output reg  [AXIS_DATA_WIDTH-1:0]           m_axis_dma_rx_tdata,
    output reg  [AXIS_KEEP_WIDTH-1:0]           m_axis_dma_rx_tkeep,
    output reg                                  m_axis_dma_rx_tvalid,
    output reg                                  m_axis_dma_rx_tlast,
    output reg  [AXIS_RX_USER_WIDTH-1:0]        m_axis_dma_rx_tuser,
    input  wire                                 m_axis_dma_rx_tready,

    // AXI generic app RX interface (from MAC -> to sync app cluster)
    output reg  [AXIS_DATA_WIDTH-1:0]           m_axis_app_rx_tdata,
    output reg  [AXIS_KEEP_WIDTH-1:0]           m_axis_app_rx_tkeep,
    output reg                                  m_axis_app_rx_tvalid,
    output reg                                  m_axis_app_rx_tlast,
    output reg  [AXIS_RX_USER_WIDTH-1:0]        m_axis_app_rx_tuser,
    input  wire                                 m_axis_app_rx_tready
);

localparam TX_SEL_HOST = 1'b0;
localparam TX_SEL_APP  = 1'b1;

reg tx_sel_reg;
reg tx_lock_reg;

wire tx_sel_eff = tx_lock_reg ? tx_sel_reg : (i_app_tx_valid ? TX_SEL_APP : TX_SEL_HOST);
wire sel_host_eff = (tx_sel_eff == TX_SEL_HOST);

wire [AXIS_DATA_WIDTH-1:0] tx_sel_tdata = sel_host_eff ? s_axis_dma_tx_tdata : s_axis_app_tx_tdata;
wire [AXIS_KEEP_WIDTH-1:0] tx_sel_tkeep = sel_host_eff ? s_axis_dma_tx_tkeep : s_axis_app_tx_tkeep;
wire                       tx_sel_tvalid = sel_host_eff ? s_axis_dma_tx_tvalid : s_axis_app_tx_tvalid;
wire                       tx_sel_tlast = sel_host_eff ? s_axis_dma_tx_tlast : s_axis_app_tx_tlast;
wire [AXIS_TX_USER_WIDTH-1:0] tx_sel_tuser = sel_host_eff ? s_axis_dma_tx_tuser : s_axis_app_tx_tuser;

localparam [1:0] RX_ROUTE_HOST = 2'd0;
localparam [1:0] RX_ROUTE_APP  = 2'd1;

wire consensus_ethertype_match = s_axis_mac_rx_tvalid &&
    (s_axis_mac_rx_tdata[P_HDR_ETHERTYPE_OFFSET_BYTES*8 +: 16] === {P_CONSENSUS_ETHERTYPE[7:0], P_CONSENSUS_ETHERTYPE[15:8]});
wire ai_ethertype_match = s_axis_mac_rx_tvalid &&
    (s_axis_mac_rx_tdata[P_HDR_ETHERTYPE_OFFSET_BYTES*8 +: 16] === {P_AI_ETHERTYPE[7:0], P_AI_ETHERTYPE[15:8]});
wire app_ethertype_match = consensus_ethertype_match || ai_ethertype_match;

reg rx_active_reg;
reg [1:0] rx_route_reg;
wire rx_fire = s_axis_mac_rx_tvalid && s_axis_mac_rx_tready;
wire [1:0] rx_route_eff = rx_active_reg ? rx_route_reg :
    (app_ethertype_match ? RX_ROUTE_APP : RX_ROUTE_HOST);

// Expose whether the app-side stream is currently presenting a valid frame to
// the shared datapath arbitration point.  The subsystem uses this to decide
// whether the formatted app destination should override the host tdest field.
assign o_app_tx_selected = i_app_tx_valid;

always @(*) begin
    m_axis_mac_tx_tdata  = {AXIS_DATA_WIDTH{1'b0}};
    m_axis_mac_tx_tkeep  = {AXIS_KEEP_WIDTH{1'b0}};
    m_axis_mac_tx_tvalid = 1'b0;
    m_axis_mac_tx_tlast  = 1'b0;
    m_axis_mac_tx_tuser  = {AXIS_TX_USER_WIDTH{1'b0}};
    s_axis_dma_tx_tready = 1'b0;
    s_axis_app_tx_tready = 1'b0;

    if (!i_enable) begin
        s_axis_dma_tx_tready = m_axis_mac_tx_tready;
        m_axis_mac_tx_tvalid = s_axis_dma_tx_tvalid;
        if (s_axis_dma_tx_tvalid) begin
            m_axis_mac_tx_tdata = s_axis_dma_tx_tdata;
            m_axis_mac_tx_tkeep = s_axis_dma_tx_tkeep;
            m_axis_mac_tx_tlast = s_axis_dma_tx_tlast;
            m_axis_mac_tx_tuser = s_axis_dma_tx_tuser;
        end
    end else begin
        m_axis_mac_tx_tdata = tx_sel_tdata;
        m_axis_mac_tx_tkeep = tx_sel_tkeep;
        m_axis_mac_tx_tvalid = tx_sel_tvalid;
        m_axis_mac_tx_tlast = tx_sel_tlast;
        m_axis_mac_tx_tuser = tx_sel_tuser;

        if (sel_host_eff) begin
            s_axis_dma_tx_tready = m_axis_mac_tx_tready;
        end else begin
            s_axis_app_tx_tready = m_axis_mac_tx_tready;
        end
    end
end

always @(posedge clk) begin
    if (rst || !i_enable) begin
        tx_sel_reg <= TX_SEL_HOST;
        tx_lock_reg <= 1'b0;
    end else begin
        if (!tx_lock_reg) begin
            if (i_app_tx_valid || s_axis_dma_tx_tvalid) begin
                tx_sel_reg <= i_app_tx_valid ? TX_SEL_APP : TX_SEL_HOST;
                tx_lock_reg <= 1'b1;
                if (m_axis_mac_tx_tready && tx_sel_tvalid && tx_sel_tlast) begin
                    tx_lock_reg <= 1'b0;
                end
            end
        end else if (m_axis_mac_tx_tready && tx_sel_tvalid && tx_sel_tlast) begin
            tx_lock_reg <= 1'b0;
        end
    end
end

always @(posedge clk) begin
    if (rst || !i_enable) begin
        rx_active_reg <= 1'b0;
        rx_route_reg <= RX_ROUTE_HOST;
    end else begin
        if (!rx_active_reg) begin
            if (rx_fire) begin
                rx_active_reg <= !s_axis_mac_rx_tlast;
                rx_route_reg <= rx_route_eff;
            end
        end else if (rx_fire && s_axis_mac_rx_tlast) begin
            rx_active_reg <= 1'b0;
            rx_route_reg <= RX_ROUTE_HOST;
        end
    end
end

always @(*) begin
    m_axis_dma_rx_tdata  = {AXIS_DATA_WIDTH{1'b0}};
    m_axis_dma_rx_tkeep  = {AXIS_KEEP_WIDTH{1'b0}};
    m_axis_dma_rx_tvalid = 1'b0;
    m_axis_dma_rx_tlast  = 1'b0;
    m_axis_dma_rx_tuser  = {AXIS_RX_USER_WIDTH{1'b0}};
    m_axis_app_rx_tdata  = {AXIS_DATA_WIDTH{1'b0}};
    m_axis_app_rx_tkeep  = {AXIS_KEEP_WIDTH{1'b0}};
    m_axis_app_rx_tvalid = 1'b0;
    m_axis_app_rx_tlast  = 1'b0;
    m_axis_app_rx_tuser  = {AXIS_RX_USER_WIDTH{1'b0}};
    s_axis_mac_rx_tready = 1'b0;

    if (!i_enable) begin
        // Disabled mode must behave like a transparent NIC datapath on both
        // TX and RX.  Route every received frame back to the host and keep the
        // app cluster fully bypassed so a soft disable does not strand traffic.
        s_axis_mac_rx_tready = m_axis_dma_rx_tready;
        if (s_axis_mac_rx_tvalid) begin
            m_axis_dma_rx_tdata = s_axis_mac_rx_tdata;
            m_axis_dma_rx_tkeep = s_axis_mac_rx_tkeep;
            m_axis_dma_rx_tvalid = s_axis_mac_rx_tvalid;
            m_axis_dma_rx_tlast = s_axis_mac_rx_tlast;
            m_axis_dma_rx_tuser = s_axis_mac_rx_tuser;
        end
    end else if (rx_route_eff == RX_ROUTE_APP) begin
        // The current app cluster RX path is intentionally lossless and
        // always-ready: both consensus_rx and ai_trace_replay consume
        // single-beat frames without backpressure.  Keep the MAC-facing
        // application route permanently ready so the generic datapath does not
        // participate in any downstream ready feedback loop.
        s_axis_mac_rx_tready = 1'b1;
        if (s_axis_mac_rx_tvalid) begin
            m_axis_app_rx_tdata = s_axis_mac_rx_tdata;
            m_axis_app_rx_tkeep = s_axis_mac_rx_tkeep;
            m_axis_app_rx_tvalid = s_axis_mac_rx_tvalid;
            m_axis_app_rx_tlast = s_axis_mac_rx_tlast;
            m_axis_app_rx_tuser = s_axis_mac_rx_tuser;
        end
    end else begin
        s_axis_mac_rx_tready = m_axis_dma_rx_tready;
        if (s_axis_mac_rx_tvalid) begin
            m_axis_dma_rx_tdata = s_axis_mac_rx_tdata;
            m_axis_dma_rx_tkeep = s_axis_mac_rx_tkeep;
            m_axis_dma_rx_tvalid = s_axis_mac_rx_tvalid;
            m_axis_dma_rx_tlast = s_axis_mac_rx_tlast;
            m_axis_dma_rx_tuser = s_axis_mac_rx_tuser;
        end
    end
end

endmodule

`default_nettype wire
