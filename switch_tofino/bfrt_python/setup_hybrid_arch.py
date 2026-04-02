#!/usr/bin/env python3
# brft_python
#
# Program Hybrid EPS/OCS:
#   - Classify optical ports (25-32) as OCS in t_set_port_kind
#   - Install an 8-slot round-robin perfect matching schedule in t_ocs_schedule for OCS ports
#
# Usage:
#   bfshell -b <this_script.py> --topo=/path/to/topology.yaml
#

import os
import yaml
import inspect

TOPO_PATH       = os.environ.get('TOPOLOGY', '/tmp/opticaldcn/system-topo.yaml')
P4_PROGRAM_NAME = os.environ.get('P4_PROGRAM_NAME', 'hybrid_arch')

NUM_SPINES = 2
NUM_LEAFS = 4
NUM_OPTICAL_PORTS = 8

#############################################################
#               BFRT Helper Functions
#############################################################
def parse_port_str(s: str):
    a, b = s.split('/')
    return int(a), int(b)

def get_dev_port(bfrt, fp_port: str):
    # Convert front-panel port string (e.g., "17/0") to device port number
    conn_id, chnl_id = parse_port_str(fp_port)
    data = bfrt.port.port_hdl_info.get(CONN_ID=conn_id, CHNL_ID=chnl_id, print_ents=False).data
    if len(data) == 0:
        raise ValueError(f"Port {fp_port} not found in BFRT database")

    return int(data[b'$DEV_PORT'])

def get_id_from_port_id(pid: str, prefix: str) -> int:
    # Extract numeric ID from port id string with given prefix
    # e.g., "leaf3_p2" -> 3 if prefix="leaf"
    if not pid.startswith(prefix):
        raise ValueError(f"Port id {pid} does not start with expected prefix {prefix}")
    rest = pid[len(prefix):]
    return int(rest.split('_', 1)[0])

def leaf_id_from_port_id(pid: str) -> int:
    return get_id_from_port_id(pid, "leaf")

def spine_id_from_port_id(pid: str) -> int:
    return get_id_from_port_id(pid, "spine")

def downleaf_id_from_port_id(pid: str) -> int:
    # For spine ports, extract the downleaf id from port id string
    # e.g., "spine1_p2" -> 2 (downleaf id) if prefix="spine"
    if not pid.startswith("spine"):
        raise ValueError(f"Port id {pid} does not start with expected prefix 'spine'")
    rest = pid[len("spine"):]
    return int(rest.split('_p', 1)[1])

def find_switch_port_for_endpoint(topo, endpoint: str, iface: str) -> str:
    # Find the front-panel port string of the switch port connected to given endpoint and interface
    for p in topo['switch']['ports']:
        ct = p.get('connected_to', {})
        if ct.get('endpoint') == endpoint and ct.get('iface') == iface:
            return p['id']
    raise ValueError(f"Cannot find switch port connected to endpoint={endpoint} iface={iface}")

def build_fabric_mappings(sw_ports, spid_to_dev):
    """
    Build:
        spine_ports: {1: {dst_leaf}}
    """

    return dst_entries, spine_ports

#############################################################
#               Programm Port Roles (EPS vs OCS)
#############################################################
def program_port_roles(bfrt, sw_ports, spid_to_dev):
    print("[Hybrid Arch] Programming port roles...")
    table = bfrt.hybrid_arch.pipe.Ingress.t_set_port_kind
    table.clear()
    
    for p in sw_ports:
        pid = p['id']
        dev_port = spid_to_dev[pid]
        
        if pid.startswith("opt_"):
            # Optical ports -> OCS
            table.add_with_set_port_kind_ocs(ingress_port=dev_port)
            print(f"  Port {pid} (dev_port {dev_port}) set as OCS")
        else:
            # All other ports -> EPS
            table.add_with_set_port_kind_eps(ingress_port=dev_port)
            print(f"  Port {pid} (dev_port {dev_port}) set as EPS")

