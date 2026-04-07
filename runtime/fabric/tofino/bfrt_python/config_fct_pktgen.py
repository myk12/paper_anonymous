#!/bfshell/bin/env python3


# descrption: This script configures the packet generator on a Tofino-based switch using BFRT APIs.

import os
import sys
import argparse
import time
import struct
from scapy.all import Ether, Raw, IP, UDP
from loguru import logger

logger.remove()
logger.add(sys.stdout, level="INFO")
##########################################################################
# Configuration Parameters
##########################################################################
P4_PROG = os.getenv("P4_PROG", "hybrid_arch")
PKTGEN_APP_ID = int(os.getenv("PKTGEN_APP_ID", "1"))
PKTGEN_PORT_ID = int(os.getenv("PKTGEN_PORT_ID", "6"))

# Default for packet
SRC_MAC = os.environ.get("SRC_MAC", "00:0a:35:06:50:95")
DST_MAC = os.environ.get("DST_MAC", "00:0a:35:06:50:94")
SRC_IP = os.environ.get("SRC_IP", "177.0.1.2")
DST_IP = os.environ.get("DST_IP", "177.0.1.1")
SRC_PORT = int(os.environ.get("SRC_PORT", "1999"))
DST_PORT = int(os.environ.get("DST_PORT", "1999"))

##########################################################################
#           HOPVAR CONFIGURATION PARAMETERS
##########################################################################
HOPVAR_MAGIC = 0x484F5056
HOPVAR_VER = 1
HOPVAR_TS_SLOTS = 10
HOPVAR_HEADER_LEN = 88

ETH_HDR_LEN = 14
IP_HDR_LEN = 20
UDP_HDR_LEN = 8
MIN_PKT_SIZE = ETH_HDR_LEN + IP_HDR_LEN + UDP_HDR_LEN + HOPVAR_HEADER_LEN


###########################################################################
# Packet Creation Function
###########################################################################
def calc_timer_nanoseconds(rate_Gbps: int, packet_size: int) -> int:
    assert rate_Gbps > 0, "Rate must be greater than 0"
    
    total_bytes = packet_size + 20  # Adding 20 bytes for inter-frame gap and preamble
    bits_per_second = rate_Gbps * 1_000_000_000
    packets_per_second = bits_per_second / (total_bytes * 8)
    timer_ns = int(1_000_000_000 / packets_per_second)
    return timer_ns

def build_hopvar_header(req_id: int = 0) -> bytes:
    """
    Build an 88-byte hopvar header matching:

    header hopvar_hdr_h {
        bit<32> magic;
        bit<16> ver;
        bit<16> hdr_len;
        bit<64> req_id;
        bit<16> flags;
        bit<16> ts_count;
        bit<48> ts0..ts9;
        bit<32> valid_bitmap;
        bit<32> reserved;
    }

    All fields are network-order / big-endian.
    """
    flags = 0
    ts_count = 0
    valid_bitmap = 0
    reserved = 0
    
    parts = []
    
    # Fixed fields
    parts.append(struct.pack('!I', HOPVAR_MAGIC))  # magic
    parts.append(struct.pack('!H', HOPVAR_VER))    # ver
    parts.append(struct.pack('!H', HOPVAR_HEADER_LEN))  # hdr_len
    parts.append(struct.pack('!Q', req_id))        # req_id
    parts.append(struct.pack('!H', flags))         # flags
    parts.append(struct.pack('!H', ts_count))      # ts_count
    
    # ts0..ts9 as 48-bit zero values
    for _ in range(HOPVAR_TS_SLOTS):
        parts.append((0).to_bytes(6, byteorder='big'))  # 48 bits = 6 bytes
    
    # tail fields
    parts.append(struct.pack('!I', valid_bitmap))  # valid_bitmap
    parts.append(struct.pack('!I', reserved))      # reserved
    
    hdr = b''.join(parts)
    assert len(hdr) == HOPVAR_HEADER_LEN, f"Hopvar header must be {HOPVAR_HEADER_LEN} bytes, got {len(hdr)}"
    return hdr

#########################################################################
#       Packet Creation Function with Hopvar Header
#########################################################################
def make_packet(packet_size: int) -> Ether:
    """
    Create a packet of exact L2 size `packet_size` bytes:
      Ethernet / IPv4 / UDP / hopvar_hdr / padding
    """
    assert packet_size >= MIN_PKT_SIZE, (
        f"Packet size must be at least {MIN_PKT_SIZE} bytes "
        f"to hold Eth+IP+UDP+hopvar header"
    )
    
    hopvar_hdr = build_hopvar_header(req_id=1)  # Example req_id, can be parameterized

    payload_size = packet_size - ETH_HDR_LEN - IP_HDR_LEN - UDP_HDR_LEN - HOPVAR_HEADER_LEN
    payload = hopvar_hdr + (b'7' * payload_size)  # Pad with '7' bytes to reach desired size

    pkt = Ether(src=SRC_MAC, dst=DST_MAC) / \
          IP(src=SRC_IP, dst=DST_IP) / \
          UDP(sport=SRC_PORT, dport=DST_PORT) / \
          Raw(load=payload)
        
    built = pkt.build()
    assert len(built) == packet_size, f"Built packet size {len(built)} does not match requested size {packet_size}"
    return pkt

