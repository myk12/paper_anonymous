#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import os
import socket
import subprocess
from pathlib import Path

import yaml
from loguru import logger

from utils.logging import setup_logger
from src.topo.model import load_topology
from src.topo.validate import validate_topology


def run(cmd: list[str], sudo: bool = False, check: bool = True) -> subprocess.CompletedProcess[str]:
    real = (["sudo"] + cmd) if sudo and os.geteuid() != 0 else cmd
    cp = subprocess.run(real, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if check and cp.returncode != 0:
        logger.error("CMD failed rc={} cmd={}", cp.returncode, " ".join(real))
        if cp.stdout:
            logger.error("stdout: {}", cp.stdout.strip()[-1200:])
        if cp.stderr:
            logger.error("stderr: {}", cp.stderr.strip()[-1200:])
        raise RuntimeError(f"command failed: {' '.join(real)}")
    return cp


def run_ns(ns: str, cmd: list[str], sudo: bool = False, check: bool = True) -> subprocess.CompletedProcess[str]:
    return run(["ip", "netns", "exec", ns] + cmd, sudo=sudo, check=check)


def netns_exists(ns: str) -> bool:
    cp = run(["ip", "netns", "list"], check=True)
    names = {line.split()[0] for line in cp.stdout.splitlines() if line.strip()}
    return ns in names


def ensure_netns(ns: str) -> None:
    if netns_exists(ns):
        logger.info("netns exists: {}", ns)
        return
    run(["ip", "netns", "add", ns], sudo=True, check=True)
    logger.info("netns created: {}", ns)


def ensure_lo_up(ns: str) -> None:
    run_ns(ns, ["ip", "link", "set", "dev", "lo", "up"], sudo=True, check=True)
    logger.info("lo up in ns={}", ns)


def iface_in_root(ifname: str) -> bool:
    return run(["ip", "link", "show", "dev", ifname], check=False).returncode == 0


def iface_in_ns(ns: str, ifname: str) -> bool:
    return run_ns(ns, ["ip", "link", "show", "dev", ifname], check=False).returncode == 0


def move_iface_to_ns(ifname: str, ns: str) -> None:
    if iface_in_ns(ns, ifname):
        logger.info("iface already in ns: {}:{}", ns, ifname)
        return
    if not iface_in_root(ifname):
        raise RuntimeError(f"iface {ifname} not in root; check old namespaces or driver state")
    run(["ip", "link", "set", "dev", ifname, "netns", ns], sudo=True, check=True)
    logger.info("moved iface {} -> ns {}", ifname, ns)


def configure_ip_up(ns: str, ifname: str, ip_cidr: str) -> None:
    run_ns(ns, ["ip", "addr", "flush", "dev", ifname], sudo=True, check=False)
    run_ns(ns, ["ip", "addr", "add", ip_cidr, "dev", ifname], sudo=True, check=True)
    run_ns(ns, ["ip", "link", "set", "dev", ifname, "up"], sudo=True, check=True)
    logger.info("configured {} in ns={} ip={}", ifname, ns, ip_cidr)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--topo", default="configs/system-topo-v3.yaml")
    ap.add_argument("--logdir", default="logs")
    ap.add_argument("--no-validate", action="store_true")
    args = ap.parse_args()

    # -------------------------------------------------
    # 1. Load topology and validate
    # -------------------------------------------------
    topo_path = Path(args.topo).resolve()
    run_id = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = Path(args.logdir) / run_id
    setup_logger(out_dir)  # your loguru setup
    logger.info("run_id={} topo={}", run_id, topo_path)

    doc = yaml.safe_load(topo_path.read_text())
    topo = load_topology(doc)
    if not args.no_validate:
        validate_topology(topo)

    host = socket.gethostname()
    logger.info("hostname={}", host)
    local_ep_ids = topo.hosts.get(host, [])
    if not local_ep_ids:
        logger.warning("No endpoints mapped to this host in topo: {}", host)
        return

    # Select endpoints for this host
    local_ep_ids = topo.hosts.get(host, [])
    if not local_ep_ids:
        logger.warning("No endpoints mapped to this host in topo: {}", host)
        return

    # Save inventory
    (out_dir / "inventory.json").write_text(topo_path.read_text())

    logger.info("Endpoints on this host: {}", local_ep_ids)
    # -------------------------------------------------
    # Apply netns + iface config
    # -------------------------------------------------
    for eid in local_ep_ids.endpoints:
        ep = topo.endpoints[eid]
        for nic in ep.network_interfaces:
            # Skip mgmt/non-switch interfaces (tofino_port null) if you want
            if nic.tofino_port is None:
                logger.info("skip mgmt iface {}:{} (tofino_port=null)", eid, nic.id)
                continue
            logger.info("setup {}:{} ifname={} netns={} ip={}", eid, nic.id, nic.ifname, nic.netns, nic.ip)
            ensure_netns(nic.netns)
            ensure_lo_up(nic.netns)
            move_iface_to_ns(nic.ifname, nic.netns)
            configure_ip_up(nic.netns, nic.ifname, nic.ip)
    
    # -------------------------------------------------
    # Install static ARP entries for all switch-facing interfaces in each namespace
    # -------------------------------------------------
    # Build global ip->mac map for switch-facing interfaces only
    ip2mac = {}
    for ep in topo.endpoints.values():
        for nic in ep.network_interfaces:
            if nic.tofino_port is None:
                continue
            ip2mac[nic.ip.split("/")[0]] = nic.mac
    logger.info("global switch-facing interfaces: {}", len(ip2mac))

    # Install into each local namespace
    for eid in local_ep_ids.endpoints:
        ep = topo.endpoints[eid]
        for nic in ep.network_interfaces:
            if nic.tofino_port is None:
                continue
            self_ip = nic.ip.split("/")[0]
            logger.info("install ARP in ns={} dev={} (self_ip={})", nic.netns, nic.ifname, self_ip)
            for ip, mac in ip2mac.items():
                if ip == self_ip:
                    continue
                run_ns(
                    nic.netns,
                    ["ip", "neigh", "replace", ip, "lladdr", mac, "dev", nic.ifname, "nud", "permanent"],
                    sudo=True,
                    check=False,
                )
    logger.info("static ARP install done: {}", out_dir)


if __name__ == "__main__":
    main()
