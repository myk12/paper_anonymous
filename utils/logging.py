from __future__ import annotations

import os
import socket
import sys
from pathlib import Path
from typing import Optional

from loguru import logger

# Standardized logging setup for all scripts, with both console and rotating file sinks, and common context (host, pid) bound to every log line.

def setup_logger(
    out_dir: Path,
    *,
    level: str = "INFO",
    console_level: Optional[str] = None,
    rotation: str = "50 MB",
    retention: str = "14 days",
    enqueue: bool = True,
) -> None:
    """
    Standard logging setup:
    - stdout (human readable)
    - file (rotating), both full log + errors-only
    - binds common context: host, pid
    """
    out_dir.mkdir(parents=True, exist_ok=True)

    logger.remove()

    host = socket.gethostname()
    pid = os.getpid()

    # Console sink
    logger.add(
        sys.stdout,
        level=console_level or level,
        backtrace=False,
        diagnose=False,
        colorize=True,
        format="<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | <level>{level: <8}</level> | "
               "<cyan>{extra[host]}</cyan>:{extra[pid]} | {message}",
    )

    # File sink (full)
    logger.add(
        str(out_dir / "run.log"),
        level=level,
        rotation=rotation,
        retention=retention,
        enqueue=enqueue,
        backtrace=True,
        diagnose=False,
        format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {extra[host]}:{extra[pid]} | {message}",
    )

    # File sink (errors only)
    logger.add(
        str(out_dir / "error.log"),
        level="ERROR",
        rotation=rotation,
        retention=retention,
        enqueue=enqueue,
        backtrace=True,
        diagnose=False,
        format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {extra[host]}:{extra[pid]} | {message}",
    )

    # Bind defaults so every log line carries host/pid
    logger.configure(extra={"host": host, "pid": pid})