###########################################################################
# Packet Generator Configuration Function
###########################################################################

def config_pktgen_buffers(bfrt, rate: int, packet_size: int):
    """Configure packet generator buffers with specified rate and packet size."""
    logger.info("Configuring Packet Generator Buffers")
    pktgen_buffer = bfrt.tf2.pktgen.pkt_buffer
    
    # Create Packet Buffer Entry
    packet = make_packet(packet_size)
    built = packet.build()
    assert len(built) == packet_size, f"Built packet size {len(built)} does not match requested size {packet_size}"
    
    logger.info(
        f"Packet built: total len={len(built)} bytes, "
        f"Payload len={len(built) - ETH_HDR_LEN - IP_HDR_LEN - UDP_HDR_LEN} bytes"
        f"Hopvar header len={HOPVAR_HEADER_LEN} bytes"
    )
    # Write Packet to Buffer
    try:
        # first create a new entry
        buffer_entry = pktgen_buffer.entry(
            pkt_buffer_offset=0,
            pkt_buffer_size=len(built),
            buffer=list(built)
        )
        buffer_entry.push()
        logger.info("Packet written to buffer")
    except Exception as e:
        # or modify existing entry if it already exists
        logger.warning(f"Failed to create new buffer entry, trying to modify existing entry: {e}")
        buffer_entry = pktgen_buffer.mod(
            pkt_buffer_offset=0,
            pkt_buffer_size=len(built),
            buffer=list(built)
        )
        buffer_entry.push()
        logger.info("Packet written to buffer")

def config_pktgen_app(bfrt, rate: int, packet_size: int):
    """Configure packet generator application with specified rate and packet size."""
    logger.info("Configuring Packet Generator Application")
    pktgen_app = bfrt.tf2.pktgen.app_cfg

    timer_ns = calc_timer_nanoseconds(rate, packet_size)

    try:    
        # first try to Create App Entry
        app_entry = pktgen_app.entry_with_trigger_timer_periodic(
            app_id=PKTGEN_APP_ID,
            app_enable=True,
            pkt_buffer_offset=0,
            pkt_len=packet_size,
            pipe_local_source_port=PKTGEN_PORT_ID,
            increment_source_port=False,
            timer_nanosec=timer_ns,
            batch_count_cfg=0,
            packets_per_batch_cfg=0,
            ibg=0, ibg_jitter=0,
            ipg=0, ipg_jitter=0,
            batch_counter=0, pkt_counter=0, trigger_counter=0,
            offset_len_from_recir_pkt_enable=False,
            source_port_wrap_max=0,
            assigned_chnl_id=PKTGEN_PORT_ID,
        )
        app_entry.push()
    except Exception as e:
        logger.warning(f"Failed to create new app entry, trying to modify existing entry: {e}")
        app_entry = pktgen_app.mod_with_trigger_timer_periodic(
            app_id=PKTGEN_APP_ID,
            app_enable=True,
            pkt_buffer_offset=0,
            pkt_len=packet_size,
            pipe_local_source_port=PKTGEN_PORT_ID,
            increment_source_port=False,
            timer_nanosec=timer_ns,
            batch_count_cfg=0,
            packets_per_batch_cfg=0,
            ibg=0, ibg_jitter=0,
            ipg=0, ipg_jitter=0,
            batch_counter=0, pkt_counter=0, trigger_counter=0,
            offset_len_from_recir_pkt_enable=False,
            source_port_wrap_max=0,
            assigned_chnl_id=PKTGEN_PORT_ID,
        )
        app_entry.push()
    logger.info(f"Packet Generator App configured: Rate={rate}Gbps, Packet Size={packet_size} bytes")

def config_pktgen_ports(bfrt):
    """Enable Packet Generator on specified port."""
    logger.info("Configuring Packet Generator Ports")
    pktgen_port = bfrt.tf2.pktgen.port_cfg

    port_entry = pktgen_port.entry(
        dev_port=PKTGEN_PORT_ID,
        pktgen_enable=True
    )
    port_entry.push()
    logger.info(f"Packet Generator enabled on port {PKTGEN_PORT_ID}")
    
############################################################################
# Main Function
############################################################################
def argument_parser():
    parser = argparse.ArgumentParser(
        description="Configure Packet Generator on Tofino Switch"
    )
    parser.add_argument("--rate", type=int, default=1,
                        help="Packet generation rate in Gbps (default: 1)")
    parser.add_argument("--packet_size", type=int, default=1024,
                        help="Packet size in bytes (default: 1024)")
    
    return parser

def main():
    logger.info("Starting Packet Generator Configuration Script")
    args = argument_parser().parse_args()

    assert 'bfrt' in globals(), "This script must be run using bfshell to access BFRT APIs."
    bfrt = globals()['bfrt']

    config_pktgen_buffers(bfrt, args.rate, args.packet_size)

    config_pktgen_app(bfrt, args.rate, args.packet_size)

    config_pktgen_ports(bfrt)
    logger.info("Packet Generator Configuration Completed")

if __name__ == "__main__":
    main()
