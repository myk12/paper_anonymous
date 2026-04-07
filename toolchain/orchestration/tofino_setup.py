#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import subprocess
import sys
from pathlib import Path

import yaml
from loguru import logger

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.append(str(REPO_ROOT))

from utils.logging import setup_logger

MODE_TO_REMOTE = {
    # keys are modes passed to --mode
    "ports" : "apply_ports_from_yaml.py",
    "l2"    : "apply_l2_from_yaml.py",
    "clos"  : "apply_clos_from_yaml.py",
    "nopaxos_host": "apply_nopaxos_from_yaml.py",
    "nopaxos_tofino": "apply_nopaxos_tofino_sequencer.py",
    "hybrid" : "setup_hybrid_arch.py",
}

TOFINO_DIR = "fabric/tofino"

def sh(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
    cp = subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if check and cp.returncode != 0:
        raise RuntimeError(
            f"cmd failed rc={cp.returncode}: {' '.join(cmd)}\nstdout:\n{cp.stdout}\nstderr:\n{cp.stderr}"
        )
    return cp


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--topo", default="configs/system-topo-v3.yaml")
    ap.add_argument("--mode", default="ports", help="comma-separated: ports,l2,clos,nopaxos_host,nopaxos_tofino")  # modes to run in order
    ap.add_argument("--logdir", default="logs")
    ap.add_argument("--remote-dir", default="/tmp/opticaldcn")
    ap.add_argument("--p4-prog", default="spineleaf")

    ap.add_argument("--switch-host", default="", help="override YAML switch.mgmt.host")
    ap.add_argument("--switch-user", default="", help="override YAML switch.mgmt.username")
    ap.add_argument("--bfshell", default="", help="override YAML switch.bfshell")
    ap.add_argument("--sde-env", default="", help="override YAML switch.sde_env")

    args = ap.parse_args()

    topo_path = Path(args.topo).resolve()
    run_id = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = Path(args.logdir) / run_id
    setup_logger(out_dir)
    logger.info("run_id={} topo={} mode={}", run_id, topo_path, args.mode)

    topo = yaml.safe_load(topo_path.read_text())
    mgmt = topo["switch"]["mgmt"]
    host = args.switch_host or mgmt["host"]
    user = args.switch_user or mgmt["username"]

    bfshell_path = args.bfshell or topo["switch"].get("bfshell", "bfshell")
    sde_env = args.sde_env or topo["switch"].get("sde_env", "")

    remote_dir = args.remote_dir.rstrip("/")
    remote_topo = f"{remote_dir}/system-topo.yaml"

    # Prepare remote dir
    logger.info("Preparing remote dir {}@{}:{}", user, host, remote_dir)
    sh(["ssh", f"{user}@{host}", "mkdir", "-p", remote_dir], check=True)

    # Copy topo
    logger.info("Copying topo to switch: {}", remote_topo)
    sh(["scp", str(topo_path), f"{user}@{host}:{remote_topo}"], check=True)

    # Execute modes in order
    modes = [m.strip() for m in args.mode.split(",") if m.strip()]
    for m in modes:
        if m not in MODE_TO_REMOTE:
            raise SystemExit(f"Unknown mode: {m}. Allowed: {', '.join(MODE_TO_REMOTE.keys())}")

        local_remote_script = Path(TOFINO_DIR) / "bfrt_python" / MODE_TO_REMOTE[m]
        if not local_remote_script.exists():
            raise FileNotFoundError(f"Missing remote script: {local_remote_script}")

        remote_script = f"{remote_dir}/{MODE_TO_REMOTE[m]}"
        logger.info("Copying remote script: {} -> {}", local_remote_script, remote_script)
        sh(["scp", str(local_remote_script), f"{user}@{host}:{remote_script}"], check=True)

        # Use bash -lc to get stable PATH and allow sourcing SDE env
        pre = ""
        if sde_env:
            pre = f"source {sde_env} >/dev/null 2>&1 || true; "

        remote_cmd = (
            f"bash -lc '{pre}"
            f"export TOPO_PATH={remote_topo} P4_PROG={args.p4_prog}; "
            f"{bfshell_path} -b {remote_script}"
            f"'"
        )

        logger.info("Running mode={} remotely via bfshell -b", m)
        cp = sh(["ssh", f"{user}@{host}", remote_cmd], check=False)

        (out_dir / f"tofino_{m}_stdout.log").write_text(cp.stdout or "")
        (out_dir / f"tofino_{m}_stderr.log").write_text(cp.stderr or "")

        if cp.returncode != 0:
            logger.error("Remote bfshell failed mode={} rc={}", m, cp.returncode)
            logger.error("See logs in {}", out_dir)
            raise SystemExit(cp.returncode)

        logger.info("Mode={} OK", m)

    logger.info("All modes done. Logs: {}", out_dir)


if __name__ == "__main__":
    main()
