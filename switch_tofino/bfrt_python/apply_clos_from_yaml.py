#!/usr/bin/env python3
import os
import yaml

TOPO_PATH = os.environ.get("TOPO_PATH", "/tmp/opticaldcn/system-topo.yaml")
P4_PROG = os.environ.get("P4_PROG", "spineleaf")

# Constants for CLOS mode, must be consistent with the P4 program
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


def leaf_id_from_port_id(pid: str) -> int:
    # "leaf3_p2" -> 3
    if not pid.startswith("leaf"):
        raise ValueError(f"Not a leaf port id: {pid}")
    rest = pid[len("leaf"):]
    return int(rest.split("_", 1)[0])


def spine_id_from_port_id(pid: str) -> int:
    # "spine2_p4" -> 2
    if not pid.startswith("spine"):
        raise ValueError(f"Not a spine port id: {pid}")
    rest = pid[len("spine"):]
    return int(rest.split("_", 1)[0])


def main():
    print("Starting apply_clos_from_yaml.py with topo=%s p4_prog=%s", TOPO_PATH, P4_PROG)
    assert "bfrt" in globals(), "Run inside bfshell bfrt_python"
    bfrt = globals()["bfrt"]

    topo = yaml.safe_load(open(TOPO_PATH, "r"))
    sw_ports = topo["switch"]["ports"]
    endpoints = topo["endpoints"]

    # port_id -> front-panel and dev_port
    spid_to_fp = {p["id"]: p["port"] for p in sw_ports}
    spid_to_dev = {pid: get_dev_port(bfrt, fp) for pid, fp in spid_to_fp.items()}

    ingress = getattr(getattr(bfrt, P4_PROG).pipe, "Ingress")

    t_mode = ingress.t_mode
    t_port_role = ingress.t_port_role
    t_dst = ingress.t_dst_mac_classify
    t_uplink = ingress.t_leaf_uplink_select
    t_spine_fwd = ingress.t_spine_forward

    # 0) Set mode to CLOS
    print("[CLOS] Setting t_mode to MODE_CLOS...")
    try:
        t_mode.clear()
    except Exception:
        pass

    ent = t_mode.entry_with_set_mode(mode_key=0, mode=MODE_CLOS)
    ent.push()
    print("[CLOS] t_mode done.")

    # 1) t_port_role: dev_port -> (kind, leaf_id, spine_id)
    print("[CLOS] Programming t_port_role...")
    try:
        t_port_role.clear()
    except Exception:
        pass

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
                kind = K_DOWNLINK
            elif role == "fabric":
                kind = K_LEAF_UPLINK
            else:
                continue
        elif pid.startswith("spine"):
            kind = K_SPINE_PORT
            spine_id = spine_id_from_port_id(pid)
        else:
            continue

        ent = t_port_role.entry_with_set_port_role(
            ingress_port=dev_port,
            kind=kind,
            leaf_id=leaf_id,
            spine_id=spine_id
        )
        ent.push()

    print("[CLOS] t_port_role done.")

    # 2) t_dst_mac_classify: dst MAC -> (dst_leaf, dst_downlink_port)
    print("[CLOS] Programming t_dst_mac_classify...")
    try:
        t_dst.clear()
    except Exception:
        pass

    for ep in endpoints:
        for nic in ep["network_interfaces"]:
            spid = nic.get("tofino_port")
            if spid is None:
                continue
            dst_leaf = leaf_id_from_port_id(spid)
            dst_downlink_port = spid_to_dev[spid]
            mac = str(nic.get("mac", nic.get("mac_address"))).lower()

            ent = t_dst.entry_with_set_dst(
                dst_addr=mac,
                dst_leaf=dst_leaf,
                dst_downlink_port=dst_downlink_port
            )
            ent.push()

    print("[CLOS] t_dst_mac_classify done.")

    # 3) t_leaf_uplink_select:
    # (ingress_leaf, selected_spine) -> uplink_port
    # By topology rule:
    # leaf L uplink to spine1 = leafL_p1, to spine2 = leafL_p3
    print("[CLOS] Programming t_leaf_uplink_select...")
    try:
        t_uplink.clear()
    except Exception:
        pass

    for leaf in [1, 2, 3, 4]:
        uplink_sp1 = f"leaf{leaf}_p1"
        uplink_sp2 = f"leaf{leaf}_p3"

        ent1 = t_uplink.entry_with_set_leaf_uplink_port(
            ingress_leaf=leaf,
            selected_spine=1,
            uplink_port=spid_to_dev[uplink_sp1]
        )
        ent1.push()

        ent2 = t_uplink.entry_with_set_leaf_uplink_port(
            ingress_leaf=leaf,
            selected_spine=2,
            uplink_port=spid_to_dev[uplink_sp2]
        )
        ent2.push()

    print("[CLOS] t_leaf_uplink_select done.")

    # 4) t_spine_forward:
    # (ig_md.ingress_spine, ig_md.dst_leaf) -> spine_port
    # spine1 ports: spine1_p1..p4 => leaf1..leaf4
    # spine2 ports: spine2_p1..p4 => leaf1..leaf4
    print("[CLOS] Programming t_spine_forward...")
    try:
        t_spine_fwd.clear()
    except Exception:
        pass

    for dst_leaf in [1, 2, 3, 4]:
        s1 = f"spine1_p{dst_leaf}"
        s2 = f"spine2_p{dst_leaf}"

        ent1 = t_spine_fwd.entry_with_set_spine_egress_port(
            ingress_spine=1,
            dst_leaf=dst_leaf,
            spine_port=spid_to_dev[s1]
        )
        ent1.push()

        ent2 = t_spine_fwd.entry_with_set_spine_egress_port(
            ingress_spine=2,
            dst_leaf=dst_leaf,
            spine_port=spid_to_dev[s2]
        )
        ent2.push()

    print("[CLOS] t_spine_forward done.")
    print("[CLOS] All MODE_CLOS tables programmed successfully.")


if __name__ == "__main__":
    main()