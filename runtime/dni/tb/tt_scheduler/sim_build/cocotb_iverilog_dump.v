module cocotb_iverilog_dump();
initial begin
    $dumpfile("sim_build/tt_scheduler.fst");
    $dumpvars(0, tt_scheduler);
end
endmodule
