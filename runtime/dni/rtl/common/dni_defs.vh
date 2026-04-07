`ifndef DNI_DEFS_VH
`define DNI_DEFS_VH

// Shared application ids for the local compiled execution program.
`define DNI_APP_NONE       8'd0
`define DNI_APP_CONSENSUS  8'd1
`define DNI_APP_AI_REPLAY  8'd2

// Shared plane ids.  These ids are carried in the execution-table metadata and
// may also be mirrored into tdest sideband bits for observability.
`define DNI_PLANE_EPS      8'd0
`define DNI_PLANE_OCS      8'd1

// Compiled execution opcodes.
`define DNI_OP_IDLE        8'h00
`define DNI_OP_GUARD       8'h01
`define DNI_OP_CONS_TX     8'h10
`define DNI_OP_CONS_RX     8'h11
`define DNI_OP_AI_TX       8'h20
`define DNI_OP_AI_RX       8'h21
`define DNI_OP_RECONFIG    8'h30

// Route/behavior flags carried in execution-table word5[7:0].
`define DNI_FLAG_VALID             8'h01
`define DNI_FLAG_TX_ENABLE         8'h02
`define DNI_FLAG_RX_ENABLE         8'h04
`define DNI_FLAG_DROP_NONMATCHING  8'h08
`define DNI_FLAG_EXPECT_PACKET     8'h10
`define DNI_FLAG_COMPLETION_EVENT  8'h20

`endif
