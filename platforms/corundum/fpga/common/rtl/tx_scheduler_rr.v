// SPDX-License-Identifier: BSD-2-Clause-Views
/*
 * Copyright (c) 2019-2024 The Regents of the University of California
 */

// Language: Verilog 2001

`resetall
`timescale 1ns / 1ps
`default_nettype none

/*
 * Transmit scheduler (round-robin)
 */
module tx_scheduler_rr #
(
    // Scheduler configuration
    parameter LEN_WIDTH = 16,
    parameter REQ_DEST_WIDTH = 8,
    parameter REQ_TAG_WIDTH = 8,
    parameter QUEUE_INDEX_WIDTH = 6,
    parameter PIPELINE = 2,
    parameter SCHED_CTRL_ENABLE = 0,
    parameter REQ_DEST_DEFAULT = 0,
    parameter MAX_TX_SIZE = 9216,
    parameter FC_SCALE = 64,

    // AXI lite interface configuration
    parameter AXIL_BASE_ADDR = 0,
    parameter AXIL_DATA_WIDTH = 32,
    parameter AXIL_ADDR_WIDTH = QUEUE_INDEX_WIDTH+2,
    parameter AXIL_STRB_WIDTH = (AXIL_DATA_WIDTH/8),

    // Register interface configuration
    parameter REG_ADDR_WIDTH = $clog2(64),
    parameter REG_DATA_WIDTH = AXIL_DATA_WIDTH,
    parameter REG_STRB_WIDTH = (REG_DATA_WIDTH/8),
    parameter RB_BLOCK_TYPE = 32'h0000C040,
    parameter RB_BASE_ADDR = 0,
    parameter RB_NEXT_PTR = 0
)
(
    input  wire                          clk,
    input  wire                          rst,

    /*
     * Control register interface
     */
    input  wire [REG_ADDR_WIDTH-1:0]     ctrl_reg_wr_addr,
    input  wire [REG_DATA_WIDTH-1:0]     ctrl_reg_wr_data,
    input  wire [REG_STRB_WIDTH-1:0]     ctrl_reg_wr_strb,
    input  wire                          ctrl_reg_wr_en,
    output wire                          ctrl_reg_wr_wait,
    output wire                          ctrl_reg_wr_ack,
    input  wire [REG_ADDR_WIDTH-1:0]     ctrl_reg_rd_addr,
    input  wire                          ctrl_reg_rd_en,
    output wire [REG_DATA_WIDTH-1:0]     ctrl_reg_rd_data,
    output wire                          ctrl_reg_rd_wait,
    output wire                          ctrl_reg_rd_ack,

    /*
     * Transmit request output (queue index)
     */
    output wire [QUEUE_INDEX_WIDTH-1:0]  m_axis_tx_req_queue,
    output wire [REQ_DEST_WIDTH-1:0]     m_axis_tx_req_dest,
    output wire [REQ_TAG_WIDTH-1:0]      m_axis_tx_req_tag,
    output wire                          m_axis_tx_req_valid,
    input  wire                          m_axis_tx_req_ready,

    /*
     * Transmit request status input
     */
    input  wire                          s_axis_tx_status_dequeue_empty,
    input  wire                          s_axis_tx_status_dequeue_error,
    input  wire [QUEUE_INDEX_WIDTH-1:0]  s_axis_tx_status_dequeue_queue,
    input  wire [REQ_TAG_WIDTH-1:0]      s_axis_tx_status_dequeue_tag,
    input  wire                          s_axis_tx_status_dequeue_valid,

    input  wire                          s_axis_tx_status_start_error,
    input  wire [LEN_WIDTH-1:0]          s_axis_tx_status_start_len,
    input  wire [QUEUE_INDEX_WIDTH-1:0]  s_axis_tx_status_start_queue,
    input  wire [REQ_TAG_WIDTH-1:0]      s_axis_tx_status_start_tag,
    input  wire                          s_axis_tx_status_start_valid,

    input  wire [LEN_WIDTH-1:0]          s_axis_tx_status_finish_len,
    input  wire [QUEUE_INDEX_WIDTH-1:0]  s_axis_tx_status_finish_queue,
    input  wire [REQ_TAG_WIDTH-1:0]      s_axis_tx_status_finish_tag,
    input  wire                          s_axis_tx_status_finish_valid,

    /*
     * Doorbell input
     */
    input  wire [QUEUE_INDEX_WIDTH-1:0]  s_axis_doorbell_queue,
    input  wire                          s_axis_doorbell_valid,

    /*
     * Scheduler control input
     */
    input  wire [QUEUE_INDEX_WIDTH-1:0]  s_axis_sched_ctrl_queue,
    input  wire                          s_axis_sched_ctrl_enable,
    input  wire                          s_axis_sched_ctrl_valid,
    output wire                          s_axis_sched_ctrl_ready,

    /*
     * AXI-Lite slave interface
     */
    input  wire [AXIL_ADDR_WIDTH-1:0]    s_axil_awaddr,
    input  wire [2:0]                    s_axil_awprot,
    input  wire                          s_axil_awvalid,
    output wire                          s_axil_awready,
    input  wire [AXIL_DATA_WIDTH-1:0]    s_axil_wdata,
    input  wire [AXIL_STRB_WIDTH-1:0]    s_axil_wstrb,
    input  wire                          s_axil_wvalid,
    output wire                          s_axil_wready,
    output wire [1:0]                    s_axil_bresp,
    output wire                          s_axil_bvalid,
    input  wire                          s_axil_bready,
    input  wire [AXIL_ADDR_WIDTH-1:0]    s_axil_araddr,
    input  wire [2:0]                    s_axil_arprot,
    input  wire                          s_axil_arvalid,
    output wire                          s_axil_arready,
    output wire [AXIL_DATA_WIDTH-1:0]    s_axil_rdata,
    output wire [1:0]                    s_axil_rresp,
    output wire                          s_axil_rvalid,
    input  wire                          s_axil_rready,

    /*
     * Control
     */
    input  wire                          enable,
    output wire                          active
);

localparam CL_FC_SCALE = $clog2(FC_SCALE);
localparam PKT_FC_W = 8;
localparam BUDGET_FC_W = LEN_WIDTH-CL_FC_SCALE;
localparam DATA_FC_W = BUDGET_FC_W+PKT_FC_W;
localparam TX_FC_W = 4;

localparam QUEUE_COUNT = 2**QUEUE_INDEX_WIDTH;

localparam GEN_ID_W = REQ_TAG_WIDTH < 8 ? REQ_TAG_WIDTH : 8;
localparam CL_OP_COUNT = PKT_FC_W;
localparam FINISH_FIFO_AW = CL_OP_COUNT;

localparam OUTPUT_FIFO_AW = $clog2(PIPELINE*2+2);

localparam RAM_WIDTH = 16;

localparam RBB = RB_BASE_ADDR & {REG_ADDR_WIDTH{1'b1}};

// check configuration
initial begin
    if (AXIL_DATA_WIDTH != 32) begin
        $error("Error: AXI lite interface width must be 32 (instance %m)");
        $finish;
    end

    if (AXIL_STRB_WIDTH * 8 != AXIL_DATA_WIDTH) begin
        $error("Error: AXI lite interface requires byte (8-bit) granularity (instance %m)");
        $finish;
    end

    if (AXIL_ADDR_WIDTH < QUEUE_INDEX_WIDTH+2) begin
        $error("Error: AXI lite address width too narrow (instance %m)");
        $finish;
    end

    if (PIPELINE < 2) begin
        $error("Error: PIPELINE must be at least 2 (instance %m)");
        $finish;
    end

    if (REG_DATA_WIDTH != 32) begin
        $error("Error: Register interface width must be 32 (instance %m)");
        $finish;
    end

    if (REG_STRB_WIDTH * 8 != REG_DATA_WIDTH) begin
        $error("Error: Register interface requires byte (8-bit) granularity (instance %m)");
        $finish;
    end

    if (REG_ADDR_WIDTH < $clog2(64)) begin
        $error("Error: Register address width too narrow (instance %m)");
        $finish;
    end

    if (RB_NEXT_PTR && RB_NEXT_PTR >= RB_BASE_ADDR && RB_NEXT_PTR < RB_BASE_ADDR + 64) begin
        $error("Error: RB_NEXT_PTR overlaps block (instance %m)");
        $finish;
    end
end

reg [PIPELINE-1:0] op_axil_write_pipe_reg = {PIPELINE{1'b0}}, op_axil_write_pipe_next;
reg [PIPELINE-1:0] op_axil_read_pipe_reg = {PIPELINE{1'b0}}, op_axil_read_pipe_next;
reg [PIPELINE-1:0] op_doorbell_pipe_reg = {PIPELINE{1'b0}}, op_doorbell_pipe_next;
reg [PIPELINE-1:0] op_req_pipe_reg = {PIPELINE{1'b0}}, op_req_pipe_next;
reg [PIPELINE-1:0] op_complete_pipe_reg = {PIPELINE{1'b0}}, op_complete_pipe_next;
reg [PIPELINE-1:0] op_ctrl_pipe_reg = {PIPELINE{1'b0}}, op_ctrl_pipe_next;
reg [PIPELINE-1:0] op_internal_pipe_reg = {PIPELINE{1'b0}}, op_internal_pipe_next;

reg [QUEUE_INDEX_WIDTH-1:0] queue_ram_addr_pipeline_reg[PIPELINE-1:0], queue_ram_addr_pipeline_next[PIPELINE-1:0];
reg [AXIL_DATA_WIDTH-1:0] write_data_pipeline_reg[PIPELINE-1:0], write_data_pipeline_next[PIPELINE-1:0];
reg [AXIL_STRB_WIDTH-1:0] write_strobe_pipeline_reg[PIPELINE-1:0], write_strobe_pipeline_next[PIPELINE-1:0];
reg [REQ_TAG_WIDTH-1:0] req_tag_pipeline_reg[PIPELINE-1:0], req_tag_pipeline_next[PIPELINE-1:0];

reg [REQ_DEST_WIDTH-1:0] tx_req_dest_reg = REQ_DEST_DEFAULT;

reg s_axis_sched_ctrl_ready_reg = 1'b0, s_axis_sched_ctrl_ready_next;

reg s_axil_awready_reg = 0, s_axil_awready_next;
reg s_axil_wready_reg = 0, s_axil_wready_next;
reg s_axil_bvalid_reg = 0, s_axil_bvalid_next;
reg s_axil_arready_reg = 0, s_axil_arready_next;
reg [AXIL_DATA_WIDTH-1:0] s_axil_rdata_reg = 0, s_axil_rdata_next;
reg s_axil_rvalid_reg = 0, s_axil_rvalid_next;

(* ramstyle = "no_rw_check" *)
reg [RAM_WIDTH-1:0] queue_ram[QUEUE_COUNT-1:0];
reg [QUEUE_INDEX_WIDTH-1:0] queue_ram_rd_addr;
reg [QUEUE_INDEX_WIDTH-1:0] queue_ram_wr_addr;
reg [RAM_WIDTH-1:0] queue_ram_wr_data;
reg queue_ram_wr_en;
reg [RAM_WIDTH-1:0] queue_ram_rd_data_reg = 0;
reg [RAM_WIDTH-1:0] queue_ram_rd_data_pipe_reg[PIPELINE-1:1];

reg [RAM_WIDTH-1:0] queue_ram_rd_data_ovrd_pipe_reg[PIPELINE-1:0], queue_ram_rd_data_ovrd_pipe_next[PIPELINE-1:0];
reg queue_ram_rd_data_ovrd_en_pipe_reg[PIPELINE-1:0], queue_ram_rd_data_ovrd_en_pipe_next[PIPELINE-1:0];

wire [RAM_WIDTH-1:0] queue_ram_rd_data = queue_ram_rd_data_ovrd_en_pipe_reg[PIPELINE-1] ? queue_ram_rd_data_ovrd_pipe_reg[PIPELINE-1] : queue_ram_rd_data_pipe_reg[PIPELINE-1];

// Scheduler RAM entry:
// bit            len  field
// 0              1    enable
// 1              1    pause
// 6              1    active
// 7              1    scheduled
// 15:8           8    generation ID

wire queue_ram_rd_data_enabled = queue_ram_rd_data[0];
wire queue_ram_rd_data_paused = queue_ram_rd_data[1];
wire queue_ram_rd_data_active = queue_ram_rd_data[6];
wire queue_ram_rd_data_scheduled = queue_ram_rd_data[7];
wire [GEN_ID_W-1:0] queue_ram_rd_data_gen_id = queue_ram_rd_data[15:8];

reg [FINISH_FIFO_AW+1-1:0] finish_fifo_wr_ptr_reg = 0, finish_fifo_wr_ptr_next;
reg [FINISH_FIFO_AW+1-1:0] finish_fifo_rd_ptr_reg = 0, finish_fifo_rd_ptr_next;
(* ram_style = "distributed", ramstyle = "no_rw_check, mlab" *)
reg [QUEUE_INDEX_WIDTH-1:0] finish_fifo_queue[(2**FINISH_FIFO_AW)-1:0];
(* ram_style = "distributed", ramstyle = "no_rw_check, mlab" *)
reg [REQ_TAG_WIDTH-1:0] finish_fifo_tag[(2**FINISH_FIFO_AW)-1:0];
reg finish_fifo_we;
reg [QUEUE_INDEX_WIDTH-1:0] finish_fifo_wr_queue;
reg [REQ_TAG_WIDTH-1:0] finish_fifo_wr_tag;

reg [QUEUE_INDEX_WIDTH-1:0] finish_queue_reg = {QUEUE_INDEX_WIDTH{1'b0}}, finish_queue_next;
reg [REQ_TAG_WIDTH-1:0] finish_tag_reg = {REQ_TAG_WIDTH{1'b0}}, finish_tag_next;
reg finish_valid_reg = 1'b0, finish_valid_next;

reg init_reg = 1'b0, init_next;
reg [QUEUE_INDEX_WIDTH-1:0] init_index_reg = 0, init_index_next;

reg [QUEUE_INDEX_WIDTH+1-1:0] active_queue_count_reg = 0, active_queue_count_next;

reg [CL_OP_COUNT+1-1:0] active_op_count_reg = 0;
reg inc_active_op;
reg dec_active_op_1;
reg dec_active_op_2;

// internal datapath
reg  [QUEUE_INDEX_WIDTH-1:0] m_axis_tx_req_queue_int;
reg  [REQ_DEST_WIDTH-1:0]    m_axis_tx_req_dest_int;
reg  [REQ_TAG_WIDTH-1:0]     m_axis_tx_req_tag_int;
reg                          m_axis_tx_req_valid_int;
wire                         m_axis_tx_req_ready_int;

assign s_axis_sched_ctrl_ready = s_axis_sched_ctrl_ready_reg;

assign s_axil_awready = s_axil_awready_reg;
assign s_axil_wready = s_axil_wready_reg;
assign s_axil_bresp = 2'b00;
assign s_axil_bvalid = s_axil_bvalid_reg;
assign s_axil_arready = s_axil_arready_reg;
assign s_axil_rdata = s_axil_rdata_reg;
assign s_axil_rresp = 2'b00;
assign s_axil_rvalid = s_axil_rvalid_reg;

assign active = active_queue_count_reg != 0;

wire [QUEUE_INDEX_WIDTH-1:0] s_axil_awaddr_queue = s_axil_awaddr >> 2;
wire [QUEUE_INDEX_WIDTH-1:0] s_axil_araddr_queue = s_axil_araddr >> 2;

wire [QUEUE_INDEX_WIDTH-1:0] axis_doorbell_fifo_queue;
wire axis_doorbell_fifo_valid;
reg axis_doorbell_fifo_ready;

axis_fifo #(
    .DEPTH(256),
    .DATA_WIDTH(QUEUE_INDEX_WIDTH),
    .KEEP_ENABLE(0),
    .LAST_ENABLE(0),
    .ID_ENABLE(0),
    .DEST_ENABLE(0),
    .USER_ENABLE(0),
    .RAM_PIPELINE(1),
    .OUTPUT_FIFO_ENABLE(0),
    .FRAME_FIFO(0),
    .PAUSE_ENABLE(0)
)
doorbell_fifo (
    .clk(clk),
    .rst(rst),

    // AXI input
    .s_axis_tdata(s_axis_doorbell_queue),
    .s_axis_tkeep(0),
    .s_axis_tvalid(s_axis_doorbell_valid),
    .s_axis_tready(),
    .s_axis_tlast(0),
    .s_axis_tid(0),
    .s_axis_tdest(0),
    .s_axis_tuser(0),

    // AXI output
    .m_axis_tdata(axis_doorbell_fifo_queue),
    .m_axis_tkeep(),
    .m_axis_tvalid(axis_doorbell_fifo_valid),
    .m_axis_tready(axis_doorbell_fifo_ready),
    .m_axis_tlast(),
    .m_axis_tid(),
    .m_axis_tdest(),
    .m_axis_tuser(),

    // Pause
    .pause_req(),
    .pause_ack(),

    // Status
    .status_depth(),
    .status_depth_commit(),
    .status_overflow(),
    .status_bad_frame(),
    .status_good_frame()
);

reg [QUEUE_INDEX_WIDTH-1:0] axis_scheduler_fifo_in_queue;
reg axis_scheduler_fifo_in_valid;
wire axis_scheduler_fifo_in_ready;

wire [QUEUE_INDEX_WIDTH-1:0] axis_scheduler_fifo_out_queue;
wire axis_scheduler_fifo_out_valid;
reg axis_scheduler_fifo_out_ready;

axis_fifo #(
    .DEPTH(2**QUEUE_INDEX_WIDTH),
    .DATA_WIDTH(QUEUE_INDEX_WIDTH),
    .KEEP_ENABLE(0),
    .LAST_ENABLE(0),
    .ID_ENABLE(0),
    .DEST_ENABLE(0),
    .USER_ENABLE(0),
    .RAM_PIPELINE(1),
    .OUTPUT_FIFO_ENABLE(0),
    .FRAME_FIFO(0),
    .PAUSE_ENABLE(0)
)
rr_fifo (
    .clk(clk),
    .rst(rst),

    // AXI input
    .s_axis_tdata(axis_scheduler_fifo_in_queue),
    .s_axis_tkeep(0),
    .s_axis_tvalid(axis_scheduler_fifo_in_valid),
    .s_axis_tready(axis_scheduler_fifo_in_ready),
    .s_axis_tlast(0),
    .s_axis_tid(0),
    .s_axis_tdest(0),
    .s_axis_tuser(0),

    // AXI output
    .m_axis_tdata(axis_scheduler_fifo_out_queue),
    .m_axis_tkeep(),
    .m_axis_tvalid(axis_scheduler_fifo_out_valid),
    .m_axis_tready(axis_scheduler_fifo_out_ready),
    .m_axis_tlast(),
    .m_axis_tid(),
    .m_axis_tdest(),
    .m_axis_tuser(),

    // Pause
    .pause_req(),
    .pause_ack(),

    // Status
    .status_depth(),
    .status_depth_commit(),
    .status_overflow(),
    .status_bad_frame(),
    .status_good_frame()
);

integer i, j;

initial begin
    // break up loop to work around iteration termination
    for (i = 0; i < 2**QUEUE_INDEX_WIDTH; i = i + 2**(QUEUE_INDEX_WIDTH/2)) begin
        for (j = i; j < i + 2**(QUEUE_INDEX_WIDTH/2); j = j + 1) begin
            queue_ram[j] = 0;
        end
    end

    for (i = 0; i < PIPELINE; i = i + 1) begin
        queue_ram_addr_pipeline_reg[i] = 0;
        write_data_pipeline_reg[i] = 0;
        write_strobe_pipeline_reg[i] = 0;
        req_tag_pipeline_reg[i] = 0;

        queue_ram_rd_data_ovrd_pipe_reg[i] = 0;
        queue_ram_rd_data_ovrd_en_pipe_reg[i] = 0;
    end
end

// flow control
reg ch_fetch_fc_cons_en = 1'b0;
reg ch_fetch_fc_rel_sched_fail_en = 1'b0;
reg ch_fetch_fc_rel_dequeue_fail_en = 1'b0;
reg ch_fetch_fc_rel_fetch_fail_en = 1'b0;
reg [DATA_FC_W-1:0] ch_tx_data_fc_cons = 0;
reg ch_tx_fc_cons_en = 1'b0;
reg [DATA_FC_W-1:0] ch_tx_data_fc_rel = 0;
reg ch_tx_fc_rel_en = 1'b0;

reg ch_enable_reg = 1'b0;
reg ch_active_reg = 1'b0;
reg ch_fetch_active_reg = 1'b0;
reg [TX_FC_W-1:0] ch_fetch_fc_cnt_reg = 0;
reg [TX_FC_W-1:0] ch_fetch_fc_lim_reg = 0;
reg ch_fetch_fc_av_reg = 0;
reg [PKT_FC_W-1:0] ch_fetch_pkt_fc_cons_reg = 0;
reg [PKT_FC_W-1:0] ch_fetch_pkt_fc_rel_sched_fail_reg = 0;
reg [PKT_FC_W-1:0] ch_fetch_pkt_fc_rel_dequeue_fail_reg = 0;
reg [PKT_FC_W-1:0] ch_fetch_pkt_fc_rel_fetch_fail_reg = 0;
reg [PKT_FC_W-1:0] ch_pkt_fc_lim_reg = {PKT_FC_W{1'b1}};
reg [BUDGET_FC_W-1:0] ch_data_fc_budget_reg = (MAX_TX_SIZE + 2**CL_FC_SCALE - 1) >> CL_FC_SCALE;
reg [PKT_FC_W-1:0] ch_tx_pkt_fc_cons_reg = 0;
reg [DATA_FC_W-1:0] ch_tx_data_fc_cons_reg = 0;
reg [PKT_FC_W-1:0] ch_tx_pkt_fc_rel_reg = 0;
reg [DATA_FC_W-1:0] ch_tx_data_fc_rel_reg = 0;
reg [DATA_FC_W-1:0] ch_data_fc_lim_reg = (MAX_TX_SIZE + 2**CL_FC_SCALE - 1) >> CL_FC_SCALE;

reg [TX_FC_W-1:0] ch_fetch_fc_cnt_d1_reg = 0;
reg [TX_FC_W-1:0] ch_fetch_fc_cnt_d2_reg = 0;
reg [PKT_FC_W-1:0] ch_fetch_pkt_fc_cnt_reg = 0;
reg [PKT_FC_W-1:0] ch_tx_pkt_fc_cnt_reg = 0;
reg [PKT_FC_W-1:0] ch_pkt_fc_cnt_reg = 0;
reg [DATA_FC_W-1:0] ch_tx_data_fc_cnt_reg = 0;
reg [DATA_FC_W-1:0] ch_data_fc_cnt_reg = 0;

always @* begin
    ch_fetch_fc_rel_dequeue_fail_en = 1'b0;
    ch_fetch_fc_rel_fetch_fail_en = 1'b0;
    ch_tx_data_fc_cons = 0;
    ch_tx_fc_cons_en = 1'b0;
    ch_tx_data_fc_rel = 0;
    ch_tx_fc_rel_en = 1'b0;

    if (s_axis_tx_status_dequeue_valid) begin
        if (s_axis_tx_status_dequeue_empty || s_axis_tx_status_dequeue_error) begin
            ch_fetch_fc_rel_dequeue_fail_en = 1'b1;
        end
    end

    ch_tx_data_fc_cons = (s_axis_tx_status_start_len + 2**CL_FC_SCALE-1) >> CL_FC_SCALE;
    if (s_axis_tx_status_start_valid) begin
        if (s_axis_tx_status_start_error) begin
            ch_fetch_fc_rel_fetch_fail_en = 1'b1;
        end else begin
            ch_fetch_fc_rel_fetch_fail_en = 1'b1;
            ch_tx_fc_cons_en = 1'b1;
        end
    end

    ch_tx_data_fc_rel = (s_axis_tx_status_finish_len + 2**CL_FC_SCALE-1) >> CL_FC_SCALE;
    if (s_axis_tx_status_finish_valid) begin
        ch_tx_fc_rel_en = 1'b1;
    end
end

always @(posedge clk) begin
    // handle events
    if (ch_fetch_fc_cons_en) begin
        ch_fetch_pkt_fc_cons_reg <= ch_fetch_pkt_fc_cons_reg + 1;
        ch_fetch_fc_cnt_reg <= ch_fetch_fc_cnt_reg + 1;
        ch_fetch_fc_av_reg <= ((ch_fetch_fc_lim_reg - ch_fetch_fc_cnt_reg - 1) & {TX_FC_W{1'b1}}) <= 2**(TX_FC_W-1) && ch_enable_reg;
    end else begin
        ch_fetch_fc_av_reg <= ((ch_fetch_fc_lim_reg - ch_fetch_fc_cnt_reg) & {TX_FC_W{1'b1}}) <= 2**(TX_FC_W-1) && ch_enable_reg;
    end

    if (ch_fetch_fc_rel_sched_fail_en) begin
        ch_fetch_pkt_fc_rel_sched_fail_reg <= ch_fetch_pkt_fc_rel_sched_fail_reg + 1;
    end

    if (ch_fetch_fc_rel_dequeue_fail_en) begin
        ch_fetch_pkt_fc_rel_dequeue_fail_reg <= ch_fetch_pkt_fc_rel_dequeue_fail_reg + 1;
    end

    if (ch_fetch_fc_rel_fetch_fail_en) begin
        ch_fetch_pkt_fc_rel_fetch_fail_reg <= ch_fetch_pkt_fc_rel_fetch_fail_reg + 1;
    end

    if (ch_tx_fc_cons_en) begin
        ch_tx_pkt_fc_cons_reg <= ch_tx_pkt_fc_cons_reg + 1;
        ch_tx_data_fc_cons_reg <= ch_tx_data_fc_cons_reg + ch_tx_data_fc_cons;
    end

    if (ch_tx_fc_rel_en) begin
        ch_tx_pkt_fc_rel_reg <= ch_tx_pkt_fc_rel_reg + 1;
        ch_tx_data_fc_rel_reg <= ch_tx_data_fc_rel_reg + ch_tx_data_fc_rel;
    end

    // intermediate counts
    ch_fetch_pkt_fc_cnt_reg <= ch_fetch_pkt_fc_cons_reg - ch_fetch_pkt_fc_rel_sched_fail_reg - ch_fetch_pkt_fc_rel_dequeue_fail_reg - ch_fetch_pkt_fc_rel_fetch_fail_reg;
    ch_tx_pkt_fc_cnt_reg <= ch_tx_pkt_fc_cons_reg - ch_tx_pkt_fc_rel_reg;
    ch_tx_data_fc_cnt_reg <= ch_tx_data_fc_cons_reg - ch_tx_data_fc_rel_reg;
    ch_fetch_fc_cnt_d1_reg <= ch_fetch_fc_cnt_reg;

    // final counts
    ch_pkt_fc_cnt_reg <= ch_fetch_pkt_fc_cnt_reg + ch_tx_pkt_fc_cnt_reg;
    ch_data_fc_cnt_reg <= ch_fetch_pkt_fc_cnt_reg*ch_data_fc_budget_reg + ch_tx_data_fc_cnt_reg;
    ch_fetch_fc_cnt_d2_reg <= ch_fetch_fc_cnt_d1_reg;

    ch_fetch_active_reg <= ch_fetch_pkt_fc_cnt_reg != 0;
    ch_active_reg <= ch_fetch_pkt_fc_cnt_reg != 0 || ch_tx_pkt_fc_cnt_reg != 0;

    // generate credits
    if ($signed({1'b0, ch_data_fc_lim_reg}) - $signed({1'b0, ch_data_fc_cnt_reg}) >= {ch_data_fc_budget_reg, 3'd0} && $signed({1'b0, ch_pkt_fc_lim_reg}) - $signed({1'b0, ch_pkt_fc_cnt_reg}) >= 8 && TX_FC_W > 3) begin
        ch_fetch_fc_lim_reg <= ch_fetch_fc_cnt_d2_reg + 8;
    end else if ($signed({1'b0, ch_data_fc_lim_reg}) - $signed({1'b0, ch_data_fc_cnt_reg}) >= {ch_data_fc_budget_reg, 2'd0} && $signed({1'b0, ch_pkt_fc_lim_reg}) - $signed({1'b0, ch_pkt_fc_cnt_reg}) >= 4 && TX_FC_W > 2) begin
        ch_fetch_fc_lim_reg <= ch_fetch_fc_cnt_d2_reg + 4;
    end else if ($signed({1'b0, ch_data_fc_lim_reg}) - $signed({1'b0, ch_data_fc_cnt_reg}) >= {ch_data_fc_budget_reg, 1'd0} && $signed({1'b0, ch_pkt_fc_lim_reg}) - $signed({1'b0, ch_pkt_fc_cnt_reg}) >= 2) begin
        ch_fetch_fc_lim_reg <= ch_fetch_fc_cnt_d2_reg + 2;
    end else if ($signed({1'b0, ch_data_fc_lim_reg}) - $signed({1'b0, ch_data_fc_cnt_reg}) >= ch_data_fc_budget_reg && $signed({1'b0, ch_pkt_fc_lim_reg}) - $signed({1'b0, ch_pkt_fc_cnt_reg}) >= 1) begin
        ch_fetch_fc_lim_reg <= ch_fetch_fc_cnt_d2_reg + 1;
    end else begin
        ch_fetch_fc_lim_reg <= ch_fetch_fc_cnt_d2_reg;
        ch_fetch_fc_av_reg <= 1'b0;
    end

    if (rst) begin
        ch_fetch_fc_cnt_reg <= 0;
        ch_fetch_fc_cnt_d1_reg <= 0;
        ch_fetch_fc_cnt_d2_reg <= 0;
        ch_fetch_fc_lim_reg <= 0;
        ch_fetch_fc_av_reg <= 0;
        ch_fetch_pkt_fc_cons_reg <= 0;
        ch_fetch_pkt_fc_rel_sched_fail_reg <= 0;
        ch_fetch_pkt_fc_rel_dequeue_fail_reg <= 0;
        ch_fetch_pkt_fc_rel_fetch_fail_reg <= 0;
        ch_fetch_pkt_fc_cnt_reg <= 0;
        ch_tx_pkt_fc_cnt_reg <= 0;
        ch_pkt_fc_cnt_reg <= 0;
        ch_tx_pkt_fc_cons_reg <= 0;
        ch_tx_data_fc_cons_reg <= 0;
        ch_tx_pkt_fc_rel_reg <= 0;
        ch_tx_data_fc_rel_reg <= 0;
        ch_tx_data_fc_cnt_reg <= 0;
        ch_data_fc_cnt_reg <= 0;
    end
end

// control registers
reg ctrl_reg_wr_ack_reg = 1'b0;
reg [REG_DATA_WIDTH-1:0] ctrl_reg_rd_data_reg = {REG_DATA_WIDTH{1'b0}};
reg ctrl_reg_rd_ack_reg = 1'b0;

reg enable_reg = 1'b0;

assign ctrl_reg_wr_wait = 1'b0;
assign ctrl_reg_wr_ack = ctrl_reg_wr_ack_reg;
assign ctrl_reg_rd_data = ctrl_reg_rd_data_reg;
assign ctrl_reg_rd_wait = 1'b0;
assign ctrl_reg_rd_ack = ctrl_reg_rd_ack_reg;

integer k;

always @(posedge clk) begin
    ctrl_reg_wr_ack_reg <= 1'b0;
    ctrl_reg_rd_data_reg <= {REG_DATA_WIDTH{1'b0}};
    ctrl_reg_rd_ack_reg <= 1'b0;

    if (ctrl_reg_wr_en && !ctrl_reg_wr_ack_reg) begin
        // write operation
        ctrl_reg_wr_ack_reg <= 1'b1;
        case ({ctrl_reg_wr_addr >> 2, 2'b00})
            // Round-robin scheduler
            RBB+8'h18: begin
                // Sched: control
                if (ctrl_reg_wr_strb[0]) begin
                    enable_reg <= ctrl_reg_wr_data[0];
                end
            end
            RBB+8'h20: begin
                if (ctrl_reg_wr_strb[0]) begin
                    ch_enable_reg <= ctrl_reg_wr_data[0];
                end
            end
            RBB+8'h24: begin
                if (ctrl_reg_wr_strb[1:0]) begin
                    tx_req_dest_reg <= ctrl_reg_wr_data[15:0];
                end
                if (ctrl_reg_wr_strb[3:2]) begin
                    // TODO
                    // ch_pkt_fc_budget_reg <= ctrl_reg_wr_data[31:16];
                end
            end
            RBB+8'h28: begin
                if (ctrl_reg_wr_strb[1:0]) begin
                    ch_data_fc_budget_reg <= ctrl_reg_wr_data[15:0];
                end
                if (ctrl_reg_wr_strb[3:2]) begin
                    ch_pkt_fc_lim_reg <= ctrl_reg_wr_data[31:16];
                end
            end
            RBB+8'h2C: begin
                ch_data_fc_lim_reg <= ctrl_reg_wr_data;
            end
            default: ctrl_reg_wr_ack_reg <= 1'b0;
        endcase
    end

    if (ctrl_reg_rd_en && !ctrl_reg_rd_ack_reg) begin
        // read operation
        ctrl_reg_rd_ack_reg <= 1'b1;
        case ({ctrl_reg_rd_addr >> 2, 2'b00})
            // Round-robin scheduler
            RBB+8'h00: ctrl_reg_rd_data_reg <= RB_BLOCK_TYPE;         // Sched: Type
            RBB+8'h04: ctrl_reg_rd_data_reg <= 32'h00000200;          // Sched: Version
            RBB+8'h08: ctrl_reg_rd_data_reg <= RB_NEXT_PTR;           // Sched: Next header
            RBB+8'h0C: ctrl_reg_rd_data_reg <= AXIL_BASE_ADDR;        // Sched: Offset
            RBB+8'h10: ctrl_reg_rd_data_reg <= 2**QUEUE_INDEX_WIDTH;  // Sched: Channel count
            RBB+8'h14: ctrl_reg_rd_data_reg <= 4;                     // Sched: Channel stride
            RBB+8'h18: begin
                // Sched: control
                ctrl_reg_rd_data_reg[0]  <= enable_reg;
                ctrl_reg_rd_data_reg[16] <= active_queue_count_reg != 0;
            end
            RBB+8'h1C: begin
                ctrl_reg_rd_data_reg[7:0]   <= 1;                     // Sched: TC count
                ctrl_reg_rd_data_reg[15:8]  <= 1;                     // Sched: Port count
                ctrl_reg_rd_data_reg[23:16] <= CL_FC_SCALE;           // Sched: FC scale
            end
            RBB+8'h20: begin
                ctrl_reg_rd_data_reg[0]  <= ch_enable_reg;
                ctrl_reg_rd_data_reg[16] <= ch_active_reg;
                ctrl_reg_rd_data_reg[17] <= ch_fetch_active_reg;
                ctrl_reg_rd_data_reg[18] <= ch_fetch_fc_av_reg;
                ctrl_reg_rd_data_reg[19] <= axis_scheduler_fifo_out_valid;
            end
            RBB+8'h24: begin
                ctrl_reg_rd_data_reg[15:0]  <= tx_req_dest_reg;
                // TODO
                ctrl_reg_rd_data_reg[31:16] <= 1; // ch_pkt_fc_budget_reg;
            end
            RBB+8'h28: begin
                ctrl_reg_rd_data_reg[15:0]  <= ch_data_fc_budget_reg;
                ctrl_reg_rd_data_reg[31:16] <= ch_pkt_fc_lim_reg;
            end
            RBB+8'h2C: begin
                ctrl_reg_rd_data_reg <= ch_data_fc_lim_reg;
            end
            default: ctrl_reg_rd_ack_reg <= 1'b0;
        endcase
    end

    if (rst) begin
        ctrl_reg_wr_ack_reg <= 1'b0;
        ctrl_reg_rd_ack_reg <= 1'b0;

        enable_reg <= 1'b0;
        tx_req_dest_reg <= REQ_DEST_DEFAULT;

        ch_enable_reg <= 0;
        ch_pkt_fc_lim_reg <= {PKT_FC_W{1'b1}};
        ch_data_fc_budget_reg <= (MAX_TX_SIZE + 2**CL_FC_SCALE - 1) >> CL_FC_SCALE;
        ch_data_fc_lim_reg <= (MAX_TX_SIZE + 2**CL_FC_SCALE - 1) >> CL_FC_SCALE;
    end
end

reg enabled;
reg paused;

always @* begin
    op_axil_write_pipe_next = {op_axil_write_pipe_reg, 1'b0};
    op_axil_read_pipe_next = {op_axil_read_pipe_reg, 1'b0};
    op_doorbell_pipe_next = {op_doorbell_pipe_reg, 1'b0};
    op_req_pipe_next = {op_req_pipe_reg, 1'b0};
    op_complete_pipe_next = {op_complete_pipe_reg, 1'b0};
    op_ctrl_pipe_next = {op_ctrl_pipe_reg, 1'b0};
    op_internal_pipe_next = {op_internal_pipe_reg, 1'b0};

    queue_ram_addr_pipeline_next[0] = 0;
    write_data_pipeline_next[0] = 0;
    write_strobe_pipeline_next[0] = 0;
    req_tag_pipeline_next[0] = 0;

    queue_ram_rd_data_ovrd_pipe_next[0] = 0;
    queue_ram_rd_data_ovrd_en_pipe_next[0] = 0;

    for (j = 1; j < PIPELINE; j = j + 1) begin
        queue_ram_addr_pipeline_next[j] = queue_ram_addr_pipeline_reg[j-1];
        write_data_pipeline_next[j] = write_data_pipeline_reg[j-1];
        write_strobe_pipeline_next[j] = write_strobe_pipeline_reg[j-1];
        req_tag_pipeline_next[j] = req_tag_pipeline_reg[j-1];

        queue_ram_rd_data_ovrd_pipe_next[j] = queue_ram_rd_data_ovrd_pipe_reg[j-1];
        queue_ram_rd_data_ovrd_en_pipe_next[j] = queue_ram_rd_data_ovrd_en_pipe_reg[j-1];
    end

    s_axis_sched_ctrl_ready_next = 1'b0;

    s_axil_awready_next = 1'b0;
    s_axil_wready_next = 1'b0;
    s_axil_bvalid_next = s_axil_bvalid_reg && !s_axil_bready;

    s_axil_arready_next = 1'b0;
    s_axil_rdata_next = s_axil_rdata_reg;
    s_axil_rvalid_next = s_axil_rvalid_reg && !s_axil_rready;

    queue_ram_rd_addr = 0;
    queue_ram_wr_addr = queue_ram_addr_pipeline_reg[PIPELINE-1];
    queue_ram_wr_data = queue_ram_rd_data;
    queue_ram_wr_en = 0;

    finish_fifo_rd_ptr_next = finish_fifo_rd_ptr_reg;
    finish_fifo_wr_ptr_next = finish_fifo_wr_ptr_reg;
    finish_fifo_we = 1'b0;
    finish_fifo_wr_queue = s_axis_tx_status_dequeue_queue;
    finish_fifo_wr_tag = s_axis_tx_status_dequeue_tag;

    finish_queue_next = finish_queue_reg;
    finish_tag_next = finish_tag_reg;
    finish_valid_next = finish_valid_reg;

    init_next = init_reg;
    init_index_next = init_index_reg;

    active_queue_count_next = active_queue_count_reg;

    inc_active_op = 1'b0;
    dec_active_op_1 = 1'b0;
    dec_active_op_2 = 1'b0;

    m_axis_tx_req_queue_int = queue_ram_addr_pipeline_reg[PIPELINE-1];
    m_axis_tx_req_dest_int = tx_req_dest_reg;
    m_axis_tx_req_tag_int = queue_ram_rd_data_gen_id;
    m_axis_tx_req_valid_int = 1'b0;

    axis_doorbell_fifo_ready = 1'b0;

    axis_scheduler_fifo_in_queue = queue_ram_addr_pipeline_reg[PIPELINE-1];
    axis_scheduler_fifo_in_valid = 1'b0;

    axis_scheduler_fifo_out_ready = 1'b0;

    ch_fetch_fc_cons_en = 1'b0;
    ch_fetch_fc_rel_sched_fail_en = 1'b0;

    // pipeline stage 0 - receive request
    if (!init_reg) begin
        // init queue states
        op_internal_pipe_next[0] = 1'b1;

        init_index_next = init_index_reg + 1;

        queue_ram_rd_addr = init_index_reg;
        queue_ram_addr_pipeline_next[0] = init_index_reg;

        if (init_index_reg == {QUEUE_INDEX_WIDTH{1'b1}}) begin
            init_next = 1'b1;
        end
    end else if (s_axil_awvalid && s_axil_wvalid && (!s_axil_bvalid || s_axil_bready) && !op_axil_write_pipe_reg) begin
        // AXIL write
        op_axil_write_pipe_next[0] = 1'b1;

        s_axil_awready_next = 1'b1;
        s_axil_wready_next = 1'b1;

        write_data_pipeline_next[0] = s_axil_wdata;
        write_strobe_pipeline_next[0] = s_axil_wstrb;

        queue_ram_rd_addr = s_axil_awaddr_queue;
        queue_ram_addr_pipeline_next[0] = s_axil_awaddr_queue;
    end else if (s_axil_arvalid && (!s_axil_rvalid || s_axil_rready) && !op_axil_read_pipe_reg) begin
        // AXIL read
        op_axil_read_pipe_next[0] = 1'b1;

        s_axil_arready_next = 1'b1;

        queue_ram_rd_addr = s_axil_araddr_queue;
        queue_ram_addr_pipeline_next[0] = s_axil_araddr_queue;
    end else if (axis_doorbell_fifo_valid) begin
        // handle doorbell
        op_doorbell_pipe_next[0] = 1'b1;

        axis_doorbell_fifo_ready = 1'b1;

        queue_ram_rd_addr = axis_doorbell_fifo_queue;
        queue_ram_addr_pipeline_next[0] = axis_doorbell_fifo_queue;
    end else if (finish_valid_reg) begin
        // transmit complete
        op_complete_pipe_next[0] = 1'b1;

        req_tag_pipeline_next[0] = finish_tag_reg;

        finish_valid_next = 1'b0;

        queue_ram_rd_addr = finish_queue_reg;
        queue_ram_addr_pipeline_next[0] = finish_queue_reg;
    end else if (SCHED_CTRL_ENABLE && s_axis_sched_ctrl_valid && !op_ctrl_pipe_reg[0]) begin
        // Scheduler control
        op_ctrl_pipe_next[0] = 1'b1;

        s_axis_sched_ctrl_ready_next = 1'b1;

        write_data_pipeline_next[0] = s_axis_sched_ctrl_enable;

        queue_ram_rd_addr = s_axis_sched_ctrl_queue;
        queue_ram_addr_pipeline_next[0] = s_axis_sched_ctrl_queue;
    end else if (enable && enable_reg && !active_op_count_reg[CL_OP_COUNT] && axis_scheduler_fifo_out_valid && ch_fetch_fc_av_reg && m_axis_tx_req_ready_int) begin
        // transmit request
        op_req_pipe_next[0] = 1'b1;

        axis_scheduler_fifo_out_ready = 1'b1;

        ch_fetch_fc_cons_en = 1'b1;
        inc_active_op = 1'b1;

        queue_ram_rd_addr = axis_scheduler_fifo_out_queue;
        queue_ram_addr_pipeline_next[0] = axis_scheduler_fifo_out_queue;
    end

    // read complete, perform operation
    if (op_internal_pipe_reg[PIPELINE-1]) begin
        // internal operation

        // init queue state
        queue_ram_wr_addr = queue_ram_addr_pipeline_reg[PIPELINE-1];
        queue_ram_wr_data = 0;
        queue_ram_wr_data[0] = 1'b0; // queue enabled
        queue_ram_wr_data[1] = 1'b0; // queue paused
        queue_ram_wr_data[6] = 1'b0; // queue active
        queue_ram_wr_data[7] = 1'b0; // queue scheduled
        queue_ram_wr_en = 1'b1;
    end else if (op_doorbell_pipe_reg[PIPELINE-1]) begin
        // handle doorbell

        // mark queue active
        queue_ram_wr_addr = queue_ram_addr_pipeline_reg[PIPELINE-1];
        queue_ram_wr_data[6] = 1'b1; // queue active
        queue_ram_wr_data[15:8] = queue_ram_rd_data_gen_id+1; // generation ID
        queue_ram_wr_en = 1'b1;

        // schedule queue if necessary
        if (queue_ram_rd_data_enabled && !queue_ram_rd_data_paused && !queue_ram_rd_data_scheduled) begin
            queue_ram_wr_data[7] = 1'b1; // queue scheduled

            axis_scheduler_fifo_in_queue = queue_ram_addr_pipeline_reg[PIPELINE-1];
            axis_scheduler_fifo_in_valid = 1'b1;

            active_queue_count_next = active_queue_count_reg + 1;
        end
    end else if (op_req_pipe_reg[PIPELINE-1]) begin
        // transmit request
        m_axis_tx_req_queue_int = queue_ram_addr_pipeline_reg[PIPELINE-1];
        m_axis_tx_req_dest_int = tx_req_dest_reg;
        m_axis_tx_req_tag_int = queue_ram_rd_data_gen_id;

        axis_scheduler_fifo_in_queue = queue_ram_addr_pipeline_reg[PIPELINE-1];

        // update state
        queue_ram_wr_addr = queue_ram_addr_pipeline_reg[PIPELINE-1];
        queue_ram_wr_en = 1'b1;

        if (queue_ram_rd_data_enabled && !queue_ram_rd_data_paused && queue_ram_rd_data_active && queue_ram_rd_data_scheduled) begin
            // queue enabled, active, and scheduled

            // issue transmit request
            m_axis_tx_req_valid_int = 1'b1;

            // reschedule
            axis_scheduler_fifo_in_valid = 1'b1;

            // update state
            queue_ram_wr_data[7] = 1'b1; // queue scheduled
        end else begin
            // queue not enabled, not active, or not scheduled
            // deschedule queue

            // update state
            queue_ram_wr_data[7] = 1'b0; // queue scheduled

            ch_fetch_fc_rel_sched_fail_en = 1'b1;
            dec_active_op_1 = 1'b1;

            if (queue_ram_rd_data_scheduled) begin
                active_queue_count_next = active_queue_count_reg - 1;
            end
        end
    end else if (op_complete_pipe_reg[PIPELINE-1]) begin
        // tx complete

        // update state
        queue_ram_wr_addr = queue_ram_addr_pipeline_reg[PIPELINE-1];
        queue_ram_wr_en = 1'b1;

        dec_active_op_1 = 1'b1;

        if (req_tag_pipeline_reg[PIPELINE-1] == queue_ram_rd_data_gen_id) begin
            // operation failed and generation ID matches; set queue inactive
            queue_ram_wr_data[6] = 1'b0; // queue active
        end
    end else if (SCHED_CTRL_ENABLE && op_ctrl_pipe_reg[PIPELINE-1]) begin
        // Scheduler control
        queue_ram_wr_addr = queue_ram_addr_pipeline_reg[PIPELINE-1];
        queue_ram_wr_en = 1'b1;

        queue_ram_wr_data[1] = !write_data_pipeline_reg[PIPELINE-1][0]; // queue pause

        // schedule if necessary
        if (queue_ram_rd_data_enabled && queue_ram_rd_data_active && !(!write_data_pipeline_reg[PIPELINE-1][0]) && !queue_ram_rd_data_scheduled) begin
            queue_ram_wr_data[7] = 1'b1; // queue scheduled

            axis_scheduler_fifo_in_queue = queue_ram_addr_pipeline_reg[PIPELINE-1];
            axis_scheduler_fifo_in_valid = 1'b1;

            active_queue_count_next = active_queue_count_reg + 1;
        end
    end else if (op_axil_write_pipe_reg[PIPELINE-1]) begin
        // AXIL write
        s_axil_bvalid_next = 1'b1;

        queue_ram_wr_addr = queue_ram_addr_pipeline_reg[PIPELINE-1];
        queue_ram_wr_en = 1'b1;

        enabled = queue_ram_rd_data_enabled;
        paused = queue_ram_rd_data_paused;

        casez (write_data_pipeline_reg[PIPELINE-1])
            32'h8001zzzz: begin
                // set port TC
                // TODO
            end
            32'h8002zzzz: begin
                // set port enable
                // TODO
            end
            32'h8003zzzz: begin
                // set port pause
                // TODO
            end
            32'h400001zz: begin
                // set queue enable
                queue_ram_wr_data[0] = write_data_pipeline_reg[PIPELINE-1][0];
                enabled = write_data_pipeline_reg[PIPELINE-1][0];
            end
            32'h400002zz: begin
                // set queue pause
                queue_ram_wr_data[1] = write_data_pipeline_reg[PIPELINE-1][0];
                paused = write_data_pipeline_reg[PIPELINE-1][0];
            end
            default: begin
                // invalid command
                $display("Error: Invalid command 0x%x for queue %d (instance %m)", write_data_pipeline_reg[PIPELINE-1], queue_ram_addr_pipeline_reg[PIPELINE-1]);
            end
        endcase

        // schedule if necessary
        if (enabled && queue_ram_rd_data_active && !paused && !queue_ram_rd_data_scheduled) begin
            queue_ram_wr_data[7] = 1'b1; // queue scheduled

            axis_scheduler_fifo_in_queue = queue_ram_addr_pipeline_reg[PIPELINE-1];
            axis_scheduler_fifo_in_valid = 1'b1;

            active_queue_count_next = active_queue_count_reg + 1;
        end
    end else if (op_axil_read_pipe_reg[PIPELINE-1]) begin
        // AXIL read
        s_axil_rvalid_next = 1'b1;
        s_axil_rdata_next = 0;

        // queue
        s_axil_rdata_next[6] = queue_ram_rd_data_enabled;
        s_axil_rdata_next[7] = queue_ram_rd_data_paused;
        s_axil_rdata_next[14] = queue_ram_rd_data_active;

        // port 0
        s_axil_rdata_next[3] = queue_ram_rd_data_enabled;
        s_axil_rdata_next[4] = queue_ram_rd_data_paused;
        s_axil_rdata_next[5] = queue_ram_rd_data_scheduled;
    end

    // handle read data override
    if (queue_ram_wr_en) begin
        for (k = 0; k < PIPELINE; k = k + 1) begin
            if (queue_ram_wr_addr == queue_ram_addr_pipeline_next[k]) begin
                queue_ram_rd_data_ovrd_pipe_next[k] = queue_ram_wr_data;
                queue_ram_rd_data_ovrd_en_pipe_next[k] = 1'b1;
            end
        end
    end

    // finish transmit operation
    if (s_axis_tx_status_dequeue_valid) begin
        finish_fifo_wr_queue = s_axis_tx_status_dequeue_queue;
        finish_fifo_wr_tag = s_axis_tx_status_dequeue_tag;

        if (s_axis_tx_status_dequeue_error || s_axis_tx_status_dequeue_empty) begin
            // dequeue failed, hand off to pipeline
            finish_fifo_we = 1'b1;
            finish_fifo_wr_ptr_next = finish_fifo_wr_ptr_reg + 1;
        end else begin
            // dequeue succeeded
            dec_active_op_2 = 1'b1;
        end
    end

    if (!finish_valid_reg && finish_fifo_wr_ptr_reg != finish_fifo_rd_ptr_reg) begin
        finish_queue_next = finish_fifo_queue[finish_fifo_rd_ptr_reg[FINISH_FIFO_AW-1:0]];
        finish_tag_next = finish_fifo_tag[finish_fifo_rd_ptr_reg[FINISH_FIFO_AW-1:0]];
        finish_valid_next = 1'b1;
        finish_fifo_rd_ptr_next = finish_fifo_rd_ptr_reg + 1;
    end
end

always @(posedge clk) begin
    op_axil_write_pipe_reg <= op_axil_write_pipe_next;
    op_axil_read_pipe_reg <= op_axil_read_pipe_next;
    op_doorbell_pipe_reg <= op_doorbell_pipe_next;
    op_req_pipe_reg <= op_req_pipe_next;
    op_complete_pipe_reg <= op_complete_pipe_next;
    op_ctrl_pipe_reg <= op_ctrl_pipe_next;
    op_internal_pipe_reg <= op_internal_pipe_next;

    finish_fifo_rd_ptr_reg <= finish_fifo_rd_ptr_next;
    finish_fifo_wr_ptr_reg <= finish_fifo_wr_ptr_next;

    finish_queue_reg <= finish_queue_next;
    finish_tag_reg <= finish_tag_next;
    finish_valid_reg <= finish_valid_next;

    s_axis_sched_ctrl_ready_reg <= s_axis_sched_ctrl_ready_next;

    s_axil_awready_reg <= s_axil_awready_next;
    s_axil_wready_reg <= s_axil_wready_next;
    s_axil_bvalid_reg <= s_axil_bvalid_next;
    s_axil_arready_reg <= s_axil_arready_next;
    s_axil_rdata_reg <= s_axil_rdata_next;
    s_axil_rvalid_reg <= s_axil_rvalid_next;

    init_reg <= init_next;
    init_index_reg <= init_index_next;

    active_queue_count_reg <= active_queue_count_next;
    active_op_count_reg <= active_op_count_reg + inc_active_op - dec_active_op_1 - dec_active_op_2;

    for (i = 0; i < PIPELINE; i = i + 1) begin
        queue_ram_addr_pipeline_reg[i] <= queue_ram_addr_pipeline_next[i];
        write_data_pipeline_reg[i] <= write_data_pipeline_next[i];
        write_strobe_pipeline_reg[i] <= write_strobe_pipeline_next[i];
        req_tag_pipeline_reg[i] <= req_tag_pipeline_next[i];

        queue_ram_rd_data_ovrd_pipe_reg[i] <= queue_ram_rd_data_ovrd_pipe_next[i];
        queue_ram_rd_data_ovrd_en_pipe_reg[i] <= queue_ram_rd_data_ovrd_en_pipe_next[i];
    end

    if (queue_ram_wr_en) begin
        queue_ram[queue_ram_wr_addr] <= queue_ram_wr_data;
    end
    queue_ram_rd_data_reg <= queue_ram[queue_ram_rd_addr];
    queue_ram_rd_data_pipe_reg[1] <= queue_ram_rd_data_reg;
    for (i = 2; i < PIPELINE; i = i + 1) begin
        queue_ram_rd_data_pipe_reg[i] <= queue_ram_rd_data_pipe_reg[i-1];
    end

    if (finish_fifo_we) begin
        finish_fifo_queue[finish_fifo_wr_ptr_reg[FINISH_FIFO_AW-1:0]] <= finish_fifo_wr_queue;
        finish_fifo_tag[finish_fifo_wr_ptr_reg[FINISH_FIFO_AW-1:0]] <= finish_fifo_wr_tag;
    end

    if (rst) begin
        op_axil_write_pipe_reg <= {PIPELINE{1'b0}};
        op_axil_read_pipe_reg <= {PIPELINE{1'b0}};
        op_doorbell_pipe_reg <= {PIPELINE{1'b0}};
        op_req_pipe_reg <= {PIPELINE{1'b0}};
        op_complete_pipe_reg <= {PIPELINE{1'b0}};
        op_ctrl_pipe_reg <= {PIPELINE{1'b0}};
        op_internal_pipe_reg <= {PIPELINE{1'b0}};

        finish_fifo_rd_ptr_reg <= {FINISH_FIFO_AW+1{1'b0}};
        finish_fifo_wr_ptr_reg <= {FINISH_FIFO_AW+1{1'b0}};

        finish_valid_reg <= 1'b0;

        s_axis_sched_ctrl_ready_reg <= 1'b0;

        s_axil_awready_reg <= 1'b0;
        s_axil_wready_reg <= 1'b0;
        s_axil_bvalid_reg <= 1'b0;
        s_axil_arready_reg <= 1'b0;
        s_axil_rvalid_reg <= 1'b0;

        init_reg <= 1'b0;
        init_index_reg <= 0;

        active_queue_count_reg <= 0;
        active_op_count_reg <= 0;
    end
end

// output datapath logic
reg [QUEUE_INDEX_WIDTH-1:0] m_axis_tx_req_queue_reg = 0;
reg [REQ_DEST_WIDTH-1:0]    m_axis_tx_req_dest_reg  = 0;
reg [REQ_TAG_WIDTH-1:0]     m_axis_tx_req_tag_reg   = 0;
reg                         m_axis_tx_req_valid_reg = 1'b0;

reg [OUTPUT_FIFO_AW+1-1:0] out_fifo_wr_ptr_reg = 0;
reg [OUTPUT_FIFO_AW+1-1:0] out_fifo_rd_ptr_reg = 0;
reg out_fifo_half_full_reg = 1'b0;

wire out_fifo_full = out_fifo_wr_ptr_reg == (out_fifo_rd_ptr_reg ^ {1'b1, {OUTPUT_FIFO_AW{1'b0}}});
wire out_fifo_empty = out_fifo_wr_ptr_reg == out_fifo_rd_ptr_reg;

(* ram_style = "distributed", ramstyle = "no_rw_check, mlab" *)
reg [QUEUE_INDEX_WIDTH-1:0] out_fifo_tx_req_queue[2**OUTPUT_FIFO_AW-1:0];
(* ram_style = "distributed", ramstyle = "no_rw_check, mlab" *)
reg [REQ_DEST_WIDTH-1:0] out_fifo_tx_req_dest[2**OUTPUT_FIFO_AW-1:0];
(* ram_style = "distributed", ramstyle = "no_rw_check, mlab" *)
reg [REQ_TAG_WIDTH-1:0] out_fifo_tx_req_tag[2**OUTPUT_FIFO_AW-1:0];

assign m_axis_tx_req_ready_int = !out_fifo_half_full_reg;

assign m_axis_tx_req_queue = m_axis_tx_req_queue_reg;
assign m_axis_tx_req_dest  = m_axis_tx_req_dest_reg;
assign m_axis_tx_req_tag   = m_axis_tx_req_tag_reg;
assign m_axis_tx_req_valid = m_axis_tx_req_valid_reg;

always @(posedge clk) begin
    m_axis_tx_req_valid_reg <= m_axis_tx_req_valid_reg && !m_axis_tx_req_ready;

    out_fifo_half_full_reg <= $unsigned(out_fifo_wr_ptr_reg - out_fifo_rd_ptr_reg) >= 2**(OUTPUT_FIFO_AW-1);

    if (!out_fifo_full && m_axis_tx_req_valid_int) begin
        out_fifo_tx_req_queue[out_fifo_wr_ptr_reg[OUTPUT_FIFO_AW-1:0]] <= m_axis_tx_req_queue_int;
        out_fifo_tx_req_dest[out_fifo_wr_ptr_reg[OUTPUT_FIFO_AW-1:0]] <= m_axis_tx_req_dest_int;
        out_fifo_tx_req_tag[out_fifo_wr_ptr_reg[OUTPUT_FIFO_AW-1:0]] <= m_axis_tx_req_tag_int;
        out_fifo_wr_ptr_reg <= out_fifo_wr_ptr_reg + 1;
    end

    if (!out_fifo_empty && (!m_axis_tx_req_valid_reg || m_axis_tx_req_ready)) begin
        m_axis_tx_req_queue_reg <= out_fifo_tx_req_queue[out_fifo_rd_ptr_reg[OUTPUT_FIFO_AW-1:0]];
        m_axis_tx_req_dest_reg <= out_fifo_tx_req_dest[out_fifo_rd_ptr_reg[OUTPUT_FIFO_AW-1:0]];
        m_axis_tx_req_tag_reg <= out_fifo_tx_req_tag[out_fifo_rd_ptr_reg[OUTPUT_FIFO_AW-1:0]];
        m_axis_tx_req_valid_reg <= 1'b1;
        out_fifo_rd_ptr_reg <= out_fifo_rd_ptr_reg + 1;
    end

    if (rst) begin
        out_fifo_wr_ptr_reg <= 0;
        out_fifo_rd_ptr_reg <= 0;
        m_axis_tx_req_valid_reg <= 1'b0;
    end
end

endmodule

`resetall
