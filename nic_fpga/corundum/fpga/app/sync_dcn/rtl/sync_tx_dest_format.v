`timescale 1ns / 1ps
`default_nettype none
`include "fpga/app/sync_dcn/rtl/common/sync_dcn_defs.vh"

/*
 * Plane-aware TX destination formatter.
 *
 * Corundum's application TX path ultimately needs an AXIS tdest field whose
 * low-order port bits select the physical egress port.  This helper keeps that
 * rule intact while also carrying plane metadata in the upper tdest bits for
 * observability and future policy hooks.
 *
 * Current convention:
 * - low PORT_SEL_WIDTH bits: physical egress port
 * - next 4 bits, if present: plane id
 * - remaining upper bits: zero
 */
module sync_tx_dest_format #(
    parameter integer DEST_WIDTH = 5,
    parameter integer PORT_SEL_WIDTH = 1,
    parameter [7:0] EPS_BASE_PORT = 8'd0,
    parameter [7:0] OCS_BASE_PORT = 8'd1
) (
    input  wire [7:0]                i_plane_id,
    input  wire [7:0]                i_target_port,
    output reg  [DEST_WIDTH-1:0]     o_tdest
);

localparam integer PLANE_BITS = DEST_WIDTH > PORT_SEL_WIDTH ?
    ((DEST_WIDTH-PORT_SEL_WIDTH) > 4 ? 4 : (DEST_WIDTH-PORT_SEL_WIDTH)) : 0;

reg [7:0] physical_port;

always @(*) begin
    // The schedule may provide a logical per-plane target port.  For the
    // current prototype, each plane has a base physical port and the logical
    // target port is interpreted relative to that base.
    case (i_plane_id)
        `SYNC_DCN_PLANE_OCS: physical_port = OCS_BASE_PORT + i_target_port;
        default: physical_port = EPS_BASE_PORT + i_target_port;
    endcase

    o_tdest = {DEST_WIDTH{1'b0}};
    o_tdest[PORT_SEL_WIDTH-1:0] = physical_port[PORT_SEL_WIDTH-1:0];

    // Preserve the physical-port selection in the low tdest bits so the
    // existing Corundum interface datapath behaves exactly as before.  Any
    // remaining upper bits are used only as plane metadata.
    if (PLANE_BITS > 0) begin
        o_tdest[PORT_SEL_WIDTH +: PLANE_BITS] = i_plane_id[PLANE_BITS-1:0];
    end
end

endmodule

`default_nettype wire
