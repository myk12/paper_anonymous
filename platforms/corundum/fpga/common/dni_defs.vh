`ifndef DNI_DEFS_VH
`define DNI_DEFS_VH

// Application identifiers carried in compiled execution-table entries.
`define DNI_APP_NONE         4'h0
`define DNI_APP_CONSENSUS    4'h1
`define DNI_APP_AI_REPLAY    4'h2

// Logical communication planes.
`define DNI_PLANE_EPS        4'h0
`define DNI_PLANE_OCS        4'h1

// Compiled operation identifiers.
`define DNI_OP_NONE          8'h00
`define DNI_OP_CONS_TX       8'h10
`define DNI_OP_CONS_RX       8'h11
`define DNI_OP_AI_TX         8'h20
`define DNI_OP_AI_RX         8'h21

// Entry flags embedded in execution-table route words.
`define DNI_FLAG_VALID               8'h01
`define DNI_FLAG_TX_ENABLE           8'h02
`define DNI_FLAG_RX_ENABLE           8'h04
`define DNI_FLAG_COMPLETION_EVENT    8'h20

`endif
