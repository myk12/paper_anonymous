#!/bfshell/bin/env python3
# -*- coding: utf-8 -*-
# author: Yuke Ma
# date: 2025-12-17
# description: Tofino Spine-Leaf Topology Control Plane Setup Script

# This script sets up the Tofino switch in a spine-leaf topology.
# It configures forwarding rules based on the provided topology YAML file.
# This script is intended to be run before starting any traffic tests.

import os
import sys
import yaml
from loguru import logger
logger.remove()
logger.add(sys.stdout, level="INFO")

###################################################
#                 Configuration
###################################################

PROJ_ROOT = os.environ.get('PROJ_ROOT', '/home/p4/spine-leaf')
P4_PROG = os.environ.get('P4_PROG', 'spineleaf')

# Logical switch ID for this setup
LEAF1_ID = 1
LEAF2_ID = 2
LEAF3_ID = 3
LEAF4_ID = 4
SPINE_ID = 10

# Port definitions
# Leaf 1
LEAF1_P1 = 1  # to server 1 nic 1
LEAF1_P2 = 2  # to server 1 nic 2
LEAF1_P4 = 4  # to spine switch

# Leaf 2
LEAF2_P1 = 5  # to server 2 nic 1
LEAF2_P2 = 6  # to server 2 nic 2
LEAF2_P4 = 8  # to spine switch

# Leaf 3
LEAF3_P1 = 9  # to server 3 nic 1
LEAF3_P2 = 10  # to server 3 nic 2
LEAF3_P4 = 12  # to spine switch

# Leaf 4
LEAF4_P1 = 13  # to server 4 nic 1
LEAF4_P2 = 14  # to server 4 nic 2
LEAF4_P4 = 16  # to spine switch

# Spine
SPINE_P1 = 17  # to leaf 1
SPINE_P2 = 19  # to leaf 2
SPINE_P3 = 21  # to leaf 3
SPINE_P4 = 23  # to leaf 4

# all ports
all_ports = [
    LEAF1_P1, LEAF1_P2, LEAF1_P4,
    LEAF2_P1, LEAF2_P2, LEAF2_P4,
    LEAF3_P1, LEAF3_P2, LEAF3_P4,
    LEAF4_P1, LEAF4_P2, LEAF4_P4,
    SPINE_P1, SPINE_P2, SPINE_P3, SPINE_P4
]

# emulated MAC address for tofino ports
MAC_LEAF1_P1 = "00:11:22:33:44:01"
MAC_LEAF1_P2 = "00:11:22:33:44:02"
MAC_LEAF1_P4 = "00:11:22:33:44:04"
MAC_LEAF2_P1 = "00:11:22:33:44:05"
MAC_LEAF2_P2 = "00:11:22:33:44:06"
MAC_LEAF2_P4 = "00:11:22:33:44:08"
MAC_LEAF3_P1 = "00:11:22:33:44:09"
MAC_LEAF3_P2 = "00:11:22:33:44:0A"
MAC_LEAF3_P4 = "00:11:22:33:44:0C"
MAC_LEAF4_P1 = "00:11:22:33:44:0D"
MAC_LEAF4_P2 = "00:11:22:33:44:0E"
MAC_LEAF4_P4 = "00:11:22:33:44:10"
MAC_SPINE_P1  = "00:11:22:33:44:11"
MAC_SPINE_P2  = "00:11:22:33:44:12"
MAC_SPINE_P3  = "00:11:22:33:44:13"
MAC_SPINE_P4  = "00:11:22:33:44:14"

# endpoint MAC for routing
MAC_ENDPOINT1_P1 = "00:0a:35:06:50:94"
MAC_ENDPOINT1_P2 = "00:0a:35:06:50:95"
MAC_ENDPOINT2_P1 = "00:0a:35:06:09:24"
MAC_ENDPOINT2_P2 = "00:0a:35:06:09:25"
MAC_ENDPOINT3_P1 = "00:0a:35:06:0b:84"
MAC_ENDPOINT3_P2 = "00:0a:35:06:0b:85"
MAC_ENDPOINT4_P1 = "00:0a:35:06:09:3c"
MAC_ENDPOINT4_P2 = "00:0a:35:06:09:3d"
MAC_ENDPOINT5_P1 = "00:0a:35:06:0b:72"
MAC_ENDPOINT5_P2 = "00:0a:35:06:0b:73"
MAC_ENDPOINT6_P1 = "00:0a:35:06:09:9c"
MAC_ENDPOINT6_P2 = "00:0a:35:06:09:9d"
MAC_ENDPOINT7_P1 = "00:0a:35:06:09:8a"
MAC_ENDPOINT7_P2 = "00:0a:35:06:09:8b"
MAC_ENDPOINT8_P1 = "00:0a:35:06:09:30"
MAC_ENDPOINT8_P2 = "00:0a:35:06:09:31"

