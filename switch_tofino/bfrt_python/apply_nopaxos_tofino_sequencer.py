#!/usr/bin/env python3
# bfrt_python

import os
import socket
import yaml
import inspect
from ipaddress import ip_address

TOPO_PATH = os.environ.get("TOPO", "/tmp/opticaldcn/system-topo.yaml")
P4_PROG = os.environ.get("P4_PROG", "nopaxos_tofino")
MGID = int(os.environ.get("MGID", "101"))
NODE_ID = int(os.environ.get("NODE_ID", "1"))
RID = int(os.environ.get("RID", "1"))

# Constants (must match with P4 program)
MODE_CLOS = 1
K_DOWNLINK = 1
K_LEAF_UPLINK = 2
K_SPINE_PORT = 3

def parse_port_str(s: str):
    a, b = s.split("/")
    return int(a), int(b)

def get_dev_port(bfrt, front_panel: str) -> int:
    conn_id, chnl_id = parse_port_str(front_panel)
    data = bfrt.port.port_hdl_info.get(CONN_ID=conn_id, CHNL_ID=chnl_id, print_ents=False).data
    return int(data[b"$DEV_PORT"])

def get_id_from_port_id(pid: str, prefix: str) -> int:
    # "leaf3_p2" -> 3 if prefix="leaf"
    if not pid.startswith(prefix):
        raise ValueError(f"Not a {prefix} port id: {pid}")
    rest = pid[len(prefix):]
    return int(rest.split("_", 1)[0])

def leaf_id_from_port_id(pid: str) -> int:
    return get_id_from_port_id(pid, "leaf")

def spine_id_from_port_id(pid: str) -> int:
    return get_id_from_port_id(pid, "spine")

def find_switch_port_for_endpoint(topo, endpoint: str, iface: str) -> str:
    for p in topo["switch"]["ports"]:
        ct = p.get("connected_to", {})
        if ct.get("endpoint") == endpoint and ct.get("iface") == iface:
            return p["id"]
    raise ValueError(f"Cannot find switch port connected to endpoint={endpoint} iface={iface}")

# -----------------------
# Programming functions
# -----------------------
def program_t_port_role(bfrt, sw_ports, spid_to_dev):
    print("[NOPAXOS_TOFINO] Programming t_port_role...")
    table = bfrt.nopaxos_tofino.pipe.Ingress.t_port_role
    table.clear()

    for p in sw_ports:
        pid = p["id"]
        role = str(p.get("role", "")).lower()
        dev_port = spid_to_dev[pid]
        
        kind = 0
        leaf_id = 0
        spine_id = 0
        
        if pid.startswith("leaf"):
            leaf_id = leaf_id_from_port_id(pid)
            if role == "server":
                kind = K_DOWNLINK   # down to servers
            elif role == "fabric":
                kind = K_LEAF_UPLINK    # up to spines
            else:
                continue
        elif pid.startswith("spine"):
            kind = K_SPINE_PORT
            spine_id = spine_id_from_port_id(pid)
        else:
            continue
        
        ent = table.entry_with_set_port_role(
            ingress_port=dev_port,
            kind=kind,
            leaf_id=leaf_id,
            spine_id=spine_id
        )
        ent.push()

    print(f"[NOPAXOS_TOFINO] t_port_role programmed {len(spid_to_dev)} entries")

def program_dst_mac_classify(bfrt, endpoints, spid_to_dev):
    print("[NOPAXOS_TOFINO] Programming t_dst_mac_classify...")
    t_dst = bfrt.nopaxos_tofino.pipe.Ingress.t_dst_mac_classify
    t_dst.clear()

    for ep in endpoints:
        for nic in ep["network_interfaces"]:
            spid = nic.get("tofino_port")
            if spid is None:
                continue
            dst_leaf = leaf_id_from_port_id(spid)
            dst_downlink_port = spid_to_dev.get(spid)
            mac = str(nic.get("mac", nic.get("mac_address"))).lower()

            ent = t_dst.entry_with_set_dst(
                dst_addr=mac,
                dst_leaf=dst_leaf,
                dst_downlink_port=dst_downlink_port
            )
            ent.push()

    print(f"[NOPAXOS_TOFINO] t_dst_mac_classify programmed entries for {len(endpoints)} endpoints")

