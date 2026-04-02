`timescale 1ns / 1ps
`default_nettype none

/*
 * Sync-DCN subsystem top.
 *
 * This module owns the entire SDCN-specific control and datapath stack:
 * - AXI-Lite register file and control ABI
 * - compiled execution-table storage and bank switching
 * - schedule executor
 * - sibling application engines
 * - generic host/app <-> MAC datapath wrapper
 * - plane-aware tdest formatting
 *
 * The Corundum-facing mqnic_app_block is therefore reduced to a thin shell that
 * forwards standard mqnic interfaces into this subsystem.
 *
 * Control/status register map used by the current host tools:
 *   0x0000  ID
 *   0x0004  VERSION
 *   0x0008  CTRL                [0]=subsystem_enable, [1]=subsystem_soft_reset
 *   0x000C  CTRL_ETHERTYPE      compatibility register, not consumed by active path
 *   0x0010  CTRL_STATUS         shadow view of enable/reset
 *   0x0014  EXEC_STATUS         [0]=exec_enable, [1]=active_bank, [2]=pending_valid
 *   0x0018  LEGACY_WINDOW_PER   compatibility register, not consumed by executor
 *   0x001C  ACTIVATE_TIME_LO
 *   0x0020  ACTIVATE_TIME_HI
 *   0x0024  ADMIN               [0]=admin_bank, [1]=arm_pending_switch
 *   0x0028  CURRENT_WINDOW_LO
 *   0x002C  CURRENT_WINDOW_HI
 *   0x0030  ACTIVE_TARGET       {plane_id, target_queue, target_port}
 *   0x0034  WINDOW_STATUS       {exec_valid, rx_enabled, tx_allowed, window_active}
 *   0x0038  CURRENT_ENTRY_PTR
 *   0x003C  ACTIVE_START_TIME_LO
 *   0x0040  ACTIVE_START_TIME_HI
 *   0x0044  ACTIVE_END_TIME_LO
 *   0x0048  ACTIVE_APP_INFO     {plane_id, app_id, opcode}
 *   0x004C  ACTIVE_CONTEXT
 *   0x0050  ACTIVE_END_TIME_HI
 *   0x0054  AI_ENABLE           [0]=ai_replay_enable
 *   0x0058  AI_PKT_SENT_COUNT
 *   0x005C  ACTIVE_ENTRY_META
 *   0x0060  BANK_STATUS         {pending_bank, active_bank}
 *   0x0064  PENDING_TIME_LO
 *   0x0068  PENDING_TIME_HI
 *   0x006C  AI_RX_PKT_COUNT
 *   0x0070  AI_RX_BYTE_COUNT
 *   0x0074  AI_RX_MATCH_COUNT
 *   0x0078  AI_RX_DROP_COUNT
 *   0x007C  CONSENSUS_CTRL      [0]=consensus_enable, [1]=clear_halt_pulse
 *   0x008C  CONSENSUS_STATUS    [0]=system_halt, [7:4]=debug_state
 *
 * Control registers decode only the 0x0xxx page.  Higher address windows are
 * reserved for table access and must never alias onto the control register
 * file.
 *
 * Table windows:
 *   0x1000 + entry*32 + word*4  TX execution table
 *   0x5800 + entry*32 + word*4  RX execution table
 *   0x9000 + entry*32 + word*4  AI trace table
 *
 * Execution-table entry layout consumed by sync_schedule_executor:
 *   word0: start_time_ns[31:0]
 *   word1: start_time_ns[63:32]
 *   word2: end_time_ns[31:0]
 *   word3: end_time_ns[63:32]
 *   word4: {context_id[15:0], opcode[7:0], plane_id[3:0], app_id[3:0]}
 *   word5: {queue_id[15:0], target_port[7:0], flags[7:0]}
 *   word6: {dst_node_id[15:0], flow_id[15:0]}
 *   word7: reserved
 */
