#!/usr/bin/env python3
import os
import yaml

TOPO_PATH = os.environ.get("TOPO_PATH", "/tmp/opticaldcn/system-topo.yaml")
P4_PROG = os.environ.get("P4_PROG", "spineleaf")


def parse_port_str(s: str):
    a, b = s.split("/")
    return int(a), int(b)


def get_dev_port(bfrt, front_panel: str) -> int:
    conn_id, chnl_id = parse_port_str(front_panel)
    data = bfrt.port.port_hdl_info.get(CONN_ID=conn_id, CHNL_ID=chnl_id, print_ents=False).data
    return int(data[b"$DEV_PORT"])


def main():
    assert "bfrt" in globals(), "Run inside bfshell bfrt_python"
    bfrt = globals()["bfrt"]

    topo = yaml.safe_load(open(TOPO_PATH, "r"))
    sw_ports = topo["switch"]["ports"]
    endpoints = topo["endpoints"]

    spid_to_fp = {p["id"]: p["port"] for p in sw_ports}

    ingress = getattr(getattr(bfrt, P4_PROG).pipe, "Ingress")
    t_l2 = ingress.t_l2_forward

    try:
        t_l2.clear()
    except Exception as e:
        print(f"[L2][Warn] failed to clear t_l2_forward: {e}")

    entries = []
    for ep in endpoints:
        for nic in ep["network_interfaces"]:
            spid = nic.get("tofino_port")
            if spid is None:
                continue
            mac = str(nic.get("mac", nic.get("mac_address"))).lower()
            fp = spid_to_fp[spid]
            dev_port = get_dev_port(bfrt, fp)
            entries.append((mac, dev_port, spid, fp))

    print(f"[L2] Programming {len(entries)} entries into pipe.Ingress.t_l2_forward ...")
    for mac, dev_port, spid, fp in entries:
        try:
            ent = t_l2.entry_with_l2_forward(dst_addr=mac, port=dev_port)
            ent.push()
        except Exception as e:
            print(f"[L2] Failed to add entry for dst={mac} -> dev_port={dev_port} (switch_port_id={spid}, front_panel={fp}): {e}")
        else:
            print(f"[L2] dst={mac} -> dev_port={dev_port} (switch_port_id={spid}, front_panel={fp})")

    print("[L2] Done.")

if __name__ == "__main__":
    main()