def program_leaf_uplink(bfrt, sw_ports, spid_to_dev):
    print("[NOPAXOS_TOFINO] Programming t_leaf_uplink...")
    t = bfrt.nopaxos_tofino.pipe.Ingress.t_leaf_uplink_v1
    t.clear()

    for p in sw_ports:
        pid = p["id"]
        if not pid.startswith("leaf"):
            continue
        leaf_id = leaf_id_from_port_id(pid)
        # Convention: role=fabric means this leaf port goes to spine, so it's an uplink
        if str(p.get("role", "")).lower() != "fabric":
            continue
        dev_port = spid_to_dev[pid]

        ent = t.entry_with_set_leaf_uplink_port(
            ingress_leaf=leaf_id,
            uplink_port=dev_port
        )
        ent.push()

    print(f"[NOPAXOS_TOFINO] t_leaf_uplink programmed for {len(sw_ports)} switch ports")

def program_spine_forwarding(bfrt, sw_ports, spid_to_dev):
    print("[NOPAXOS_TOFINO] Programming t_spine_forward...")
    t = bfrt.nopaxos_tofino.pipe.Ingress.t_spine_forward
    t.clear()

    for spine_id in [1, 2]:  # example: support spine1 and spine2
        for dst_leaf in [1, 2, 3, 4]:  # example: support dst_leaf 1-3
            spine = f"spine{spine_id}_p{dst_leaf}"  # convention: spineX_pY goes to dst_leaf Y

            ent = t.entry_with_set_spine_egress_port(
                ingress_spine=spine_id,
                dst_leaf=dst_leaf,
                spine_port=spid_to_dev[spine]
            )
            ent.push()

    print(f"[NOPAXOS_TOFINO] t_spine_forward programmed for {len(sw_ports)} switch ports")

def program_gid_to_bitmap(bfrt):
    print("[NOPAXOS_TOFINO] Programming t_gid_to_bitmap...")
    t = bfrt.nopaxos_tofino.pipe.Ingress.t_gid_to_bitmap
    t.clear()
    
    for safe_gid in range(16):  # example: support safe_gid 0-15
        bitmap = 1 << safe_gid  # example: bitmap with only the bit corresponding to safe_gid set
        bitmap_net = socket.htons(bitmap)  # convert to network byte order if needed
        safe_gid_net = socket.htons(safe_gid + 1)  # convert to network byte order if needed
        ent = t.entry_with_set_gid_to_bitmap(
            group0_id=safe_gid_net,
            bitmap=bitmap_net
        )
        ent.push()
    print(f"[NOPAXOS_TOFINO] t_gid_to_bitmap programmed for safe_gid 0-15")