module sync_dcn_subsystem #(
    parameter PTP_TS_WIDTH = 96,
    parameter P_NODE_ID = 0,
    parameter PORTS_PER_IF = 1,
    parameter TX_TAG_WIDTH = 16,
    parameter AXIL_APP_CTRL_DATA_WIDTH = 32,
    parameter AXIL_APP_CTRL_ADDR_WIDTH = 16,
    parameter AXIL_APP_CTRL_STRB_WIDTH = (AXIL_APP_CTRL_DATA_WIDTH/8),
    parameter AXIS_IF_DATA_WIDTH = 512,
    parameter AXIS_IF_KEEP_WIDTH = AXIS_IF_DATA_WIDTH/8,
    parameter AXIS_IF_TX_ID_WIDTH = 12,
    parameter AXIS_IF_RX_ID_WIDTH = 1,
    parameter AXIS_IF_TX_DEST_WIDTH = 5,
    parameter AXIS_IF_RX_DEST_WIDTH = 8,
    parameter AXIS_IF_TX_USER_WIDTH = 1,
    parameter AXIS_IF_RX_USER_WIDTH = 1
) (
    // clock and reset
    input  wire                                           clk,
    input  wire                                           rst,

    // ----------------------------------------------------------------------
    //                      AXI-Lite control interface
    // ----------------------------------------------------------------------
    // AXI-Lite Write 
    input  wire [AXIL_APP_CTRL_ADDR_WIDTH-1:0]            s_axil_app_ctrl_awaddr,
    input  wire [2:0]                                     s_axil_app_ctrl_awprot,
    input  wire                                           s_axil_app_ctrl_awvalid,
    output wire                                           s_axil_app_ctrl_awready,
    input  wire [AXIL_APP_CTRL_DATA_WIDTH-1:0]            s_axil_app_ctrl_wdata,
    input  wire [AXIL_APP_CTRL_STRB_WIDTH-1:0]            s_axil_app_ctrl_wstrb,
    input  wire                                           s_axil_app_ctrl_wvalid,
    output wire                                           s_axil_app_ctrl_wready,
    output wire [1:0]                                     s_axil_app_ctrl_bresp,
    output wire                                           s_axil_app_ctrl_bvalid,
    input  wire                                           s_axil_app_ctrl_bready,

    // AXI-Lite Read
    input  wire [AXIL_APP_CTRL_ADDR_WIDTH-1:0]            s_axil_app_ctrl_araddr,
    input  wire [2:0]                                     s_axil_app_ctrl_arprot,
    input  wire                                           s_axil_app_ctrl_arvalid,
    output wire                                           s_axil_app_ctrl_arready,
    output wire [AXIL_APP_CTRL_DATA_WIDTH-1:0]            s_axil_app_ctrl_rdata,
    output wire [1:0]                                     s_axil_app_ctrl_rresp,
    output wire                                           s_axil_app_ctrl_rvalid,
    input  wire                                           s_axil_app_ctrl_rready,

    // ----------------------------------------------------------------------
    //                      PTP time input
    // ----------------------------------------------------------------------
    input  wire [PTP_TS_WIDTH-1:0]                        ptp_sync_ts_tod,

    // ----------------------------------------------------------------------
    //           TX path: host and app -> SDCN datapath -> MAC
    // ----------------------------------------------------------------------
    // Upstream host interface (before datapath processing, tdest formatting, and scheduling)
    input  wire [AXIS_IF_DATA_WIDTH-1:0]                  s_axis_if_tx_tdata,
    input  wire [AXIS_IF_KEEP_WIDTH-1:0]                  s_axis_if_tx_tkeep,
    input  wire                                           s_axis_if_tx_tvalid,
    output wire                                           s_axis_if_tx_tready,
    input  wire                                           s_axis_if_tx_tlast,
    input  wire [AXIS_IF_TX_ID_WIDTH-1:0]                 s_axis_if_tx_tid,
    input  wire [AXIS_IF_TX_DEST_WIDTH-1:0]               s_axis_if_tx_tdest,
    input  wire [AXIS_IF_TX_USER_WIDTH-1:0]               s_axis_if_tx_tuser,

    // Downstream MAC interface (after datapath processing and tdest formatting)
    output wire [AXIS_IF_DATA_WIDTH-1:0]                  m_axis_if_tx_tdata,
    output wire [AXIS_IF_KEEP_WIDTH-1:0]                  m_axis_if_tx_tkeep,
    output wire                                           m_axis_if_tx_tvalid,
    input  wire                                           m_axis_if_tx_tready,
    output wire                                           m_axis_if_tx_tlast,
    output wire [AXIS_IF_TX_ID_WIDTH-1:0]                 m_axis_if_tx_tid,
    output wire [AXIS_IF_TX_DEST_WIDTH-1:0]               m_axis_if_tx_tdest,
    output wire [AXIS_IF_TX_USER_WIDTH-1:0]               m_axis_if_tx_tuser,

    // ----------------------------------------------------------------------
    //           RX path: MAC -> SDCN datapath -> host and app
    // ----------------------------------------------------------------------
    // Upstream MAC interface (before datapath processing and scheduling)
    input  wire [AXIS_IF_DATA_WIDTH-1:0]                  s_axis_if_rx_tdata,
    input  wire [AXIS_IF_KEEP_WIDTH-1:0]                  s_axis_if_rx_tkeep,
    input  wire                                           s_axis_if_rx_tvalid,
    output wire                                           s_axis_if_rx_tready,
    input  wire                                           s_axis_if_rx_tlast,
    input  wire [AXIS_IF_RX_ID_WIDTH-1:0]                 s_axis_if_rx_tid,
    input  wire [AXIS_IF_RX_DEST_WIDTH-1:0]               s_axis_if_rx_tdest,
    input  wire [AXIS_IF_RX_USER_WIDTH-1:0]               s_axis_if_rx_tuser,

    // Downstream host interface (after datapath processing and scheduling)
    output wire [AXIS_IF_DATA_WIDTH-1:0]                  m_axis_if_rx_tdata,
    output wire [AXIS_IF_KEEP_WIDTH-1:0]                  m_axis_if_rx_tkeep,
    output wire                                           m_axis_if_rx_tvalid,
    input  wire                                           m_axis_if_rx_tready,
    output wire                                           m_axis_if_rx_tlast,
    output wire [AXIS_IF_RX_ID_WIDTH-1:0]                 m_axis_if_rx_tid,
    output wire [AXIS_IF_RX_DEST_WIDTH-1:0]               m_axis_if_rx_tdest,
    output wire [AXIS_IF_RX_USER_WIDTH-1:0]               m_axis_if_rx_tuser
);

localparam  [31:0] REG_ID_VAL        = 32'h434E534E;
localparam  [31:0] REG_VERSION_VAL   = 32'h0002_0000;
localparam  integer TX_SCHED_ENTRY_INDEX_WIDTH = 10;
localparam  integer TX_SCHED_ENTRY_COUNT = 1024;
localparam  integer TX_SCHED_VISIBLE_ENTRY_COUNT = 576;
localparam  integer RX_SCHED_ENTRY_INDEX_WIDTH = 9;
localparam  integer RX_SCHED_ENTRY_COUNT = 512;
localparam  integer RX_SCHED_VISIBLE_ENTRY_COUNT = 448;
localparam  integer AI_TRACE_ENTRY_INDEX_WIDTH = 10;
localparam  integer AI_TRACE_ENTRY_COUNT = 1024;
localparam  integer AI_TRACE_VISIBLE_ENTRY_COUNT = 896;
localparam  integer TABLE_ENTRY_STRIDE_BYTES = 32;
localparam  integer TX_EXEC_TABLE_BASE = 16'h1000;
localparam  integer TX_EXEC_TABLE_BYTES = TX_SCHED_VISIBLE_ENTRY_COUNT*TABLE_ENTRY_STRIDE_BYTES;
localparam  integer RX_EXEC_TABLE_BASE = TX_EXEC_TABLE_BASE + TX_EXEC_TABLE_BYTES;
localparam  integer RX_EXEC_TABLE_BYTES = RX_SCHED_VISIBLE_ENTRY_COUNT*TABLE_ENTRY_STRIDE_BYTES;
localparam  integer AI_TRACE_TABLE_BASE = RX_EXEC_TABLE_BASE + RX_EXEC_TABLE_BYTES;
localparam  integer AI_TRACE_TABLE_BYTES = AI_TRACE_VISIBLE_ENTRY_COUNT*TABLE_ENTRY_STRIDE_BYTES;
localparam  integer APP_PORT_SEL_WIDTH = PORTS_PER_IF > 1 ? $clog2(PORTS_PER_IF) : 1;

