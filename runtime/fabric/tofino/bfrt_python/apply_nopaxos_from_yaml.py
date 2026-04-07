#!/usr/bin/env python3
# apply_nopaxos_v1_from_yaml.py
#
# New organized P4 program control-plane for v1 topology.
# Programs everything EXCEPT: ports bring-up + t_l2_forward entries.
#
# Programs:
#   - pipe.Ingress.t_port_role
#   - pipe.Ingress.t_mode (default MODE_NOPAXOS_V1=2)
#   - pipe.Ingress.t_dst_mac_classify
#   - pipe.Ingress.t_leaf_uplink_v1
#   - pipe.Ingress.t_spine_forward
#   - PRE: bfrt.pre.node + bfrt.pre.mgid
#   - pipe.Ingress.t_nopaxos_phase1
#
# Run:
#   bfshell -b /tmp/opticaldcn/apply_nopaxos_v1_from_yaml.py
#
import os
import yaml

TOPO    = os.environ.get("TOPO", "/tmp/opticaldcn/system-topo.yaml")
MGID    = int(os.environ.get("MGID", "101"))
NODE_ID = int(os.environ.get("NODE_ID", "1"))
RID     = int(os.environ.get("RID", "1"))
MODE    = int(os.environ.get("MODE", "2"))  # default: MODE_NOPAXOS_V1

# P4 constants (your definition)
MODE_L2         = 0
MODE_L3         = 1
MODE_NOPAXOS_V1 = 2

K_UNKNOWN       = 0
K_LEAF_DOWNLINK = 1
K_LEAF_UPLINK   = 2
K_SPINE_PORT    = 3


def pick_p4_name() -> str:
    try:
        progs = bfrt.info(return_info=True).get("programs", [])
        if len(progs) == 1:
            return progs[0]
        if "spineleaf" in progs:
            return "spineleaf"
        return progs[0] if progs else "spineleaf"
    except Exception:
        return "spineleaf"


P4 = pick_p4_name()
p4 = getattr(bfrt, P4)


def require_tables():
    need = {
        "t_spine_forward",
        "t_port_role",
        "t_nopaxos_phase1",
        "t_mode",
        "t_leaf_uplink_v1",
        "t_dst_mac_classify",
        # t_l2_forward exists but is handled by apply_l2_from_yaml.py
    }
    got = set()
    try:
        node = p4.pipe.Ingress
        # BFRT python objects expose attributes; easiest is to probe known ones
        for name in list(need):
            getattr(node, name)
            got.add(name)
    except Exception as e:
        raise RuntimeError(f"BFRT missing required table: {e}")
    missing = need - got
    if missing:
        raise RuntimeError(f"BFRT missing tables: {sorted(missing)}")


def load_topo():
    with open(TOPO, "r") as f:
        return yaml.safe_load(f)


def get_dev_port_from_fp(fp: str) -> int:
    """
    Convert front-panel string like '2/0' into dev_port using port_hdl_info.
    This avoids iterating port.port and is stable on your SDE.
    """
    fp = fp.strip()
    conn_str, chnl_str = fp.split("/")
    conn_id = int(conn_str)
    chnl_id = int(chnl_str)

    ent = bfrt.port.port_hdl_info.get(CONN_ID=conn_id, CHNL_ID=chnl_id, print_ents=False)

    # Different SDE builds expose data slightly differently.
    data = getattr(ent, "data", None)
    if isinstance(data, dict):
        # Most common: b'$DEV_PORT'
        for k in (b"$DEV_PORT", "$DEV_PORT", "dev_port", b"dev_port"):
            if k in data:
                return int(data[k])
        # fallback: first numeric value
        for v in data.values():
            try:
                return int(v)
            except Exception:
                continue

    # Fallback: to_dict() style
    try:
        dd = ent.to_dict()
        if "$DEV_PORT" in dd:
            return int(dd["$DEV_PORT"])
        if "dev_port" in dd:
            return int(dd["dev_port"])
    except Exception:
        pass

    raise RuntimeError(f"Failed to resolve dev_port for fp={fp} via port_hdl_info")