# ============================================================
#               Helper Functions
# ============================================================

def get_port_hdl(bfrt, conn_id: int, chnl_id: int) -> int:
    """Get the device port handle from connection ID and channel ID."""
    dev_port = bfrt.port.port_hdl_info.get(CONN_ID=conn_id, CHNL_ID=chnl_id, print_ents=False).data[b'$DEV_PORT']
    return int(dev_port)

def is_fpga_port(bfrt, port: int) -> bool:
    """Check if the given device port is an FPGA port."""
    if port in [LEAF1_P1, LEAF1_P2, LEAF2_P1, LEAF2_P2, LEAF3_P1, LEAF3_P2, LEAF4_P1, LEAF4_P2]:
        return True
    return False

def clear_all_tables(bfrt_ingress):
    """Clear all tables in the P4 program."""
    logger.info("Clearing all tables...")
    bfrt_ingress.t_ipv4_lpm.clear()
    bfrt_ingress.t_port_mapping.clear()
    logger.info("All tables cleared.")

def init_ports(bfrt):
    """Initialize port configurations."""
    logger.info("Initializing port configurations...")
    for port in all_ports:
        dp = int(get_port_hdl(bfrt, port, 0))
        if is_fpga_port(bfrt, port):
            logger.info(f"  Configuring port {port} as FPGA port")
            bfrt.port.port.add(DEV_PORT=dp,
                               SPEED='BF_SPEED_100G',
                               FEC='BF_FEC_TYP_RS',
                               AUTO_NEGOTIATION='PM_AN_FORCE_DISABLE',
                               PORT_ENABLE=True)
        else:
            logger.info(f"  Configuring port {port} as non-FPGA port")
            bfrt.port.port.add(DEV_PORT=dp,
                               SPEED='BF_SPEED_100G',
                               FEC='BF_FEC_TYP_NONE',
                               AUTO_NEGOTIATION='PM_AN_FORCE_DISABLE',
                               PORT_ENABLE=True)
    logger.info("Port configurations initialized.")

def add_port_mapping(bfrt, phys_port: int, log_switch_id: int):
    """Add port mapping entry."""
    logger.info(f"Adding port mapping: phys_port={phys_port} -> log_switch_id={log_switch_id}")
    dev_port = get_port_hdl(bfrt, phys_port, 0)
    port_mapping_table = bfrt.spineleaf.pipe.Ingress.t_port_mapping
    port_mapping_table.add_with_set_logical_switch(
        ingress_port=dev_port,
        switch_id=log_switch_id
    )
    logger.info(f"Port mapping added for DEV_PORT={dev_port} to switch_id={log_switch_id}")

def add_ipv4_route(bfrt, log_switch_id: int, dst_ip: str, prefix_len: int, out_port: int):
    """Add IPv4 routing entry."""
    logger.info(f"Adding IPv4 route: log_switch_id={log_switch_id}, dst_ip={dst_ip}/{prefix_len} -> out_port={out_port}")
    dev_port = get_port_hdl(bfrt, out_port, 0)
    ipv4_table = bfrt.spineleaf.pipe.Ingress.t_ipv4_lpm
    ipv4_table.add_with_ipv4_forward(
        logical_switch_id=log_switch_id,
        dst_addr=dst_ip,
        dst_addr_p_length=prefix_len,
        port=dev_port
    )
    logger.info(f"IPv4 route added for dst_ip={dst_ip}/{prefix_len} on switch_id={log_switch_id}")

# ============================================================
#               Main Setup Function
# ============================================================

logger.info("Starting Tofino Spine-Leaf Topology Control Plane Setup...")

# Get BFRT instance
assert 'bfrt' in globals(), "This script must be run in bfshell with bfrt loaded."
bfrt = globals()['bfrt']
bfrt_ingress = bfrt.spineleaf.pipe.Ingress
# port_mapping table

# 1. Clear all tables
clear_all_tables(bfrt_ingress)

# 2. Initialize ports
init_ports(bfrt)

# 3. Configure port mappings
# Leaf 1
add_port_mapping(bfrt, LEAF1_P1, LEAF1_ID)
add_port_mapping(bfrt, LEAF1_P2, LEAF1_ID)
add_port_mapping(bfrt, LEAF1_P4, LEAF1_ID)