##############################################################
#                   EPS Setup
##############################################################
def program_eps_forwarding(bfrt, endpoints, sw_ports, spid_to_dev):
    """
    Program EPS forwarding in t_eps_forward:
      key: (ingress_port, dst_mac, selected_spine)
      action: set_ucast_port(port)

    Policy:
      - leaf-downlink ingress:
          same-leaf: sel 0/1 -> dst_downlink
          cross-leaf: sel 0 -> leaf uplink p1 (to spine1), sel 1 -> leaf uplink p3 (to spine2)
      - spine ingress:
          any spine port ingress: sel 0/1 -> spine egress port towards dst_leaf
      - leaf-uplink ingress (traffic coming down from spines into leaf uplinks):
          sel 0/1 -> dst_downlink (only if dst_leaf == this leaf)
    """
    print("[Hybrid Arch] Programming EPS t_eps_forward...")

    t = bfrt.hybrid_arch.pipe.Ingress.t_eps_forward
    t.clear()

    # -----------------------------
    # Build mappings from sw_ports
    # -----------------------------
    # leaf_downlinks: leaf_id -> list of dev_ports (leafX_p2, leafX_p4)
    leaf_downlinks = {1: [], 2: [], 3: [], 4: []}

    # leaf_uplink_sp1/sp2: leaf_id -> dev_port for leafX_p1 / leafX_p3
    leaf_uplink_sp1 = {}
    leaf_uplink_sp2 = {}

    # spine ingress sets + spine egress map
    spine1_ing = set()
    spine2_ing = set()
    spine_eg_to_leaf = {1: {}, 2: {}}  # spine_id -> {dst_leaf -> dev_port}

    for p in sw_ports:
        pid = p["id"]
        role = str(p.get("role", "")).lower()
        dev = spid_to_dev[pid]

        if pid.startswith("leaf"):
            leaf_id = leaf_id_from_port_id(pid)
            if role == "server" and (pid.endswith("_p2") or pid.endswith("_p4")):
                leaf_downlinks[leaf_id].append(dev)
            elif role == "fabric":
                if pid.endswith("_p1"):
                    leaf_uplink_sp1[leaf_id] = dev
                elif pid.endswith("_p3"):
                    leaf_uplink_sp2[leaf_id] = dev

        elif pid.startswith("spine"):
            spine_id = spine_id_from_port_id(pid)  # 1 or 2
            # dst_leaf is the p-index: spine1_p3 -> 3
            dst_leaf = int(pid.split("_p")[-1])
            spine_eg_to_leaf[spine_id][dst_leaf] = dev
            if spine_id == 1:
                spine1_ing.add(dev)
            elif spine_id == 2:
                spine2_ing.add(dev)

    # sanity
    for lid in (1, 2, 3, 4):
        if lid not in leaf_uplink_sp1 or lid not in leaf_uplink_sp2:
            raise RuntimeError(f"Leaf{lid} missing uplinks (need leaf{lid}_p1 and leaf{lid}_p3)")
        if len(leaf_downlinks[lid]) == 0:
            raise RuntimeError(f"Leaf{lid} missing downlinks (need leaf{lid}_p2/p4 as server)")

    for spine_id in (1, 2):
        for dst_leaf in (1, 2, 3, 4):
            if dst_leaf not in spine_eg_to_leaf[spine_id]:
                raise RuntimeError(f"Missing spine{spine_id}_p{dst_leaf} mapping in YAML")

    # -----------------------------
    # Build dst_mac entries (EPS = p1 only)
    # -----------------------------
    # dst_entries: list[(dst_mac_bytes, dst_leaf, dst_downlink_dev)]
    dst_entries = []
    for ep in endpoints:
        for nic in ep.get("network_interfaces", []):
            if nic.get("id") != "p1":
                continue
            spid = nic.get("tofino_port")
            if not spid or not str(spid).startswith("leaf"):
                continue

            dst_leaf = leaf_id_from_port_id(spid)
            dst_down = spid_to_dev[spid]
            mac_addr = str(nic.get("mac", nic.get("mac_address"))).lower()
            dst_entries.append((mac_addr, dst_leaf, dst_down))

    if not dst_entries:
        raise RuntimeError("No EPS dst_entries built (check endpoints p1 tofino_port + mac fields)")

    # -----------------------------
    # helper: add entry
    # -----------------------------
    n = 0
    def _add(table, ing_dev, mac_addr, sel, eg_dev):
        nonlocal n
        if eg_dev is None:
            raise RuntimeError(f"eg_dev is None (ing={ing_dev}, sel={sel})")
        ent = table.entry_with_set_ucast_port(
            ingress_port=ing_dev,
            dst_addr=mac_addr,
            selected_spine=sel,
            port=eg_dev
        )
        ent.push()
        n += 1

    # ==========================================================
    # (1) leaf-downlink ingress rules
    # ==========================================================
    for leaf_id in (1, 2, 3, 4):
        up_sp1 = leaf_uplink_sp1[leaf_id]
        up_sp2 = leaf_uplink_sp2[leaf_id]
        for ing_dev in leaf_downlinks[leaf_id]:
            for mac_addr, dst_leaf, dst_down in dst_entries:
                if dst_leaf == leaf_id:
                    _add(t, ing_dev, mac_addr, 0, dst_down)
                    _add(t, ing_dev, mac_addr, 1, dst_down)
                else:
                    _add(t, ing_dev, mac_addr, 0, up_sp1)
                    _add(t, ing_dev, mac_addr, 1, up_sp2)

    # ==========================================================
    # (2) spine-ingress rules: spine ingress -> spine egress to dst_leaf
    # ==========================================================
    for sp_ing in spine1_ing:
        for mac_addr, dst_leaf, _dst_down in dst_entries:
            eg = spine_eg_to_leaf[1][dst_leaf]
            _add(t, sp_ing, mac_addr, 0, eg)
            _add(t, sp_ing, mac_addr, 1, eg)

    for sp_ing in spine2_ing:
        for mac_addr, dst_leaf, _dst_down in dst_entries:
            eg = spine_eg_to_leaf[2][dst_leaf]
            _add(t, sp_ing, mac_addr, 0, eg)
            _add(t, sp_ing, mac_addr, 1, eg)

    # ==========================================================
    # (3) leaf-uplink ingress rules: leaf uplink ingress -> leaf downlink (only for dst_leaf == leaf)
    # ==========================================================
    for leaf_id in (1, 2, 3, 4):
        for uplink_ing in (leaf_uplink_sp1[leaf_id], leaf_uplink_sp2[leaf_id]):
            for mac_addr, dst_leaf, dst_down in dst_entries:
                if dst_leaf == leaf_id:
                    _add(t, uplink_ing, mac_addr, 0, dst_down)
                    _add(t, uplink_ing, mac_addr, 1, dst_down)

    print(f"[Hybrid Arch] t_eps_forward programmed {n} entries")

