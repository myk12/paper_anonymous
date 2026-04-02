#!/bin/bash
# environment: bash
VIVADO_PATH="/INET/tools/Xilinx/Vivado/2023.1"

set -e  # exit on error

# Check argument: bitstream file path
if [ "$#" -ne 1 ]; then
    echo "Usage: $0 <bitstream.bit>"
    echo "Example: $0 my_design.bit"
    exit 1
fi

BITFILE="$1"

# Check if Vivado is available
if ! which vivado >/dev/null; then
    echo "Error: Vivado not found in PATH. Please run source /opt/Xilinx/Vivado/<version>/settings64.sh"
    exit 1
fi

# Check if bitfile exists
if [ ! -f "$BITFILE" ]; then
    echo "Error: File '$BITFILE' does not exist"
    exit 1
fi

echo "Starting FPGA programming with bitstream: $BITFILE"

# Run TCL script in Vivado batch mode (here-document style TCL)
if vivado -mode batch -nolog -nojournal -source /dev/stdin -tclargs "$BITFILE" << "EOF"
# TCL script start: batch program all detected FPGAs
set bitfile [lindex $argv 0]

# Open hardware manager
open_hw_manager

# Connect to hw_server (default localhost:3121, can be modified)
connect_hw_server -host localhost -port 3121

# Open target (all hw_targets)
open_hw_target [get_hw_targets *]

# Get all FPGA devices
set devices [get_hw_devices]

if {[llength $devices] == 0} {
    error "No FPGA devices detected. Please check JTAG connection and hw_server."
}

# Loop program each device (assuming all devices use the same bitfile)
foreach device $devices {
    puts "Programming device: $device"
    
    # Set bitstream file
    set_property PROGRAM.FILE $bitfile $device
    
    # Clear probes file (optional, if there is ILA)
    set_property PROBES.FILE {} $device
    
    # Refresh device
    refresh_hw_device $device
    
    # Program device
    program_hw_devices $device
    
    puts "Device $device programming completed."
}

# Close connections
close_hw_target [current_hw_target]
disconnect_hw_server [hw_server]
close_hw_manager

puts "All FPGA programming completed successfully."
EOF
then
    echo -e "\nBatch programming succeeded!"
else
    echo -e "\nBatch programming failed. Please check the logs."
    exit 1
fi
echo "FPGA programming finished."
