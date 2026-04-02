#!/usr/bin/env python3
import os
import yaml
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TOPO_PATH = os.environ.get("TOPO_PATH", "/tmp/opticaldcn/system-topo.yaml")
P4_PROG = os.environ.get("P4_PROG", "spineleaf")  # unused here, but keep interface consistent


def parse_port_str(s: str):
    a, b = s.split("/")
    return int(a), int(b)


def get_dev_port(bfrt, front_panel: str) -> int:
    conn_id, chnl_id = parse_port_str(front_panel)
    data = bfrt.port.port_hdl_info.get(CONN_ID=conn_id, CHNL_ID=chnl_id, print_ents=False).data
    return int(data[b"$DEV_PORT"])


def normalize_speed(speed):
    s = str(speed).upper()
    if "100" in s:
        return "BF_SPEED_100G"
    if "50" in s:
        return "BF_SPEED_50G"
    if "40" in s:
        return "BF_SPEED_40G"
    if "25" in s:
        return "BF_SPEED_25G"
    if "10" in s:
        return "BF_SPEED_10G"
    return s

def normalize_fec(fec):
    f = str(fec).upper()
    if f in ["RS", "RS-FEC", "RSFEC"]:
        return "BF_FEC_TYP_RS"
    if f in ["FC", "FC-FEC", "FCFEC"]:
        return "BF_FEC_TYP_FEC"
    if f in ["NONE", "NO", "OFF"]:
        return "BF_FEC_TYP_NONE"
    return "BF_FEC_TYP_NONE"  # default to NONE if unrecognized or not specified

def port_add_enable(port_tbl, dev_port: int, speed: str, fec: str):
    """
    BFRT port table API varies across SDE releases.
    """
    logger.info(f"Adding/enabling port dev_port={dev_port} speed={speed} fec={fec}")
    try:
        port_tbl.add(
            DEV_PORT=dev_port,
            SPEED=speed,
            FEC=fec,
            AUTO_NEGOTIATION="PM_AN_FORCE_DISABLE",
            PORT_ENABLE=True,
        )
        return
    except Exception:
        pass

    try:
        port_tbl.mod(
            DEV_PORT=dev_port,
            SPEED=speed,
            FEC=fec,
            AUTO_NEGOTIATION="PM_AN_FORCE_DISABLE",
            PORT_ENABLE=True,
        )
        return
    except Exception:
        pass

    raise RuntimeError(f"Failed to add/enable port with dev_port={dev_port} speed={speed} fec={fec}. Check port table schema and adjust code as needed.")

def main():
    assert "bfrt" in globals(), "Run inside bfshell bfrt_python"
    bfrt = globals()["bfrt"]

    topo = yaml.safe_load(open(TOPO_PATH, "r"))
    sw_ports = topo["switch"]["ports"]

    port_tbl = bfrt.port.port

    logger.info(f"[PORTS] Bringing up ports from YAML: total={len(sw_ports)}")
    failed = []

    for p in sw_ports:
        pid = p["id"]
        fp = p["port"]
        role = str(p.get("role", "")).lower()

        spec = p.get("spec", {}) or {}
        speed = normalize_speed(spec.get("speed", "100G"))
        fec = normalize_fec(spec.get("fec", None))

        dev_port = get_dev_port(bfrt, fp)

        try:
            port_add_enable(port_tbl, dev_port=dev_port, speed=speed, fec=fec)
        except RuntimeError as e:
            failed.append((pid, fp, dev_port, speed, fec, role))
        else:
            logger.info(f"[PORTS] enabled {pid} fp={fp} dev_port={dev_port} speed={speed} fec={fec} role={role}")

    if failed:
        logger.error("[PORTS] Failed to enable some ports. Likely port table schema mismatch.")
        logger.error("[PORTS] Run `bfrt.port.port.info()` to inspect expected fields/methods.")
        try:
            bfrt.port.port.info()
        except Exception as e:
            logger.error("[PORTS] info() failed: {}", e)

        for (pid, fp, dp, spd, fec, role) in failed:
            logger.error(f"[PORTS] FAIL id={pid} fp={fp} dev_port={dp} speed={spd} fec={fec} role={role}")

        raise SystemExit(2)

    logger.info("[PORTS] Done.")

if __name__ == "__main__":
    main()
