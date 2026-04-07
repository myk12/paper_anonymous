module cocotb_iverilog_dump();
initial begin
    $dumpfile("sim_build/exec_table.fst");
    $dumpvars(0, exec_table);
end
endmodule