def build_stage_indices(topo):
    leaf_ports = {}
    spine_ports = {}
    for i, leaf in enumerate(topo["switch"]["stages"]["leaves"], start=1):
        leaf_ports[i] = list(leaf["ports"])
    for i, sp in enumerate(topo["switch"]["stages"]["spines"], start=1):
        spine_ports[i] = list(sp["ports"])
    return leaf_ports, spine_ports


def find_leaf_id(leaf_ports, port_id: str) -> int:
    for lid, plist in leaf_ports.items():
        if port_id in plist:
            return lid
    return 0


def find_spine_id(spine_ports, port_id: str) -> int:
    for sid, plist in spine_ports.items():
        if port_id in plist:
            return sid
    return 0


def get_switch_port(topo, port_id: str) -> dict:
    for p in topo["switch"]["ports"]:
        if p["id"] == port_id:
            return p
    raise KeyError(port_id)


def find_switch_port_for_endpoint(topo, endpoint: str, iface: str) -> str:
    for p in topo["switch"]["ports"]:
        ct = p.get("connected_to")
        if ct and ct.get("endpoint") == endpoint and ct.get("iface") == iface:
            return p["id"]
    raise KeyError(f"no switch port connected_to {endpoint}:{iface}")


def safe_clear(table):
    try:
        table.clear()
    except Exception:
        pass


def program_t_mode():
    t = p4.pipe.Ingress.t_mode
    t.set_default_with_set_mode(mode=MODE)
    # also set explicit entry for key=0
    try:
        t.add_with_set_mode(mode_key=0, mode=MODE)
    except Exception:
        try:
            t.mod_with_set_mode(mode_key=0, mode=MODE)
        except Exception:
            pass
    print(f"[NOPAXOSv1] t_mode set: default+key0 mode={MODE} (L2=0, L3=1, NOPAXOS=2)")


def program_t_port_role(topo):
    leaf_ports, spine_ports = build_stage_indices(topo)
    t = p4.pipe.Ingress.t_port_role
    safe_clear(t)

    n = 0
    for sp in topo["switch"]["ports"]:
        role = sp.get("role", "")
        if role == "unused":
            continue

        pid = sp["id"]
        fp = sp["port"]
        dev = get_dev_port_from_fp(fp)

        if role == "server":
            leaf_id = find_leaf_id(leaf_ports, pid)
            t.add_with_set_port_role(
                ingress_port=dev,
                kind=K_LEAF_DOWNLINK,
                leaf_id=leaf_id,
                spine_id=0,
            )
            n += 1
            continue

        if role == "fabric":
            leaf_id = find_leaf_id(leaf_ports, pid)
            spine_id = find_spine_id(spine_ports, pid)

            if leaf_id != 0:
                pw = sp.get("pair_with")
                sid = find_spine_id(spine_ports, pw) if pw else 0
                t.add_with_set_port_role(
                    ingress_port=dev,
                    kind=K_LEAF_UPLINK,
                    leaf_id=leaf_id,
                    spine_id=sid,
                )
                n += 1
            elif spine_id != 0:
                pw = sp.get("pair_with")
                lid = find_leaf_id(leaf_ports, pw) if pw else 0
                t.add_with_set_port_role(
                    ingress_port=dev,
                    kind=K_SPINE_PORT,
                    leaf_id=lid,
                    spine_id=spine_id,
                )
                n += 1

    print(f"[NOPAXOSv1] t_port_role programmed {n} entries")