reg [31:0]  reg_ctrl                        = 1'b0;
reg         reg_ctrl_enable                 = 1'b0;     // Global enable for the entire subsystem.  Must be set for any other control bits to have effect.
reg         reg_ctrl_reset                  = 1'b0;     // Reset for the entire subsystem.  Must be set for any other control bits to have effect.
// This register is kept for control-plane ABI continuity.  The current
// compiled-window executor does not consult a trigger Ethertype anymore.
reg [15:0]  reg_ctrl_ethertype              = 16'hAE86;
reg         reg_exec_enable                 = 1'b1;     // Global enable for schedule execution.  Allows software to prepare the schedule tables and then start execution at a precise time by setting this bit.
reg         reg_schedule_admin_bank         = 1'b1;     // Schedule table bank selected for host read/write access and for schedule activation when reg_schedule_arm is set.  The other bank is active and driving the execution outputs.
reg [31:0]  reg_legacy_window_period_ns     = 32'd10000;
reg [63:0]  reg_schedule_activate_time_ns   = 64'd0;    // Absolute PTP time in nanoseconds at which to activate the schedule when reg_schedule_arm is set.  This allows software to prepare the schedule tables, set an activation time in the future, and then arm the activation to ensure the schedule starts at the precise desired time.
reg         reg_schedule_arm                = 1'b0;     // When set, the schedule in the bank indicated by reg_schedule_admin_bank will be activated at the time specified in reg_schedule_activate_time_ns.  This bit is self-clearing.
reg         reg_consensus_enable            = 1'b1;     // Separate app-level enable so consensus can be disabled while the shared subsystem stays up.
reg         reg_consensus_clear_halt        = 1'b0;     // One-cycle pulse that requests the consensus core to leave halt state and reinitialize protocol state.
reg         reg_ai_enable                   = 1'b0;     // AI trace enable.  When set, AI-generated trace entries will be written to the AI trace table and reflected in the AI trace table read data and pkt_sent_count registers.
// Legacy counter registers retained for software compatibility.  They are not
// yet driven by the reworked subsystem datapath and therefore currently read as
// sticky zero unless software explicitly clears them.
reg [31:0]  reg_cnt_rx_hit                  = 0;
reg [31:0]  reg_cnt_rx_pass                 = 0;
reg [31:0]  reg_cnt_tx_inj                  = 0;
reg [31:0]  reg_cnt_err                     = 0;
reg         reg_cnt_clear_r                 = 1'b0;

reg [AXIL_APP_CTRL_ADDR_WIDTH-1:0]         reg_wr_addr;
reg [AXIL_APP_CTRL_DATA_WIDTH-1:0]         reg_wr_data;
reg [AXIL_APP_CTRL_STRB_WIDTH-1:0]         reg_wr_strb;
reg                                        reg_wr_en;
reg                                        reg_wr_ack;
reg [AXIL_APP_CTRL_ADDR_WIDTH-1:0]         reg_rd_addr;
reg [AXIL_APP_CTRL_DATA_WIDTH-1:0]         reg_rd_data;
reg                                        reg_rd_en;
reg                                        reg_rd_ack;

wire ctrl_wr_sel = reg_wr_en && reg_wr_addr[15:12] == 4'h0;
wire ctrl_rd_sel = reg_rd_en && reg_rd_addr[15:12] == 4'h0;

wire tx_schedule_wr_sel = reg_wr_en &&
    reg_wr_addr >= TX_EXEC_TABLE_BASE &&
    reg_wr_addr < (TX_EXEC_TABLE_BASE + TX_EXEC_TABLE_BYTES);
wire [AXIL_APP_CTRL_ADDR_WIDTH-1:0] tx_schedule_wr_offset = reg_wr_addr - TX_EXEC_TABLE_BASE;
wire [TX_SCHED_ENTRY_INDEX_WIDTH-1:0] tx_schedule_wr_entry = tx_schedule_wr_offset[AXIL_APP_CTRL_ADDR_WIDTH-1:5];
wire [2:0] tx_schedule_wr_word = tx_schedule_wr_offset[4:2];
wire tx_schedule_rd_sel = reg_rd_en &&
    reg_rd_addr >= TX_EXEC_TABLE_BASE &&
    reg_rd_addr < (TX_EXEC_TABLE_BASE + TX_EXEC_TABLE_BYTES);
wire [AXIL_APP_CTRL_ADDR_WIDTH-1:0] tx_schedule_rd_offset = reg_rd_addr - TX_EXEC_TABLE_BASE;
wire [TX_SCHED_ENTRY_INDEX_WIDTH-1:0] tx_schedule_rd_entry = tx_schedule_rd_offset[AXIL_APP_CTRL_ADDR_WIDTH-1:5];
wire [2:0] tx_schedule_rd_word = tx_schedule_rd_offset[4:2];

wire rx_schedule_wr_sel = reg_wr_en &&
    reg_wr_addr >= RX_EXEC_TABLE_BASE &&
    reg_wr_addr < (RX_EXEC_TABLE_BASE + RX_EXEC_TABLE_BYTES);
