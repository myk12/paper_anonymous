// SPDX-License-Identifier: BSD-2-Clause-Views
/*
 * Copyright (c) 2019-2024 The Regents of the University of California
 */

// Language: Verilog 2001

`resetall
`timescale 1ns / 1ps
`default_nettype none

/*
 * TDMA BER measurement
 */
module mqnic_tdma_ber #
(
    parameter COUNT = 4,
    parameter TDMA_INDEX_W = 6,
    parameter ERR_BITS = 66,
    parameter ERR_CNT_W = $clog2(ERR_BITS),
    parameter RAM_SIZE = 1024,
    parameter PHY_PIPELINE = 2,

    parameter REG_ADDR_WIDTH = 16,
    parameter REG_DATA_WIDTH = 32,
    parameter REG_STRB_WIDTH = (REG_DATA_WIDTH/8),
    parameter RB_BASE_ADDR = 0,
    parameter RB_NEXT_PTR = 0
)
(
    input  wire                        clk,
    input  wire                        rst,

    /*
     * Register interface
     */
    input  wire [REG_ADDR_WIDTH-1:0]   ctrl_reg_wr_addr,
    input  wire [REG_DATA_WIDTH-1:0]   ctrl_reg_wr_data,
    input  wire [REG_STRB_WIDTH-1:0]   ctrl_reg_wr_strb,
    input  wire                        ctrl_reg_wr_en,
    output wire                        ctrl_reg_wr_wait,
    output wire                        ctrl_reg_wr_ack,
    input  wire [REG_ADDR_WIDTH-1:0]   ctrl_reg_rd_addr,
    input  wire                        ctrl_reg_rd_en,
    output wire [REG_DATA_WIDTH-1:0]   ctrl_reg_rd_data,
    output wire                        ctrl_reg_rd_wait,
    output wire                        ctrl_reg_rd_ack,

    /*
     * PTP clock
     */
    input  wire [95:0]                 ptp_ts_tod,
    input  wire                        ptp_ts_tod_step,

    /*
     * PHY connections
     */
    input  wire [COUNT-1:0]            phy_tx_clk,
    input  wire [COUNT-1:0]            phy_rx_clk,
    input  wire [COUNT*ERR_CNT_W-1:0]  phy_rx_error_count,
    output wire [COUNT-1:0]            phy_cfg_tx_prbs31_enable,
    output wire [COUNT-1:0]            phy_cfg_rx_prbs31_enable
);

localparam RAM_AW = $clog2(RAM_SIZE);
localparam CL_RAM_AW = $clog2(RAM_AW);

localparam RBB = RB_BASE_ADDR & {REG_ADDR_WIDTH{1'b1}};

// check configuration
initial begin
    if (REG_DATA_WIDTH != 32) begin
        $error("Error: Register interface width must be 32 (instance %m)");
        $finish;
    end

    if (REG_STRB_WIDTH * 8 != REG_DATA_WIDTH) begin
        $error("Error: Register interface requires byte (8-bit) granularity (instance %m)");
        $finish;
    end

    if (REG_ADDR_WIDTH < 6) begin
        $error("Error: Register address width too narrow (instance %m)");
        $finish;
    end

    if (RB_NEXT_PTR && RB_NEXT_PTR >= RB_BASE_ADDR && RB_NEXT_PTR < RB_BASE_ADDR + 8'h80 + COUNT*16) begin
        $error("Error: RB_NEXT_PTR overlaps block (instance %m)");
        $finish;
    end
end

reg [3:0] cur_ts_reg = 0;
reg [3:0] last_ts_reg = 0;
reg [3:0] ts_inc_reg = 0;

reg [COUNT-1:0] cfg_tx_prbs31_enable_reg = 0;
reg [COUNT-1:0] cfg_rx_prbs31_enable_reg = 0;

reg [RAM_AW-1:0] ram_csr_index_reg = 0;
reg ram_csr_wr_zero_reg = 1'b0;
reg [RAM_AW-1:0] ram_acc_index_reg = 0;

reg acc_en_reg = 1'b0;
reg slice_en_reg = 1'b0;
reg acc_reg = 1'b0;

reg [31:0] cycle_count_reg = 0;
reg [31:0] slice_time_reg = 0;
reg [31:0] slice_offset_reg = 0;

reg slice_running_reg = 1'b0;
reg slice_active_reg = 1'b0;
reg [31:0] slice_count_reg = 0;
reg [CL_RAM_AW-1:0] slice_shift_reg = 0;
reg [RAM_AW-1:0] slice_index_reg = 0;

wire tdma_schedule_start;
wire [TDMA_INDEX_W-1:0] tdma_timeslot_index;
wire tdma_timeslot_start;
wire tdma_timeslot_end;
wire tdma_timeslot_active;

wire [31:0] update_count_val[COUNT-1:0];
wire [31:0] error_count_val[COUNT-1:0];

wire [31:0] update_count_rd_val[COUNT-1:0];
wire [31:0] error_count_rd_val[COUNT-1:0];

genvar n;

generate

for (n = 0; n < COUNT; n = n + 1) begin : ch

    wire ch_phy_tx_clk = phy_tx_clk[n];
    wire ch_phy_rx_clk = phy_rx_clk[n];

    wire [ERR_CNT_W-1:0] ch_phy_rx_error_count = phy_rx_error_count[n*ERR_CNT_W +: ERR_CNT_W];

    // PHY TX BER interface
    reg phy_cfg_tx_prbs31_enable_reg = 1'b0;
    (* shreg_extract = "no" *)
    reg [PHY_PIPELINE-1:0] phy_cfg_tx_prbs31_enable_pipe_reg = 0;

    always @(posedge ch_phy_tx_clk) begin
        phy_cfg_tx_prbs31_enable_reg <= cfg_tx_prbs31_enable_reg[n];
        phy_cfg_tx_prbs31_enable_pipe_reg <= {phy_cfg_tx_prbs31_enable_pipe_reg, phy_cfg_tx_prbs31_enable_reg};
    end

    assign phy_cfg_tx_prbs31_enable[n] = phy_cfg_tx_prbs31_enable_pipe_reg[PHY_PIPELINE-1];

    // PHY RX BER interface
    reg phy_cfg_rx_prbs31_enable_reg = 1'b0;
    (* shreg_extract = "no" *)
    reg [PHY_PIPELINE-1:0] phy_cfg_rx_prbs31_enable_pipe_reg = 0;
    (* shreg_extract = "no" *)
    reg [PHY_PIPELINE*ERR_CNT_W-1:0] phy_rx_error_count_pipe_reg = 0;

    // accumulate errors, dump every 16 cycles
    reg [ERR_CNT_W+4-1:0] phy_rx_error_count_reg = 0;
    reg [ERR_CNT_W+4-1:0] phy_rx_error_count_acc_reg = 0;
    reg [3:0] phy_rx_count_reg = 4'd0;
    reg phy_rx_flag_reg = 1'b0;

    always @(posedge ch_phy_rx_clk) begin
        phy_cfg_rx_prbs31_enable_reg <= cfg_rx_prbs31_enable_reg[n];
        phy_cfg_rx_prbs31_enable_pipe_reg <= {phy_cfg_rx_prbs31_enable_pipe_reg, phy_cfg_rx_prbs31_enable_reg};
        phy_rx_error_count_pipe_reg <= {phy_rx_error_count_pipe_reg, ch_phy_rx_error_count};

        phy_rx_count_reg <= phy_rx_count_reg + 1;

        if (phy_rx_count_reg == 0) begin
            phy_rx_error_count_reg <= phy_rx_error_count_acc_reg;
            phy_rx_error_count_acc_reg <= phy_rx_error_count_pipe_reg[(PHY_PIPELINE-1)*7 +: 7];
            phy_rx_flag_reg <= !phy_rx_flag_reg;
        end else begin
            phy_rx_error_count_acc_reg <= phy_rx_error_count_acc_reg + (phy_rx_error_count_pipe_reg[(PHY_PIPELINE-1)*7 +: 7]);
        end
    end

    assign phy_cfg_rx_prbs31_enable[n] = phy_cfg_rx_prbs31_enable_pipe_reg[PHY_PIPELINE-1];

    // synchronize dumped counts to control clock domain
    (* shreg_extract = "no" *)
    reg rx_flag_sync_reg_1 = 1'b0;
    (* shreg_extract = "no" *)
    reg rx_flag_sync_reg_2 = 1'b0;
    (* shreg_extract = "no" *)
    reg rx_flag_sync_reg_3 = 1'b0;

    always @(posedge clk) begin
        rx_flag_sync_reg_1 <= phy_rx_flag_reg;
        rx_flag_sync_reg_2 <= rx_flag_sync_reg_1;
        rx_flag_sync_reg_3 <= rx_flag_sync_reg_2;
    end

    reg [31:0] update_count_reg = 0;
    reg [31:0] error_count_reg = 0;

    reg [31:0] update_count_mem[(2**RAM_AW)-1:0];
    reg [31:0] error_count_mem[(2**RAM_AW)-1:0];

    integer i;

    initial begin
        for (i = 0; i < 2**RAM_AW; i = i + 1) begin
            update_count_mem[i] = 0;
            error_count_mem[i] = 0;
        end
    end

    reg [ERR_CNT_W+4-1:0] phy_rx_error_count_sync_reg = 0;
    reg phy_rx_error_count_sync_valid_reg = 1'b0;

    always @(posedge clk) begin
        phy_rx_error_count_sync_valid_reg <= 1'b0;
        if (rx_flag_sync_reg_2 ^ rx_flag_sync_reg_3) begin
            phy_rx_error_count_sync_reg <= phy_rx_error_count_reg;
            phy_rx_error_count_sync_valid_reg <= 1'b1;
        end
    end

    reg [1:0] accumulate_state_reg = 0;
    reg [31:0] rx_ts_update_count_rd_reg = 0;
    reg [31:0] rx_ts_error_count_rd_reg = 0;
    reg [31:0] rx_ts_update_count_reg = 0;
    reg [31:0] rx_ts_error_count_reg = 0;

    reg [RAM_AW-1:0] index_reg = 0;

    assign update_count_val[n] = update_count_reg;
    assign error_count_val[n] = error_count_reg;

    always @(posedge clk) begin
        if (phy_rx_error_count_sync_valid_reg) begin
            update_count_reg <= update_count_reg + 1;
            error_count_reg <= error_count_reg + phy_rx_error_count_sync_reg;
        end

        case (accumulate_state_reg)
            2'd0: begin
                index_reg <= ram_acc_index_reg;
                rx_ts_error_count_reg <= phy_rx_error_count_sync_reg;
                if (acc_reg && phy_rx_error_count_sync_valid_reg) begin
                    accumulate_state_reg <= 2'd1;
                end
            end
            2'd1: begin
                rx_ts_update_count_rd_reg <= update_count_mem[index_reg];
                rx_ts_error_count_rd_reg <= error_count_mem[index_reg];

                accumulate_state_reg <= 2'd2;
            end
            2'd2: begin
                rx_ts_update_count_reg <= 1 + rx_ts_update_count_rd_reg;
                rx_ts_error_count_reg <= rx_ts_error_count_reg + rx_ts_error_count_rd_reg;

                accumulate_state_reg <= 2'd3;
            end
            2'd3: begin
                update_count_mem[index_reg] <= rx_ts_update_count_reg;
                error_count_mem[index_reg] <= rx_ts_error_count_reg;

                accumulate_state_reg <= 2'd0;
            end
            default: begin
                accumulate_state_reg <= 2'd0;
            end
        endcase

        if (rst) begin
            update_count_reg <= 0;
            error_count_reg <= 0;

            accumulate_state_reg <= 0;
        end
    end

    reg [31:0] update_count_mem_rd_data_reg = 0;
    reg [31:0] error_count_mem_rd_data_reg = 0;

    assign update_count_rd_val[n] = update_count_mem_rd_data_reg;
    assign error_count_rd_val[n] = error_count_mem_rd_data_reg;

    always @(posedge clk) begin
        if (ram_csr_wr_zero_reg) begin
            update_count_mem[ram_csr_index_reg] <= 0;
            error_count_mem[ram_csr_index_reg] <= 0;
        end else begin
            update_count_mem_rd_data_reg <= update_count_mem[ram_csr_index_reg];
            error_count_mem_rd_data_reg <= error_count_mem[ram_csr_index_reg];
        end
    end

end

endgenerate

// control registers
reg ctrl_reg_wr_ack_reg = 1'b0;
reg [REG_DATA_WIDTH-1:0] ctrl_reg_rd_data_reg = 0;
reg ctrl_reg_rd_ack_reg = 1'b0;

reg tdma_enable_reg = 1'b0;
wire tdma_locked;
wire tdma_error;

reg [79:0] set_tdma_schedule_start_reg = 0;
reg set_tdma_schedule_start_valid_reg = 0;
reg [79:0] set_tdma_schedule_period_reg = 0;
reg set_tdma_schedule_period_valid_reg = 0;
reg [79:0] set_tdma_timeslot_period_reg = 0;
reg set_tdma_timeslot_period_valid_reg = 0;
reg [79:0] set_tdma_active_period_reg = 0;
reg set_tdma_active_period_valid_reg = 0;

assign ctrl_reg_wr_wait = 1'b0;
assign ctrl_reg_wr_ack = ctrl_reg_wr_ack_reg;
assign ctrl_reg_rd_data = ctrl_reg_rd_data_reg;
assign ctrl_reg_rd_wait = 1'b0;
assign ctrl_reg_rd_ack = ctrl_reg_rd_ack_reg;

integer k;

always @(posedge clk) begin
    ctrl_reg_wr_ack_reg <= 1'b0;
    ctrl_reg_rd_data_reg <= 0;
    ctrl_reg_rd_ack_reg <= 1'b0;

    set_tdma_schedule_start_valid_reg <= 1'b0;
    set_tdma_schedule_period_valid_reg <= 1'b0;
    set_tdma_timeslot_period_valid_reg <= 1'b0;
    set_tdma_active_period_valid_reg <= 1'b0;

    ram_csr_wr_zero_reg <= 1'b0;

    cycle_count_reg <= cycle_count_reg + 1;

    if (tdma_timeslot_end) begin
        acc_reg <= 1'b0;
        slice_running_reg <= 1'b0;
        slice_active_reg <= 1'b0;
    end

    if (slice_en_reg) begin
        if (tdma_timeslot_start) begin
            slice_running_reg <= slice_en_reg;
            if (slice_offset_reg) begin
                slice_active_reg <= 1'b0;
                slice_count_reg <= slice_offset_reg;
            end else begin
                acc_reg <= acc_en_reg;
                slice_active_reg <= 1'b1;
                slice_count_reg <= slice_time_reg;
            end
            ram_acc_index_reg <= 0 | (tdma_timeslot_index << slice_shift_reg);
            slice_index_reg <= 0;
        end else if (slice_count_reg > ts_inc_reg) begin
            slice_count_reg <= slice_count_reg - ts_inc_reg;
        end else begin
            slice_count_reg <= slice_count_reg - ts_inc_reg + slice_time_reg;
            acc_reg <= acc_en_reg && slice_running_reg;
            slice_active_reg <= slice_running_reg;
            if (slice_active_reg && slice_running_reg) begin
                ram_acc_index_reg <= (slice_index_reg + 1) | (tdma_timeslot_index << slice_shift_reg);
                slice_index_reg <= slice_index_reg + 1;
                if ((~slice_index_reg & ({RAM_AW{1'b1}} >> (RAM_AW-slice_shift_reg))) == 0) begin
                    slice_running_reg <= 1'b0;
                    slice_active_reg <= 1'b0;
                    acc_reg <= 1'b0;
                end
            end
        end
    end else begin
        if (tdma_timeslot_start) begin
            acc_reg <= acc_en_reg;
            ram_acc_index_reg <= tdma_timeslot_index;
        end
        slice_running_reg <= 1'b0;
        slice_active_reg <= 1'b0;
    end

    if (ctrl_reg_wr_en && !ctrl_reg_wr_ack_reg) begin
        // write operation
        ctrl_reg_wr_ack_reg <= 1'b1;
        case ({ctrl_reg_wr_addr >> 2, 2'b00})
            // TDMA scheduler
            RBB+8'h1C: begin
                // TDMA: control and status
                if (ctrl_reg_wr_strb[0]) begin
                    tdma_enable_reg <= ctrl_reg_wr_data[0];
                end
            end
            RBB+8'h24: set_tdma_schedule_start_reg[29:0] <= ctrl_reg_wr_data;  // TDMA: schedule start ns
            RBB+8'h28: set_tdma_schedule_start_reg[63:32] <= ctrl_reg_wr_data; // TDMA: schedule start sec l
            RBB+8'h2C: begin
                // TDMA: schedule start sec h
                set_tdma_schedule_start_reg[79:64] <= ctrl_reg_wr_data;
                set_tdma_schedule_start_valid_reg <= 1'b1;
            end
            RBB+8'h34: begin
                // TDMA: schedule period ns
                set_tdma_schedule_period_reg[29:0] <= ctrl_reg_wr_data;
                set_tdma_schedule_period_valid_reg <= 1'b1;
            end
            RBB+8'h38: begin
                // TDMA: timeslot period ns
                set_tdma_timeslot_period_reg[29:0] <= ctrl_reg_wr_data;
                set_tdma_timeslot_period_valid_reg <= 1'b1;
            end
            RBB+8'h3C: begin
                // TDMA: active period ns
                set_tdma_active_period_reg[29:0] <= ctrl_reg_wr_data;
                set_tdma_active_period_valid_reg <= 1'b1;
            end
            RBB+8'h4C: begin
                // TDMA BER: control and status
                acc_en_reg <= ctrl_reg_wr_data[0];                // TDMA BER: Accumulate errors
                slice_en_reg <= ctrl_reg_wr_data[1];              // TDMA BER: Slice mode
            end
            RBB+8'h50: cfg_tx_prbs31_enable_reg <= ctrl_reg_wr_data;  // TDMA BER: Control PHY TX pattern generation
            RBB+8'h54: cfg_rx_prbs31_enable_reg <= ctrl_reg_wr_data;  // TDMA BER: Control PHY RX error checkers
            RBB+8'h58: begin
                ram_csr_index_reg <= ctrl_reg_wr_data;            // TDMA BER: RAM index
                ram_csr_wr_zero_reg <= ctrl_reg_wr_data[31];      // TDMA BER: Zero counters
            end
            RBB+8'h5C: cycle_count_reg <= ctrl_reg_wr_data;       // TDMA BER: Cycle count
            RBB+8'h60: slice_time_reg <= ctrl_reg_wr_data;        // TDMA BER: Slice time in cycles
            RBB+8'h64: slice_offset_reg <= ctrl_reg_wr_data;      // TDMA BER: Slice offset in cycles
            RBB+8'h68: slice_shift_reg <= ctrl_reg_wr_data;       // TDMA BER: Slice timeslot index shift
            default: ctrl_reg_wr_ack_reg <= 1'b0;
        endcase
    end

    if (ctrl_reg_rd_en && !ctrl_reg_rd_ack_reg) begin
        // read operation
        ctrl_reg_rd_ack_reg <= 1'b1;
        case ({ctrl_reg_rd_addr >> 2, 2'b00})
            // TDMA BER block
            RBB+8'h00: ctrl_reg_rd_data_reg <= 32'h0000C061;          // TDMA BER block: Type
            RBB+8'h04: ctrl_reg_rd_data_reg <= 32'h00000100;          // TDMA BER block: Version
            RBB+8'h08: ctrl_reg_rd_data_reg <= RB_NEXT_PTR;           // TDMA BER block: Next header
            RBB+8'h0C: ctrl_reg_rd_data_reg <= RB_BASE_ADDR+8'h10;    // TDMA BER block: Offset
            // TDMA scheduler
            RBB+8'h10: ctrl_reg_rd_data_reg <= 32'h0000C060;          // TDMA: Type
            RBB+8'h14: ctrl_reg_rd_data_reg <= 32'h00000200;          // TDMA: Version
            RBB+8'h18: ctrl_reg_rd_data_reg <= RB_BASE_ADDR+8'h40;    // TDMA: Next header
            RBB+8'h1C: begin
                // TDMA: control and status
                ctrl_reg_rd_data_reg[0] <= tdma_enable_reg;
                ctrl_reg_rd_data_reg[8] <= tdma_locked;
                ctrl_reg_rd_data_reg[9] <= tdma_error;
                ctrl_reg_rd_data_reg[31:16] <= 2**TDMA_INDEX_W;
            end
            RBB+8'h24: ctrl_reg_rd_data_reg <= set_tdma_schedule_start_reg[29:0];    // TDMA: schedule start ns
            RBB+8'h28: ctrl_reg_rd_data_reg <= set_tdma_schedule_start_reg[63:32];   // TDMA: schedule start sec l
            RBB+8'h2C: ctrl_reg_rd_data_reg <= set_tdma_schedule_start_reg[79:64];   // TDMA: schedule start sec h
            RBB+8'h34: ctrl_reg_rd_data_reg <= set_tdma_schedule_period_reg[29:0];   // TDMA: schedule period ns
            RBB+8'h38: ctrl_reg_rd_data_reg <= set_tdma_timeslot_period_reg[29:0];   // TDMA: timeslot period ns
            RBB+8'h3C: ctrl_reg_rd_data_reg <= set_tdma_active_period_reg[29:0];     // TDMA: active period ns
            // TDMA BER
            RBB+8'h40: ctrl_reg_rd_data_reg <= 32'h0000C062;          // TDMA BER: Type
            RBB+8'h44: ctrl_reg_rd_data_reg <= 32'h00000100;          // TDMA BER: Version
            RBB+8'h48: ctrl_reg_rd_data_reg <= 0;                     // TDMA BER: Next header
            RBB+8'h4C: begin
                // TDMA BER: control and status
                ctrl_reg_rd_data_reg[0] <= acc_en_reg;                // TDMA BER: Accumulate errors
                ctrl_reg_rd_data_reg[1] <= slice_en_reg;              // TDMA BER: Slice mode
                ctrl_reg_rd_data_reg[15:8] <= COUNT;                  // TDMA BER: Channel count
                ctrl_reg_rd_data_reg[31:16] <= ERR_BITS*16;           // TDMA BER: Bits per update
            end
            RBB+8'h50: ctrl_reg_rd_data_reg <= cfg_tx_prbs31_enable_reg;  // TDMA BER: Control PHY TX pattern generation
            RBB+8'h54: ctrl_reg_rd_data_reg <= cfg_rx_prbs31_enable_reg;  // TDMA BER: Control PHY RX error checkers
            RBB+8'h58: ctrl_reg_rd_data_reg <= ram_csr_index_reg;     // TDMA BER: RAM index
            RBB+8'h5C: ctrl_reg_rd_data_reg <= cycle_count_reg;       // TDMA BER: Cycle count
            RBB+8'h60: ctrl_reg_rd_data_reg <= slice_time_reg;        // TDMA BER: Slice time in ns
            RBB+8'h64: ctrl_reg_rd_data_reg <= slice_offset_reg;      // TDMA BER: Slice offset in ns
            RBB+8'h68: ctrl_reg_rd_data_reg <= slice_shift_reg;       // TDMA BER: Slice timeslot index shift
            RBB+8'h6C: ctrl_reg_rd_data_reg <= 0;
            default: ctrl_reg_rd_ack_reg <= 1'b0;
        endcase
        for (k = 0; k < COUNT; k = k + 1) begin
            if ({ctrl_reg_rd_addr >> 2, 2'b00} == RBB+8'h80 + k*16) begin
                ctrl_reg_rd_data_reg <= update_count_val[k];
                ctrl_reg_rd_ack_reg <= 1'b1;
            end
            if ({ctrl_reg_rd_addr >> 2, 2'b00} == RBB+8'h84 + k*16) begin
                ctrl_reg_rd_data_reg <= error_count_val[k];
                ctrl_reg_rd_ack_reg <= 1'b1;
            end
            if ({ctrl_reg_rd_addr >> 2, 2'b00} == RBB+8'h88 + k*16) begin
                ctrl_reg_rd_data_reg <= update_count_rd_val[k];
                ctrl_reg_rd_ack_reg <= 1'b1;
            end
            if ({ctrl_reg_rd_addr >> 2, 2'b00} == RBB+8'h8C + k*16) begin
                ctrl_reg_rd_data_reg <= error_count_rd_val[k];
                ctrl_reg_rd_ack_reg <= 1'b1;
            end
        end
    end

    cur_ts_reg <= ptp_ts_tod[19:16];
    last_ts_reg <= cur_ts_reg;
    ts_inc_reg <= cur_ts_reg - last_ts_reg;

    if (rst) begin
        ctrl_reg_wr_ack_reg <= 1'b0;
        ctrl_reg_rd_ack_reg <= 1'b0;

        cfg_tx_prbs31_enable_reg <= 0;
        cfg_rx_prbs31_enable_reg <= 0;

        ram_csr_index_reg <= 0;

        cycle_count_reg <= 0;
        slice_time_reg <= 0;
        slice_offset_reg <= 0;

        slice_running_reg <= 1'b0;
        slice_active_reg <= 1'b0;
        slice_count_reg <= 0;
    end
end

tdma_scheduler #(
    .INDEX_WIDTH(TDMA_INDEX_W),
    .SCHEDULE_START_S(48'h0),
    .SCHEDULE_START_NS(30'h0),
    .SCHEDULE_PERIOD_S(48'd0),
    .SCHEDULE_PERIOD_NS(30'd1000000),
    .TIMESLOT_PERIOD_S(48'd0),
    .TIMESLOT_PERIOD_NS(30'd100000),
    .ACTIVE_PERIOD_S(48'd0),
    .ACTIVE_PERIOD_NS(30'd100000)
)
tdma_scheduler_inst (
    .clk(clk),
    .rst(rst),
    .input_ts_96(ptp_ts_tod),
    .input_ts_step(ptp_ts_tod_step),
    .enable(tdma_enable_reg),
    .input_schedule_start(set_tdma_schedule_start_reg),
    .input_schedule_start_valid(set_tdma_schedule_start_valid_reg),
    .input_schedule_period(set_tdma_schedule_period_reg),
    .input_schedule_period_valid(set_tdma_schedule_period_valid_reg),
    .input_timeslot_period(set_tdma_timeslot_period_reg),
    .input_timeslot_period_valid(set_tdma_timeslot_period_valid_reg),
    .input_active_period(set_tdma_active_period_reg),
    .input_active_period_valid(set_tdma_active_period_valid_reg),
    .locked(tdma_locked),
    .error(tdma_error),
    .schedule_start(tdma_schedule_start),
    .timeslot_index(tdma_timeslot_index),
    .timeslot_start(tdma_timeslot_start),
    .timeslot_end(tdma_timeslot_end),
    .timeslot_active(tdma_timeslot_active)
);

endmodule

`resetall