def program_t_dst_mac_classify(topo):
    leaf_ports, _ = build_stage_indices(topo)
    t = p4.pipe.Ingress.t_dst_mac_classify
    safe_clear(t)

    n = 0
    for ep in topo["endpoints"]:
        for nic in ep.get("network_interfaces", []):
            if nic.get("id") != "p1":
                continue
            spid = nic.get("tofino_port")
            if not spid:
                continue

            mac = nic["mac"]
            swp = get_switch_port(topo, spid)
            downlink_dev = get_dev_port_from_fp(swp["port"])
            dst_leaf = find_leaf_id(leaf_ports, spid)

            t.add_with_set_dst(dst_addr=mac, dst_leaf=dst_leaf, dst_downlink_port=downlink_dev)
            n += 1

    print(f"[NOPAXOSv1] t_dst_mac_classify programmed {n} entries")


def program_t_leaf_uplink_v1(topo):
    leaf_ports, spine_ports = build_stage_indices(topo)
    t = p4.pipe.Ingress.t_leaf_uplink_v1
    safe_clear(t)

    n = 0
    for sp in topo["switch"]["ports"]:
        if sp.get("role") != "fabric":
            continue
        pid = sp["id"]
        leaf_id = find_leaf_id(leaf_ports, pid)
        if leaf_id == 0:
            continue

        pw = sp.get("pair_with")
        sid = find_spine_id(spine_ports, pw) if pw else 0
        if sid == 0:
            continue

        uplink_dev = get_dev_port_from_fp(sp["port"])
        t.add_with_set_leaf_uplink_port(ingress_leaf=leaf_id, uplink_port=uplink_dev)
        n += 1
        print(f"[UPLINKv1] leaf={leaf_id} uplink_dev={uplink_dev} (port_id={pid} fp={sp['port']} spine={sid})")

    print(f"[NOPAXOSv1] t_leaf_uplink_v1 programmed {n} entries")


def program_t_spine_forward(topo):
    leaf_ports, spine_ports = build_stage_indices(topo)
    t = p4.pipe.Ingress.t_spine_forward
    safe_clear(t)

    n = 0
    for sp in topo["switch"]["ports"]:
        if sp.get("role") != "fabric":
            continue
        pid = sp["id"]
        spine_id = find_spine_id(spine_ports, pid)
        if spine_id == 0:
            continue

        pw = sp.get("pair_with")
        dst_leaf = find_leaf_id(leaf_ports, pw) if pw else 0
        if dst_leaf == 0:
            continue

        egress_dev = get_dev_port_from_fp(sp["port"])
        t.add_with_set_spine_egress_port(
            ingress_spine=spine_id,
            dst_leaf=dst_leaf,
            spine_port=egress_dev,
        )
        n += 1
        print(f"[SPINE_FWD] spine={spine_id} dst_leaf={dst_leaf} -> egress_dev={egress_dev} (port_id={pid} fp={sp['port']})")

    print(f"[NOPAXOSv1] t_spine_forward programmed {n} entries")