# Leaf 2
add_port_mapping(bfrt, LEAF2_P1, LEAF2_ID)
add_port_mapping(bfrt, LEAF2_P2, LEAF2_ID)
add_port_mapping(bfrt, LEAF2_P4, LEAF2_ID)

# Leaf 3
add_port_mapping(bfrt, LEAF3_P1, LEAF3_ID)
add_port_mapping(bfrt, LEAF3_P2, LEAF3_ID)
add_port_mapping(bfrt, LEAF3_P4, LEAF3_ID)

# Leaf 4
add_port_mapping(bfrt, LEAF4_P1, LEAF4_ID)
add_port_mapping(bfrt, LEAF4_P2, LEAF4_ID)
add_port_mapping(bfrt, LEAF4_P4, LEAF4_ID)

# Spine
add_port_mapping(bfrt, SPINE_P1, SPINE_ID)
add_port_mapping(bfrt, SPINE_P2, SPINE_ID)
add_port_mapping(bfrt, SPINE_P3, SPINE_ID)
add_port_mapping(bfrt, SPINE_P4, SPINE_ID)

# 4. Configure IPv4 routes
logger.info("Configuring IPv4 routes...")
# Leaf 1 routes
# downstream to server 1
add_ipv4_route(bfrt, LEAF1_ID, "10.0.1.1", 32, LEAF1_P1)
add_ipv4_route(bfrt, LEAF1_ID, "10.0.1.2", 32, LEAF1_P1)
add_ipv4_route(bfrt, LEAF1_ID, "10.0.1.3", 32, LEAF1_P2)
add_ipv4_route(bfrt, LEAF1_ID, "10.0.1.4", 32, LEAF1_P2)
# upstream to spine
#FIXME: be careful about the risk of loop here!!!
add_ipv4_route(bfrt, LEAF1_ID, "10.0.0.0", 8, LEAF1_P4)
# Leaf 2 routes
# downstream to server 2
add_ipv4_route(bfrt, LEAF2_ID, "10.0.2.1", 32, LEAF2_P1)
add_ipv4_route(bfrt, LEAF2_ID, "10.0.2.2", 32, LEAF2_P1)
add_ipv4_route(bfrt, LEAF2_ID, "10.0.2.3", 32, LEAF2_P2)
add_ipv4_route(bfrt, LEAF2_ID, "10.0.2.4", 32, LEAF2_P2)
# upstream to spine
add_ipv4_route(bfrt, LEAF2_ID, "10.0.0.0", 8, LEAF2_P4)

# Leaf 3 routes
# downstream to server 3
add_ipv4_route(bfrt, LEAF3_ID, "10.0.3.1", 32, LEAF3_P1)
add_ipv4_route(bfrt, LEAF3_ID, "10.0.3.2", 32, LEAF3_P1)
add_ipv4_route(bfrt, LEAF3_ID, "10.0.3.3", 32, LEAF3_P2)
add_ipv4_route(bfrt, LEAF3_ID, "10.0.3.4", 32, LEAF3_P2)
# upstream to spine
add_ipv4_route(bfrt, LEAF3_ID, "10.0.0.0", 8, LEAF3_P4)

# Leaf 4 routes
# downstream to server 4
add_ipv4_route(bfrt, LEAF4_ID, "10.0.4.1", 32, LEAF4_P1)
add_ipv4_route(bfrt, LEAF4_ID, "10.0.4.2", 32, LEAF4_P1)
add_ipv4_route(bfrt, LEAF4_ID, "10.0.4.3", 32, LEAF4_P2)
add_ipv4_route(bfrt, LEAF4_ID, "10.0.4.4", 32, LEAF4_P2)

# upstream to spine
add_ipv4_route(bfrt, LEAF4_ID, "10.0.0.0", 8, LEAF4_P4)

# Spine routes
# to leaf 1
add_ipv4_route(bfrt, SPINE_ID, "10.0.1.0", 24, SPINE_P1)
# to leaf 2
add_ipv4_route(bfrt, SPINE_ID, "10.0.2.0", 24, SPINE_P2)
# to leaf 3
add_ipv4_route(bfrt, SPINE_ID, "10.0.3.0", 24, SPINE_P3)
# to leaf 4
add_ipv4_route(bfrt, SPINE_ID, "10.0.4.0", 24, SPINE_P4)
logger.info("Tofino Spine-Leaf Topology Control Plane Setup Completed.")
