module cocotb_iverilog_dump();
initial begin
    $dumpfile("sim_build/processor_runtime.fst");
    $dumpvars(0, processor_runtime);
end
endmodule