wire [AXIL_APP_CTRL_ADDR_WIDTH-1:0] rx_schedule_wr_offset = reg_wr_addr - RX_EXEC_TABLE_BASE;
wire [RX_SCHED_ENTRY_INDEX_WIDTH-1:0] rx_schedule_wr_entry = rx_schedule_wr_offset[AXIL_APP_CTRL_ADDR_WIDTH-1:5];
wire [2:0] rx_schedule_wr_word = rx_schedule_wr_offset[4:2];
wire rx_schedule_rd_sel = reg_rd_en &&
    reg_rd_addr >= RX_EXEC_TABLE_BASE &&
    reg_rd_addr < (RX_EXEC_TABLE_BASE + RX_EXEC_TABLE_BYTES);
wire [AXIL_APP_CTRL_ADDR_WIDTH-1:0] rx_schedule_rd_offset = reg_rd_addr - RX_EXEC_TABLE_BASE;
wire [RX_SCHED_ENTRY_INDEX_WIDTH-1:0] rx_schedule_rd_entry = rx_schedule_rd_offset[AXIL_APP_CTRL_ADDR_WIDTH-1:5];
wire [2:0] rx_schedule_rd_word = rx_schedule_rd_offset[4:2];

wire ai_trace_wr_sel = reg_wr_en &&
    reg_wr_addr >= AI_TRACE_TABLE_BASE &&
    reg_wr_addr < (AI_TRACE_TABLE_BASE + AI_TRACE_TABLE_BYTES);
wire [AXIL_APP_CTRL_ADDR_WIDTH-1:0] ai_trace_wr_offset = reg_wr_addr - AI_TRACE_TABLE_BASE;
wire [AI_TRACE_ENTRY_INDEX_WIDTH-1:0] ai_trace_wr_entry = ai_trace_wr_offset[AXIL_APP_CTRL_ADDR_WIDTH-1:5];
wire [2:0] ai_trace_wr_word = ai_trace_wr_offset[4:2];
wire ai_trace_rd_sel = reg_rd_en &&
    reg_rd_addr >= AI_TRACE_TABLE_BASE &&
    reg_rd_addr < (AI_TRACE_TABLE_BASE + AI_TRACE_TABLE_BYTES);
wire [AXIL_APP_CTRL_ADDR_WIDTH-1:0] ai_trace_rd_offset = reg_rd_addr - AI_TRACE_TABLE_BASE;
wire [AI_TRACE_ENTRY_INDEX_WIDTH-1:0] ai_trace_rd_entry = ai_trace_rd_offset[AXIL_APP_CTRL_ADDR_WIDTH-1:5];
wire [2:0] ai_trace_rd_word = ai_trace_rd_offset[4:2];

wire [31:0] exec_schedule_rd_data;
wire [31:0] rx_exec_schedule_rd_data;
wire [63:0] exec_current_window_id;
wire [TX_SCHED_ENTRY_INDEX_WIDTH-1:0] exec_current_entry_ptr;
wire exec_window_open_pulse;
wire exec_window_close_pulse;
wire exec_commit_start_pulse;
wire exec_valid;
wire exec_tx_allowed;
wire exec_window_active;
wire [7:0] exec_target_port;
wire [15:0] exec_target_queue;
wire exec_active_bank;
wire exec_pending_valid;
wire exec_pending_bank;
wire [63:0] exec_pending_time_ns;
wire [63:0] exec_active_entry_start_time_ns;
wire [63:0] exec_active_entry_end_time_ns;
wire [31:0] exec_active_entry_meta;
wire [31:0] exec_active_entry_route;
wire [31:0] exec_active_entry_flow;
wire [7:0] exec_app_id;
wire [7:0] exec_plane_id;
wire [7:0] exec_opcode;
wire [15:0] exec_context_id;
wire [15:0] exec_dst_node_id;
wire [15:0] exec_flow_id;
wire [63:0] rx_exec_current_window_id;
wire [RX_SCHED_ENTRY_INDEX_WIDTH-1:0] rx_exec_current_entry_ptr;
wire rx_exec_window_open_pulse;
wire rx_exec_window_close_pulse;
wire rx_exec_commit_start_pulse;
wire rx_exec_valid;
wire rx_exec_rx_enabled;
wire rx_exec_window_active;
wire [7:0] rx_exec_target_port;
wire [15:0] rx_exec_target_queue;
wire [63:0] rx_exec_active_entry_start_time_ns;
wire [63:0] rx_exec_active_entry_end_time_ns;
wire [31:0] rx_exec_active_entry_meta;
wire [31:0] rx_exec_active_entry_route;
wire [31:0] rx_exec_active_entry_flow;
wire [7:0] rx_exec_app_id;
wire [7:0] rx_exec_plane_id;
wire [7:0] rx_exec_opcode;
wire [15:0] rx_exec_context_id;
wire [15:0] rx_exec_dst_node_id;
wire [15:0] rx_exec_flow_id;
wire [31:0] ai_trace_rd_data;
wire [31:0] ai_pkt_sent_count;
wire [31:0] ai_rx_pkt_count;
wire [31:0] ai_rx_byte_count;
wire [31:0] ai_rx_match_count;
wire [31:0] ai_rx_drop_count;
wire        consensus_system_halt;
wire [3:0]  consensus_debug_state;

wire [AXIS_IF_DATA_WIDTH-1:0]           app_tx_tdata;
wire [AXIS_IF_KEEP_WIDTH-1:0]           app_tx_tkeep;
wire                                    app_tx_tvalid;
wire                                    app_tx_tlast;
wire [AXIS_IF_TX_USER_WIDTH-1:0]        app_tx_tuser;
wire                                    app_tx_tready;
wire                                    app_tx_valid;
wire [AXIS_IF_DATA_WIDTH-1:0]           app_rx_tdata;
wire [AXIS_IF_KEEP_WIDTH-1:0]           app_rx_tkeep;
wire                                    app_rx_tvalid;
wire                                    app_rx_tlast;
wire [AXIS_IF_RX_USER_WIDTH-1:0]        app_rx_tuser;
wire                                    app_rx_tready;
wire [AXIS_IF_TX_DEST_WIDTH-1:0]        app_tx_tdest;
wire                                    subsystem_enable = reg_ctrl_enable && !reg_ctrl_reset;