def program_pre_and_phase1(topo):
    np = topo["nopaxos"]
    udp_port = int(np["udp_port"])
    groupaddr = np["groupaddr"]

    # sequencer dev_port
    seq_ep = np["sequencer"]["endpoint"]
    seq_if = np["sequencer"]["iface"]
    seq_spid = find_switch_port_for_endpoint(topo, seq_ep, seq_if)
    seq_fp = get_switch_port(topo, seq_spid)["port"]
    seq_dev = get_dev_port_from_fp(seq_fp)

    # replica dev_ports
    replica_devs = []
    for r in np["replicas"]:
        rep_ep = r["endpoint"]
        rep_if = r["iface"]
        rep_spid = find_switch_port_for_endpoint(topo, rep_ep, rep_if)
        rep_fp = get_switch_port(topo, rep_spid)["port"]
        replica_devs.append(get_dev_port_from_fp(rep_fp))

    # PRE
    pre = bfrt.pre
    try:
        pre.mgid.delete(MGID=MGID)
    except Exception:
        pass
    try:
        pre.node.delete(MULTICAST_NODE_ID=NODE_ID)
    except Exception:
        pass

    pre.node.add(MULTICAST_NODE_ID=NODE_ID, MULTICAST_RID=RID, DEV_PORT=replica_devs, MULTICAST_LAG_ID=[])
    pre.mgid.add(
        MGID=MGID,
        MULTICAST_NODE_ID=[NODE_ID],
        MULTICAST_NODE_L1_XID_VALID=[0],
        MULTICAST_NODE_L1_XID=[0],
        MULTICAST_ECMP_ID=[],
        MULTICAST_ECMP_L1_XID_VALID=[],
        MULTICAST_ECMP_L1_XID=[],
    )

    # Phase1
    t = p4.pipe.Ingress.t_nopaxos_phase1
    safe_clear(t)

    def add_mcast(ing_dev: int):
        # Most common BFRT names
        try:
            t.add_with_nopaxos_p1_to_mcast(
            ig_intr_md_ingress_port=ing_dev,
                ipv4_valid=1,
                udp_valid=1,
                ipv4_dst_addr=groupaddr,
                udp_dst_port=udp_port,
                mgid=MGID,
                rid=RID,
            )
        except TypeError:
            print(f"[NOPAXOSv1] WARNING: t_nopaxos_phase1 may have unexpected signature; trying with isValid suffix...")
            t.mod_with_nopaxos_p1_to_mcast(
                ig_intr_md_ingress_port=ing_dev,
                ipv4_isValid=1,
                udp_isValid=1,
                ipv4_dst_addr=groupaddr,
                udp_dst_port=udp_port,
                mgid=MGID,
                rid=RID,
            )
        else:
            print(f"[NOPAXOSv1] WARNING: t_nopaxos_phase1 may have unexpected signature;")

    def add_to_seq(ing_dev: int):
        try:
            t.add_with_nopaxos_p1_to_sequencer(
                ig_intr_md_ingress_port=ing_dev,
                ipv4_valid=1,
                udp_valid=1,
                ipv4_dst_addr=groupaddr,
                udp_dst_port=udp_port,
                sequencer_port=seq_dev,
            )
            return
        except TypeError:
            print(f"[NOPAXOSv1] WARNING: t_nopaxos_phase1 may have unexpected signature; trying with isValid suffix...")
            t.mod_with_nopaxos_p1_to_sequencer(
                ig_intr_md_ingress_port=ing_dev,
                ipv4_isValid=1,
                udp_isValid=1,
                ipv4_dst_addr=groupaddr,
                udp_dst_port=udp_port,
                sequencer_port=seq_dev,
            )
        else:
            print(f"[NOPAXOSv1] WARNING: t_nopaxos_phase1 may have unexpected signature;")

    # 1) sequencer ingress -> mcast
    add_mcast(seq_dev)

    # 2) all server ports except sequencer -> sequencer
    for sp in topo["switch"]["ports"]:
        if sp.get("role") != "server":
            continue
        dev = get_dev_port_from_fp(sp["port"])
        if dev == seq_dev:
            continue
        add_to_seq(dev)

    print(f"[NOPAXOSv1] PRE MGID={MGID} NODE_ID={NODE_ID} RID={RID} replica_devs={replica_devs}")
    print(f"[NOPAXOSv1] Phase1 groupaddr={groupaddr} udp_port={udp_port} sequencer_dev={seq_dev} (fp={seq_fp})")


def main():
    require_tables()
    topo = load_topo()

    print(f"[NOPAXOSv1] program={P4}")
    print(f"[NOPAXOSv1] topo={TOPO}")
    print(f"[NOPAXOSv1] MODE={MODE} (L2=0, L3=1, NOPAXOS_V1=2)")

    program_t_port_role(topo)
    program_t_mode()
    program_t_dst_mac_classify(topo)
    program_t_leaf_uplink_v1(topo)
    program_t_spine_forward(topo)
    program_pre_and_phase1(topo)

    print("[NOPAXOSv1] Done.")


if __name__ == "__main__":
    main()