def program_nopaxos_tofino_sequencer(bfrt, topo, spid_to_dev):
    print("[NOPAXOS_TOFINO] Programming t_nopaxos_tofino_sequencer...")
    t = bfrt.nopaxos_tofino.pipe.Ingress.t_nopaxos_tofino_sequencer
    t.clear()

    np = topo["nopaxos"]
    udp_port = int(np["udp_port"])
    group_addr = np["group_addr"]
    
    # client endpoint
    client_ep = np["client"]["endpoint"]
    client_iface = np["client"]["iface"]
    client_spid = find_switch_port_for_endpoint(topo, client_ep, client_iface)
    client_dev_port = spid_to_dev[client_spid]
    
    replica_devs = []
    for replica in np["replicas"]:
        replica_ep = replica["endpoint"]
        replica_iface = replica["iface"]
        replica_spid = find_switch_port_for_endpoint(topo, replica_ep, replica_iface)
        replica_dev_port = spid_to_dev[replica_spid]
        replica_devs.append(replica_dev_port)
    
    # Packet Replication Engine (PRE) will replicate packets to all replica_devs based on the MGID, so we program the sequencer table to forward matching packets to the MGID
    pre = bfrt.pre
    
    try:
        pre.node.delete(MULTICAST_NODE_ID=NODE_ID)
    except:
        pass
    pre.node.add(MULTICAST_NODE_ID=NODE_ID,
                MULTICAST_RID=RID,
                DEV_PORT=replica_devs,
                MULTICAST_LAG_ID=[])

    try:
        pre.mgid.delete(MGID=MGID)
    except:
        pass
    pre.mgid.add(MGID=MGID,
                MULTICAST_NODE_ID=[NODE_ID],
                MULTICAST_NODE_L1_XID_VALID=[0],
                MULTICAST_NODE_L1_XID=[0],
                MULTICAST_ECMP_ID=[],
                MULTICAST_ECMP_L1_XID_VALID=[],
                MULTICAST_ECMP_L1_XID=[])

    ent = t.entry_with_nopaxos_tofino_sequencer(
        ig_intr_md_ingress_port=client_dev_port,
        ipv4_valid=1,
        udp_valid=1,
        ipv4_dst_addr=group_addr,
        udp_dst_port=udp_port,
        nopaxos_oum_valid=1,
        mcast_grp=MGID,
        rid=RID
    )
    ent.push()

    print(f"[NOPAXOS_TOFINO] t_nopaxos_tofino_sequencer programmed for client_dev_port={client_dev_port}, group_addr={group_addr}, udp_port={udp_port}, MGID={MGID}")

# -----------------------
# Main
# -----------------------

def main():
    print(f"[NOPAXOS_TOFINO] Starting apply_nopaxos_tofino_sequencer.py")

    # BFRT root is injected in bfshell as `bfrt`
    global bfrt
    program = "nopaxos_tofino"
    topo_path = os.getenv("TOPO", "/tmp/opticaldcn/system-topo.yaml")

    print(f"[NOPAXOS_TOFINO] program={program}")
    print(f"[NOPAXOS_TOFINO] topo={topo_path}")

    topo = yaml.safe_load(open(topo_path, "r"))
    sw_ports = topo["switch"]["ports"]
    endpoints = topo["endpoints"]
    
    # port_id -> front-panel and dev_port
    spid_to_fp = {p["id"]: p["port"] for p in sw_ports}
    spid_to_dev = {pid: get_dev_port(bfrt, fp) for pid, fp in spid_to_fp.items()}

    ingress = getattr(getattr(bfrt, program).pipe, "Ingress")

    # table for classifying port roles (downlink/uplink/spine)
    print("[NOPAXOS_TOFINO] Programming t_port_role...")
    program_t_port_role(bfrt, sw_ports, spid_to_dev)

    # table for classifying packets by dst MAC (to get dst_leaf and dst_downlink_port)
    print("[NOPAXOS_TOFINO] Programming forwarding tables...")
    program_dst_mac_classify(bfrt, endpoints, spid_to_dev)
    
    # table for selecting uplink port based on leaf_id
    t_leaf_uplink = ingress.t_leaf_uplink_v1
    program_leaf_uplink(bfrt, sw_ports, spid_to_dev)

    # table for spine forwarding based on (spine_id, dst_leaf)
    t_spine_fwd = ingress.t_spine_forward
    program_spine_forwarding(bfrt, sw_ports, spid_to_dev)

    # table for mapping safe_gid to bitmap (for scalable multicast group management)
    #t_gid_to_bitmap = ingress.t_gid_to_bitmap
    #program_gid_to_bitmap(bfrt)

    # table for dispatching packets to sequencer based on (ingress_port, ipv4_valid, udp_valid, dst_addr, dst_port, oum_valid)
    t_nopaxos_tofino_sequencer = ingress.t_nopaxos_tofino_sequencer
    program_nopaxos_tofino_sequencer(bfrt, topo, spid_to_dev)

    print("[NOPAXOS_TOFINO] Done.")

if __name__ == "__main__":
    main()