###############################################################
#                       OCS Setup
###############################################################
def program_ocs_scheduling(bfrt, sw_ports, spid_to_dev, n_slots=8):
    print("[Hybrid Arch] Programming OCS tables...")
    t = bfrt.hybrid_arch.pipe.Ingress.t_ocs_schedule
    t.clear()
    
    # Build optical dev_port list in stable order opt_p1, opt_p2, ..., opt_p8
    opt_ids = [f"opt_p{i}" for i in range(1, NUM_OPTICAL_PORTS + 1)]
    for opt_id in opt_ids:
        if opt_id not in spid_to_dev:
            raise RuntimeError(f"Optical port {opt_id} not found in topology ports")

    ocs_ports = [spid_to_dev[opt_id] for opt_id in opt_ids]
    print(f"[Hybrid Arch] Found optical ports with dev_port: {ocs_ports}")
    
    n = 0
    for slot in range(n_slots):
        for i in range(NUM_OPTICAL_PORTS):
            ing_dev = ocs_ports[i]
            eg_dev = ocs_ports[(i + slot + 1) % NUM_OPTICAL_PORTS]  # Rotate for perfect matching
            ent = t.entry_with_set_ucast_port(
                ingress_port=ing_dev,
                slot_id=slot,
                port=eg_dev)
            ent.push()
            n += 1

    print(f"[Hybrid Arch] OCS tables programmed {n} entries")

#############################################################
#                   Main Function
#############################################################

def main():
    print("[Hybrid Arch] Starting setup for Hybrid EPS/OCS architecture")
    
    topo_path = os.getenv('TOPOLOGY', TOPO_PATH)
    prog = os.getenv('P4_PROGRAM_NAME', P4_PROGRAM_NAME)
    print(f"[Hybrid Arch] Loading topology from: {topo_path}")
    print(f"[Hybrid Arch] Using P4 program: {prog}")

    # ----------------------------------------------
    # 1. Load topology and extract port information
    # ----------------------------------------------
    topo = yaml.safe_load(open(topo_path))
    sw_ports = topo['switch']['ports']
    
    # Map switch port-id -> front-panel string -> dev_port
    spid_to_fp  = {p["id"]: p["port"] for p in sw_ports}
    spid_to_dev = {pid: get_dev_port(bfrt, fp) for pid, fp in spid_to_fp.items()}
    endpoints = topo.get('endpoints', [])

    # Resolve program Ingress
    prog_root = getattr(bfrt, prog, None)
    if prog_root is None:
        raise ValueError(f"Program {prog} not found in BFRT database. Available programs: {bfrt.keys()}")

    ingress = prog_root.pipe.Ingress
    
    # ----------------------------------------------
    # 2. Program port roles (EPS vs OCS)
    # ----------------------------------------------
    print("[Hybrid Arch] Programming port roles...")
    program_port_roles(bfrt, sw_ports, spid_to_dev)
    
    # ----------------------------------------------
    # 3. Program EPS
    # ----------------------------------------------
    program_eps_forwarding(bfrt, endpoints, sw_ports, spid_to_dev)

    # ----------------------------------------------
    # 4. Program OCS
    # ----------------------------------------------
    program_ocs_scheduling(bfrt, sw_ports, spid_to_dev)
    print("[Hybrid Arch] Setup complete for Hybrid EPS/OCS architecture")

if __name__ == "__main__":
    main()