axil_reg_if #(
    .ADDR_WIDTH(AXIL_APP_CTRL_ADDR_WIDTH),
    .DATA_WIDTH(AXIL_APP_CTRL_DATA_WIDTH),
    .STRB_WIDTH(AXIL_APP_CTRL_STRB_WIDTH),
    .TIMEOUT(0)
)
axil_reg_if_inst (
    .clk(clk),
    .rst(rst),
    .s_axil_awaddr(s_axil_app_ctrl_awaddr),
    .s_axil_awprot(s_axil_app_ctrl_awprot),
    .s_axil_awvalid(s_axil_app_ctrl_awvalid),
    .s_axil_awready(s_axil_app_ctrl_awready),
    .s_axil_wdata(s_axil_app_ctrl_wdata),
    .s_axil_wstrb(s_axil_app_ctrl_wstrb),
    .s_axil_wvalid(s_axil_app_ctrl_wvalid),
    .s_axil_wready(s_axil_app_ctrl_wready),
    .s_axil_bresp(s_axil_app_ctrl_bresp),
    .s_axil_bvalid(s_axil_app_ctrl_bvalid),
    .s_axil_bready(s_axil_app_ctrl_bready),
    .s_axil_araddr(s_axil_app_ctrl_araddr),
    .s_axil_arprot(s_axil_app_ctrl_arprot),
    .s_axil_arvalid(s_axil_app_ctrl_arvalid),
    .s_axil_arready(s_axil_app_ctrl_arready),
    .s_axil_rdata(s_axil_app_ctrl_rdata),
    .s_axil_rresp(s_axil_app_ctrl_rresp),
    .s_axil_rvalid(s_axil_app_ctrl_rvalid),
    .s_axil_rready(s_axil_app_ctrl_rready),
    .reg_wr_addr(reg_wr_addr),
    .reg_wr_data(reg_wr_data),
    .reg_wr_strb(reg_wr_strb),
    .reg_wr_en(reg_wr_en),
    .reg_wr_wait(1'b0),
    .reg_wr_ack(reg_wr_ack),
    .reg_rd_addr(reg_rd_addr),
    .reg_rd_data(reg_rd_data),
    .reg_rd_en(reg_rd_en),
    .reg_rd_wait(1'b0),
    .reg_rd_ack(reg_rd_ack)
);

sync_schedule_executor #(
    .TX_ENTRY_INDEX_WIDTH(TX_SCHED_ENTRY_INDEX_WIDTH),
    .TX_ENTRY_COUNT(TX_SCHED_ENTRY_COUNT),
    .RX_ENTRY_INDEX_WIDTH(RX_SCHED_ENTRY_INDEX_WIDTH),
    .RX_ENTRY_COUNT(RX_SCHED_ENTRY_COUNT)
)
sync_schedule_executor_inst (
    .clk(clk),
    .rst(rst),
    .i_enable(subsystem_enable),
    .i_ptp_time_ns({32'd0, ptp_sync_ts_tod[47:16]}),
    .cfg_exec_enable(reg_exec_enable),
    .cfg_set_pending_valid(reg_schedule_arm),
    .cfg_set_pending_bank(reg_schedule_admin_bank),
    .cfg_set_pending_time_ns(reg_schedule_activate_time_ns),
    .cfg_tx_wr_en(tx_schedule_wr_sel),
    .cfg_tx_wr_bank(reg_schedule_admin_bank),
    .cfg_tx_wr_entry(tx_schedule_wr_entry),
    .cfg_tx_wr_word(tx_schedule_wr_word),
    .cfg_tx_wr_data(reg_wr_data),
    .cfg_tx_rd_bank(reg_schedule_admin_bank),
    .cfg_tx_rd_entry(tx_schedule_rd_entry),
    .cfg_tx_rd_word(tx_schedule_rd_word),
    .cfg_tx_rd_data(exec_schedule_rd_data),
    .cfg_rx_wr_en(rx_schedule_wr_sel),
    .cfg_rx_wr_bank(reg_schedule_admin_bank),
    .cfg_rx_wr_entry(rx_schedule_wr_entry),
    .cfg_rx_wr_word(rx_schedule_wr_word),
    .cfg_rx_wr_data(reg_wr_data),
    .cfg_rx_rd_bank(reg_schedule_admin_bank),
    .cfg_rx_rd_entry(rx_schedule_rd_entry),
    .cfg_rx_rd_word(rx_schedule_rd_word),
    .cfg_rx_rd_data(rx_exec_schedule_rd_data),
    .o_current_window_id(exec_current_window_id),
    .o_current_entry_ptr(exec_current_entry_ptr),
    .o_window_open_pulse(exec_window_open_pulse),
    .o_window_close_pulse(exec_window_close_pulse),
    .o_commit_start_pulse(exec_commit_start_pulse),
    .o_exec_valid(exec_valid),
    .o_tx_allowed(exec_tx_allowed),
    .o_window_active(exec_window_active),
    .o_target_port(exec_target_port),
    .o_target_queue(exec_target_queue),
    .o_app_id(exec_app_id),
    .o_plane_id(exec_plane_id),
    .o_opcode(exec_opcode),
    .o_context_id(exec_context_id),
    .o_dst_node_id(exec_dst_node_id),
    .o_flow_id(exec_flow_id),
    .o_active_bank(exec_active_bank),
    .o_pending_valid(exec_pending_valid),
    .o_pending_bank(exec_pending_bank),
    .o_pending_time_ns(exec_pending_time_ns),
    .o_active_entry_start_time_ns(exec_active_entry_start_time_ns),
    .o_active_entry_end_time_ns(exec_active_entry_end_time_ns),
    .o_active_entry_meta(exec_active_entry_meta),
    .o_active_entry_route(exec_active_entry_route),
    .o_active_entry_flow(exec_active_entry_flow),
    .o_rx_current_window_id(rx_exec_current_window_id),
    .o_rx_current_entry_ptr(rx_exec_current_entry_ptr),
    .o_rx_window_open_pulse(rx_exec_window_open_pulse),
    .o_rx_window_close_pulse(rx_exec_window_close_pulse),
    .o_rx_commit_start_pulse(rx_exec_commit_start_pulse),
    .o_rx_exec_valid(rx_exec_valid),
    .o_rx_enabled(rx_exec_rx_enabled),
    .o_rx_window_active(rx_exec_window_active),
    .o_rx_target_port(rx_exec_target_port),
    .o_rx_target_queue(rx_exec_target_queue),
    .o_rx_app_id(rx_exec_app_id),
    .o_rx_plane_id(rx_exec_plane_id),
    .o_rx_opcode(rx_exec_opcode),
    .o_rx_context_id(rx_exec_context_id),
    .o_rx_dst_node_id(rx_exec_dst_node_id),
    .o_rx_flow_id(rx_exec_flow_id),
    .o_rx_active_entry_start_time_ns(rx_exec_active_entry_start_time_ns),
    .o_rx_active_entry_end_time_ns(rx_exec_active_entry_end_time_ns),
    .o_rx_active_entry_meta(rx_exec_active_entry_meta),
    .o_rx_active_entry_route(rx_exec_active_entry_route),
    .o_rx_active_entry_flow(rx_exec_active_entry_flow)
);

sync_dcn_apps #(
    .P_NODE_ID(P_NODE_ID),
    .PTP_TS_WIDTH(PTP_TS_WIDTH),
    .AXIS_DATA_WIDTH(AXIS_IF_DATA_WIDTH),
    .AXIS_KEEP_WIDTH(AXIS_IF_KEEP_WIDTH),
    .AXIS_TX_USER_WIDTH(AXIS_IF_TX_USER_WIDTH),
    .AXIS_RX_USER_WIDTH(AXIS_IF_RX_USER_WIDTH),
    .AXIS_USER_WIDTH(AXIS_IF_TX_USER_WIDTH),
    .TX_TAG_WIDTH(TX_TAG_WIDTH)
)
sync_dcn_apps_inst (
    .clk(clk),
    .rst(rst),
    .i_enable(subsystem_enable),
    .i_tx_current_window_id(exec_current_window_id),
    .i_tx_window_open_pulse(exec_window_open_pulse),
    .i_tx_commit_start_pulse(exec_commit_start_pulse),
    .i_tx_window_close_pulse(exec_window_close_pulse),
    .i_tx_allowed(exec_tx_allowed),
    .i_tx_app_id(exec_app_id),
    .i_tx_opcode(exec_opcode),
    .i_tx_context_id(exec_context_id),
    .i_rx_current_window_id(rx_exec_current_window_id),
    .i_rx_window_open_pulse(rx_exec_window_open_pulse),
    .i_rx_commit_start_pulse(rx_exec_commit_start_pulse),
    .i_rx_window_close_pulse(rx_exec_window_close_pulse),
    .i_rx_enabled(rx_exec_rx_enabled),
    .i_rx_app_id(rx_exec_app_id),
    .i_rx_opcode(rx_exec_opcode),
    .i_rx_context_id(rx_exec_context_id),
    .i_consensus_enable(reg_consensus_enable),
    .i_consensus_clear_halt(reg_consensus_clear_halt),
    .o_consensus_system_halt(consensus_system_halt),
    .o_consensus_debug_state(consensus_debug_state),
    .i_ai_enable(reg_ai_enable),
    .i_ai_cfg_wr_en(ai_trace_wr_sel),
    .i_ai_cfg_wr_entry(ai_trace_wr_entry),
    .i_ai_cfg_wr_word(ai_trace_wr_word),
    .i_ai_cfg_wr_data(reg_wr_data),
    .i_ai_cfg_rd_entry(ai_trace_rd_entry),
    .i_ai_cfg_rd_word(ai_trace_rd_word),
    .o_ai_cfg_rd_data(ai_trace_rd_data),
    .o_ai_pkt_sent_count(ai_pkt_sent_count),
    .o_ai_rx_pkt_count(ai_rx_pkt_count),
    .o_ai_rx_byte_count(ai_rx_byte_count),
    .o_ai_rx_match_count(ai_rx_match_count),
    .o_ai_rx_drop_count(ai_rx_drop_count),
    .s_axis_app_rx_tdata(app_rx_tdata),
    .s_axis_app_rx_tkeep(app_rx_tkeep),
    .s_axis_app_rx_tvalid(app_rx_tvalid),
    .s_axis_app_rx_tlast(app_rx_tlast),
    .s_axis_app_rx_tuser(app_rx_tuser),
    .s_axis_app_rx_tready(app_rx_tready),
    .m_axis_app_tx_tdata(app_tx_tdata),
    .m_axis_app_tx_tkeep(app_tx_tkeep),
    .m_axis_app_tx_tvalid(app_tx_tvalid),
    .m_axis_app_tx_tready(app_tx_tready),
    .m_axis_app_tx_tlast(app_tx_tlast),
    .m_axis_app_tx_tuser(app_tx_tuser),
    .o_app_tx_valid(app_tx_valid)
);

sync_dcn_datapath #(
    .AXIS_DATA_WIDTH(AXIS_IF_DATA_WIDTH),
    .AXIS_KEEP_WIDTH(AXIS_IF_KEEP_WIDTH),
    .AXIS_TX_USER_WIDTH(AXIS_IF_TX_USER_WIDTH),
    .AXIS_RX_USER_WIDTH(AXIS_IF_RX_USER_WIDTH),
    .P_CONSENSUS_ETHERTYPE(16'h88B5),
    .P_AI_ETHERTYPE(16'h88B6),
    .P_HDR_ETHERTYPE_OFFSET_BYTES(12)
)
sync_dcn_datapath_inst (
    .clk(clk),
    .rst(rst),
    .i_enable(subsystem_enable),
    .s_axis_dma_tx_tdata(s_axis_if_tx_tdata),
    .s_axis_dma_tx_tkeep(s_axis_if_tx_tkeep),
    .s_axis_dma_tx_tvalid(s_axis_if_tx_tvalid),
    .s_axis_dma_tx_tlast(s_axis_if_tx_tlast),
    .s_axis_dma_tx_tuser(s_axis_if_tx_tuser),
    .s_axis_dma_tx_tready(s_axis_if_tx_tready),
    .s_axis_app_tx_tdata(app_tx_tdata),
    .s_axis_app_tx_tkeep(app_tx_tkeep),
    .s_axis_app_tx_tvalid(app_tx_tvalid),
    .s_axis_app_tx_tlast(app_tx_tlast),
    .s_axis_app_tx_tuser(app_tx_tuser),
    .s_axis_app_tx_tready(app_tx_tready),
    .i_app_tx_valid(app_tx_valid),
    .o_app_tx_selected(),
    .m_axis_mac_tx_tdata(m_axis_if_tx_tdata),
    .m_axis_mac_tx_tkeep(m_axis_if_tx_tkeep),
    .m_axis_mac_tx_tvalid(m_axis_if_tx_tvalid),
    .m_axis_mac_tx_tlast(m_axis_if_tx_tlast),
    .m_axis_mac_tx_tuser(m_axis_if_tx_tuser),
    .m_axis_mac_tx_tready(m_axis_if_tx_tready),
    .s_axis_mac_rx_tdata(s_axis_if_rx_tdata),
    .s_axis_mac_rx_tkeep(s_axis_if_rx_tkeep),
    .s_axis_mac_rx_tvalid(s_axis_if_rx_tvalid),
    .s_axis_mac_rx_tlast(s_axis_if_rx_tlast),
    .s_axis_mac_rx_tuser(s_axis_if_rx_tuser),
    .s_axis_mac_rx_tready(s_axis_if_rx_tready),
    .m_axis_dma_rx_tdata(m_axis_if_rx_tdata),
    .m_axis_dma_rx_tkeep(m_axis_if_rx_tkeep),
    .m_axis_dma_rx_tvalid(m_axis_if_rx_tvalid),
    .m_axis_dma_rx_tlast(m_axis_if_rx_tlast),
    .m_axis_dma_rx_tuser(m_axis_if_rx_tuser),
    .m_axis_dma_rx_tready(m_axis_if_rx_tready),
    .m_axis_app_rx_tdata(app_rx_tdata),
    .m_axis_app_rx_tkeep(app_rx_tkeep),
    .m_axis_app_rx_tvalid(app_rx_tvalid),
    .m_axis_app_rx_tlast(app_rx_tlast),
    .m_axis_app_rx_tuser(app_rx_tuser),
    .m_axis_app_rx_tready(app_rx_tready)
);

sync_tx_dest_format #(
    .DEST_WIDTH(AXIS_IF_TX_DEST_WIDTH),
    .PORT_SEL_WIDTH(APP_PORT_SEL_WIDTH),
    .EPS_BASE_PORT(8'd0),
    .OCS_BASE_PORT(8'd1)
)
sync_tx_dest_format_inst (
    .i_plane_id(exec_plane_id),
    .i_target_port(exec_target_port),
    .o_tdest(app_tx_tdest)
);

assign m_axis_if_tx_tid = s_axis_if_tx_tid;
assign m_axis_if_tx_tdest = app_tx_valid ? app_tx_tdest : s_axis_if_tx_tdest;
assign m_axis_if_rx_tid = s_axis_if_rx_tid;
assign m_axis_if_rx_tdest = s_axis_if_rx_tdest;

always @(posedge clk) begin
    if (rst) begin
        reg_ctrl_enable <= 1'b0;
        reg_ctrl_reset <= 1'b0;
        reg_ctrl_ethertype <= 16'hAE86;
        reg_exec_enable <= 1'b1;
        reg_schedule_admin_bank <= 1'b1;
        reg_legacy_window_period_ns <= 32'd10000;
        reg_schedule_activate_time_ns <= 64'd0;
        reg_schedule_arm <= 1'b0;
        reg_consensus_enable <= 1'b1;
        reg_consensus_clear_halt <= 1'b0;
        reg_ai_enable <= 1'b0;
        reg_cnt_clear_r <= 1'b0;
    end else begin
        reg_cnt_clear_r <= 1'b0;
        reg_schedule_arm <= 1'b0;
        reg_consensus_clear_halt <= 1'b0;
        reg_ctrl_enable <= reg_ctrl[0];
        reg_ctrl_reset <= reg_ctrl[1];

        if (ctrl_wr_sel) begin
            case ({reg_wr_addr[11:2], 2'b00})
                12'h008: reg_ctrl <= reg_wr_data;
                12'h00C: reg_ctrl_ethertype <= reg_wr_data[15:0];
                12'h014: reg_exec_enable <= reg_wr_data[0];
                12'h018: reg_legacy_window_period_ns <= reg_wr_data;
                12'h01C: reg_schedule_activate_time_ns[31:0] <= reg_wr_data;
                12'h020: reg_schedule_activate_time_ns[63:32] <= reg_wr_data;
                12'h024: begin
                    reg_schedule_admin_bank <= reg_wr_data[0];
                    reg_schedule_arm <= reg_wr_data[1];
                end
                12'h07C: begin
                    reg_consensus_enable <= reg_wr_data[0];
                    reg_consensus_clear_halt <= reg_wr_data[1];
                end
                12'h054: reg_ai_enable <= reg_wr_data[0];
                12'h094: reg_cnt_clear_r <= reg_wr_data[0];
                default: ;
            endcase
        end

        if (reg_wr_en) begin
            reg_wr_ack <= 1'b1;
        end else begin
            reg_wr_ack <= 1'b0;
        end

        if (reg_cnt_clear_r) begin
            reg_cnt_rx_hit <= 0;
            reg_cnt_rx_pass <= 0;
            reg_cnt_tx_inj <= 0;
            reg_cnt_err <= 0;
        end
    end
end

always @(posedge clk) begin
    if (rst) begin
        reg_rd_data <= 0;
    end else begin
        if (ctrl_rd_sel) begin
            case ({reg_rd_addr[11:2], 2'b00})
                12'h000: reg_rd_data <= REG_ID_VAL;
                12'h004: reg_rd_data <= REG_VERSION_VAL;
                12'h008: reg_rd_data <= reg_ctrl;
                12'h00C: reg_rd_data <= {16'b0, reg_ctrl_ethertype};
                12'h010: reg_rd_data <= {30'b0, reg_ctrl_enable, reg_ctrl_reset};
                12'h014: reg_rd_data <= {29'd0, exec_pending_valid, exec_active_bank, reg_exec_enable};
                12'h018: reg_rd_data <= reg_legacy_window_period_ns;
                12'h01C: reg_rd_data <= reg_schedule_activate_time_ns[31:0];
                12'h020: reg_rd_data <= reg_schedule_activate_time_ns[63:32];
                12'h024: reg_rd_data <= {30'd0, 1'b0, reg_schedule_admin_bank};
                12'h028: reg_rd_data <= exec_current_window_id[31:0];
                12'h02C: reg_rd_data <= exec_current_window_id[63:32];
                12'h030: reg_rd_data <= {exec_plane_id, exec_target_queue, exec_target_port};
                12'h034: reg_rd_data <= {28'd0, exec_valid, rx_exec_rx_enabled, exec_tx_allowed, exec_window_active};
                12'h038: reg_rd_data <= {{(32-TX_SCHED_ENTRY_INDEX_WIDTH){1'b0}}, exec_current_entry_ptr};
                12'h03C: reg_rd_data <= exec_active_entry_start_time_ns[31:0];
                12'h040: reg_rd_data <= exec_active_entry_start_time_ns[63:32];
                12'h044: reg_rd_data <= exec_active_entry_end_time_ns[31:0];
                12'h048: reg_rd_data <= {exec_plane_id, exec_app_id, exec_opcode, 8'd0};
                12'h04C: reg_rd_data <= {16'd0, exec_context_id};
                12'h050: reg_rd_data <= exec_active_entry_end_time_ns[63:32];
                12'h054: reg_rd_data <= {31'd0, reg_ai_enable};
                12'h058: reg_rd_data <= ai_pkt_sent_count;
                12'h05C: reg_rd_data <= exec_active_entry_meta;
                12'h060: reg_rd_data <= {30'd0, exec_pending_bank, exec_active_bank};
                12'h064: reg_rd_data <= exec_pending_time_ns[31:0];
                12'h068: reg_rd_data <= exec_pending_time_ns[63:32];
                12'h06C: reg_rd_data <= ai_rx_pkt_count;
                12'h070: reg_rd_data <= ai_rx_byte_count;
                12'h074: reg_rd_data <= ai_rx_match_count;
                12'h078: reg_rd_data <= ai_rx_drop_count;
                12'h07C: reg_rd_data <= {30'd0, 1'b0, reg_consensus_enable};
                12'h080: reg_rd_data <= reg_cnt_rx_hit;
                12'h084: reg_rd_data <= reg_cnt_rx_pass;
                12'h088: reg_rd_data <= reg_cnt_tx_inj;
                12'h08C: reg_rd_data <= {24'd0, consensus_debug_state, 3'd0, consensus_system_halt};
                12'h090: reg_rd_data <= reg_cnt_err;
                12'h098: reg_rd_data <= {28'd0, rx_exec_valid, rx_exec_rx_enabled, 1'b0, rx_exec_window_active};
                12'h09C: reg_rd_data <= {{(32-RX_SCHED_ENTRY_INDEX_WIDTH){1'b0}}, rx_exec_current_entry_ptr};
                12'h0A0: reg_rd_data <= {rx_exec_plane_id, rx_exec_app_id, rx_exec_opcode, 8'd0};
                12'h0A4: reg_rd_data <= {16'd0, rx_exec_context_id};
                12'h0A8: reg_rd_data <= rx_exec_active_entry_start_time_ns[31:0];
                12'h0AC: reg_rd_data <= rx_exec_active_entry_start_time_ns[63:32];
                12'h0B0: reg_rd_data <= rx_exec_active_entry_end_time_ns[31:0];
                12'h0B4: reg_rd_data <= rx_exec_active_entry_end_time_ns[63:32];
                12'h0B8: reg_rd_data <= rx_exec_active_entry_meta;
                default: reg_rd_data <= 0;
            endcase
        end else if (reg_rd_en) begin
            reg_rd_data <= 0;
        end

        if (tx_schedule_rd_sel) begin
            reg_rd_data <= exec_schedule_rd_data;
        end
        if (rx_schedule_rd_sel) begin
            reg_rd_data <= rx_exec_schedule_rd_data;
        end
        if (ai_trace_rd_sel) begin
            reg_rd_data <= ai_trace_rd_data;
        end

        if (reg_rd_en) begin
            reg_rd_ack <= 1'b1;
        end else begin
            reg_rd_ack <= 1'b0;
        end
    end
end

endmodule

`default_nettype wire
