`timescale 1ns / 1ps

/*
 * Synchronous Consensus Core Module:
 *
 * This core runs inside a synchronous distributed system driven by compiled
 * execution windows.  The surrounding schedule executor decides when a local
 * consensus collect window opens and when the protocol should transition to its
 * commit phase.  The core therefore focuses only on protocol state, not on
 * PHC math or online schedule arbitration.
 *
 * Current implementation note:
 * one logical consensus round is expected to map to one execution window.  The
 * surrounding packet format uses the executor's current_window_id as the round
 * identifier, so splitting one logical round across multiple execution windows
 * would currently change the on-wire round id.
 *
*/

module consensus_core #(
    parameter P_NODE_COUNT = 3,
    parameter P_NODE_ID = 0,
    parameter P_CONSENSUS_QUORUM = (P_NODE_COUNT / 2 + 1),
    parameter P_LOG_ITEM_LEN = 16, // in bytes
    parameter P_NODE_ID_WIDTH = 8,
    parameter P_KV_WIDTH = 8
) (
    // clock and reset
    input wire                                  clk,
    input wire                                  rst_n,
    input wire                                  i_clear_halt,

    // timing control interface (from the compiled schedule executor)
    input wire [63:0]                           i_current_window_id,
    input wire                                  i_window_open_pulse,
    input wire                                  i_commit_start_pulse,
    input wire                                  i_window_close_pulse,

    // data interface
    input wire                                  i_rx_valid,
    input wire [P_NODE_ID_WIDTH-1:0]            i_rx_node_id,
    input wire [P_KV_WIDTH-1:0]                 i_rx_knowledge_vec,
    input wire [P_LOG_ITEM_LEN*8-1:0]           i_rx_propose,

    // status outputs
    output reg [P_NODE_COUNT-1:0]               o_alive_mask,
    output reg                                  o_system_halt,   // high when system halts

    // data output
    output reg [P_NODE_COUNT-1:0]               o_tx_knowledge_vec,
    output wire [P_LOG_ITEM_LEN*8-1:0]          o_tx_propose,

    // application data output (committed logs)
    output reg [P_LOG_ITEM_LEN*8*P_NODE_COUNT-1:0]      o_commit_log,
    output reg [P_NODE_COUNT-1:0]                       o_commit_valid
);

//------------------------------------------------
//         State Machine for Consensus Processing
//------------------------------------------------
localparam S_IDLE           = 2'b00;
localparam S_COLLECT        = 2'b01;
localparam S_FAIL_DETECT    = 2'b10;
localparam S_COMMIT         = 2'b11;

reg [1:0]   state, next_state;
//------------------------------------------------
//         Internal Storage (Buffer)
//------------------------------------------------
// global status of each node
reg [P_NODE_COUNT-1:0]          r_alive_mask;                           // alive mask
reg [P_NODE_COUNT-1:0]          r_knowledge_matrix [0:P_NODE_COUNT-1];  // knowledge matrix
reg [P_NODE_COUNT-1:0]          r_rx_mask;
reg [P_NODE_COUNT-1:0]          r_last_rx_mask;     // This is my own knowledge vector                      // received mask

// logs observed in the current execution window
reg [P_LOG_ITEM_LEN*8-1:0]      r_propose_log [0:P_NODE_COUNT-1];       // proposed logs
reg [P_LOG_ITEM_LEN*8-1:0]      r_commit_log [0:P_NODE_COUNT-1];        // acknowledged logs
wire [P_NODE_COUNT-1:0]        r_consensus_reached;                    // consensus reached for each node
wire [7:0]                     r_rx_number;                            // number of received packets

reg [$clog2(P_NODE_COUNT+1)-1:0]  r_column_sum [0:P_NODE_COUNT-1];        // column sum of knowledge matrix
wire [P_NODE_COUNT-1:0]        r_others_saw_me;                     // other nodes saw me the same way

reg                            r_am_i_blind;                           // self blind detection
reg                            r_am_i_mute;                          // self mute detection
reg                            r_halt_condition_met;                  // halt condition met

// propose padding with NODE_ID
assign o_tx_propose = {P_LOG_ITEM_LEN{P_NODE_ID[7:0]}};

// global loop variables
integer i, j, k;

// ------------------------------------------------
//              3. combinational logic
// ------------------------------------------------

// 3.1 calculate received packets count
assign r_rx_number = count_ones(r_rx_mask);

function [$clog2(P_NODE_COUNT+1)-1:0] count_ones;
    input [P_NODE_COUNT-1:0] vec;
    integer idx;
    begin
        count_ones = 0;
        for (idx = 0; idx < P_NODE_COUNT; idx = idx + 1) begin
            if (vec[idx]) begin
                count_ones = count_ones + 1;
            end
        end
    end
endfunction

// 3.2 calculate column sums and consensus reached
always @(*) begin
    for (k = 0; k < P_NODE_COUNT; k = k + 1) begin
        r_column_sum[k] = 0;
        for (j = 0; j < P_NODE_COUNT; j = j + 1) begin
            r_column_sum[k] = r_column_sum[k] + (r_knowledge_matrix[j][k] ? r_alive_mask[j] : 1'b0);
        end
    end
end

genvar g;
generate
    for (g = 0; g < P_NODE_COUNT; g = g + 1) begin : cons_check
        assign r_consensus_reached[g] = (r_column_sum[g] >= P_CONSENSUS_QUORUM) ? 1'b1 : 1'b0;
    end
endgenerate

// 3.3 self diagnosis: blind and mute detection
// "Blind": if I see no other alive nodes
assign r_am_i_blind = ((r_alive_mask & r_rx_mask & ~(1 << P_NODE_ID)) == 0);

genvar os;
generate
    for (os = 0; os < P_NODE_COUNT; os = os + 1) begin : others_saw_me_gen
        assign r_others_saw_me[os] = r_alive_mask[os] && r_knowledge_matrix[os][P_NODE_ID];
    end
endgenerate

// "Mute": if no other alive nodes saw me as alive
assign r_am_i_mute = ((r_others_saw_me & r_alive_mask & ~(1 << P_NODE_ID)) == 0);

assign r_halt_condition_met = (r_rx_number < P_CONSENSUS_QUORUM) || r_am_i_blind || r_am_i_mute;

// ------------------------------------------------
//  FSM PART 1: state register update (sequential)
// ------------------------------------------------

always @(posedge clk) begin
    if (!rst_n || i_clear_halt) begin
        state <= S_IDLE;
    end else begin
        state <= next_state;
    end
end

// ------------------------------------------------
//  FSM PART 2: next state logic and outputs (combinational)
// ------------------------------------------------
always @(*) begin
    // default assignments
    next_state = state;

    if (i_window_open_pulse & !o_system_halt) begin
        // Open a new synchronous collect window.
        next_state = S_COLLECT;
    end
    else begin
        case (state)
            S_IDLE: begin
                if (i_window_open_pulse & !o_system_halt) begin
                    next_state = S_COLLECT;
                end
            end
            S_COLLECT: begin
                if (i_commit_start_pulse) begin
                    next_state = S_FAIL_DETECT;
                end
            end
            S_FAIL_DETECT: begin
                if (o_system_halt) begin
                    next_state = S_IDLE;
                end else begin
                    next_state = S_COMMIT;
                end
            end
            S_COMMIT: begin
                next_state = S_IDLE;
            end
        endcase
    end
end

// ------------------------------------------------
//  FSM PART 3: state actions (sequential)
// ------------------------------------------------
always @(posedge clk) begin
    if (!rst_n || i_clear_halt) begin
        // reset all registers
        for (i = 0; i < P_NODE_COUNT; i = i + 1) begin
            r_knowledge_matrix[i] <= {P_NODE_COUNT{1'b0}};
            r_propose_log[i] <= 0;
            r_commit_log[i] <= 0;
            r_rx_mask[i] <= 1'b0;
            r_last_rx_mask[i] <= 1'b0;

            o_commit_valid[i] <= 1'b0;
            o_commit_log[i*P_LOG_ITEM_LEN*8 +: P_LOG_ITEM_LEN*8] <= 0;
        end
        r_alive_mask <= {P_NODE_COUNT{1'b1}}; // all alive at start
        r_rx_mask <= {P_NODE_COUNT{1'b1}}; // all alive at start
        r_last_rx_mask <= {P_NODE_COUNT{1'b0}};
        o_system_halt <= 1'b0;
        o_alive_mask <= {P_NODE_COUNT{1'b1}};
        o_tx_knowledge_vec <= {P_NODE_COUNT{1'b1}};

    end else begin
        case (state)
            S_IDLE: begin
                // Prepare for the next execution window.
                if (next_state == S_COLLECT) begin
                    // Clear buffers
                    for (i = 0; i < P_NODE_COUNT; i = i + 1) begin
                        r_knowledge_matrix[i] <= {P_NODE_COUNT{1'b0}};
                        r_propose_log[i] <= 0;

                        o_commit_valid[i] <= 1'b0;
                        o_commit_log[i*P_LOG_ITEM_LEN*8 +: P_LOG_ITEM_LEN*8] <= 0;
                    end
                    r_last_rx_mask      <= r_rx_mask;
                    o_tx_knowledge_vec  <= r_rx_mask; // update my own knowledge vector
                    // Reset rx mask
                    r_rx_mask           <= {P_NODE_COUNT{1'b0}};
                end
            end
            S_COLLECT: begin
                // Collect incoming packets
                // set self rx mask
                r_rx_mask[P_NODE_ID] <= 1'b1;
                r_knowledge_matrix[P_NODE_ID] <= r_last_rx_mask;

                // process incoming packets
                if (i_rx_valid) begin
                    // receive valid packet record
                    r_knowledge_matrix[i_rx_node_id] <= i_rx_knowledge_vec;
                    r_propose_log[i_rx_node_id] <= i_rx_propose;
                    r_rx_mask[i_rx_node_id] <= 1'b1;
                end
            end
            S_FAIL_DETECT: begin
                // Only update logic if we are not already halting
                if (!o_system_halt) begin
                    if (r_halt_condition_met) begin
                        o_system_halt <= 1'b1;
                    end else begin
                        // Perform Alive Mask Update
                        for (i = 0; i < P_NODE_COUNT; i = i + 1) begin
                            // Rule A: Dead if unresponsive (did not send packet)
                            if (r_rx_mask[i] == 1'b0) begin
                                r_alive_mask[i] <= 1'b0; // mark as dead
                            end
                            // Rule B: Dead if did not ack consensused logs
                            else begin
                                for (j = 0; j < P_NODE_COUNT; j = j + 1) begin
                                    if (r_consensus_reached[j] && !r_knowledge_matrix[i][j]) begin
                                        r_alive_mask[i] <= 1'b0; // mark as dead
                                    end
                                end
                            end
                        end
                        o_alive_mask <= r_alive_mask;   // update output alive mask
                    end
                end
            end
            S_COMMIT: begin
                // Commit logs based on consensus
                for (k = 0; k < P_NODE_COUNT; k = k + 1) begin
                    if (r_consensus_reached[k]) begin
                        r_commit_log[k] <= r_propose_log[k];
                        o_commit_log[k*P_LOG_ITEM_LEN*8 +: P_LOG_ITEM_LEN*8] <= r_propose_log[k];
                        o_commit_valid[k] <= 1'b1;
                    end else begin
                        o_commit_valid[k] <= 1'b0;
                    end
                end
            end
        endcase
    end
end

endmodule
