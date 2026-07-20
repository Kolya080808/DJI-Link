#!/usr/bin/env python3
"""
applog.py — file logging in the Beat Saber style.

- The current run always writes to  logs/latest.log
- On the NEXT startup, the previous latest.log is renamed to a dated archive
  (logs/2026-07-20_21-53-04.log), using the time that log was last written.
- Archives older than KEEP_DAYS (default 7) are deleted at startup.

Everything is tee'd: messages still print to the console (only logs go to the
file, nothing else clutters stdout). Use `get_logger()` anywhere, or just keep
calling the existing print()/log() helpers — pc_client routes them through here.
"""

from __future__ import annotations
import logging
import os
import time
from datetime import datetime, timedelta
from logging.handlers import RotatingFileHandler
from pathlib import Path

KEEP_DAYS = 7
LOGGER_NAME = "djilink"

# In-memory tail of recent log lines, so the GUI can show a live log pane without
# re-reading the file. Bounded so it never grows unbounded during a long flight.
from collections import deque
_TAIL = deque(maxlen=400)


def tail() -> list[str]:
    return list(_TAIL)


class _TailHandler(logging.Handler):
    def emit(self, record):
        try:
            _TAIL.append(self.format(record))
        except Exception:
            pass

# logs/ sits next to this file, so it works the same however the app is launched.
LOG_DIR = Path(__file__).resolve().parent / "logs"
LATEST = LOG_DIR / "latest.log"

_configured = False


def _archive_previous() -> None:
    """Rename an existing latest.log to a dated file, named by its last write time."""
    if not LATEST.exists():
        return
    try:
        mtime = datetime.fromtimestamp(LATEST.stat().st_mtime)
    except OSError:
        mtime = datetime.now()
    stamp = mtime.strftime("%Y-%m-%d_%H-%M-%S")
    target = LOG_DIR / f"{stamp}.log"
    # Avoid clobbering if two runs share a second.
    n = 1
    while target.exists():
        target = LOG_DIR / f"{stamp}_{n}.log"
        n += 1
    try:
        LATEST.rename(target)
    except OSError:
        pass  # if we can't archive, we'll just overwrite latest.log below


def _cleanup_old() -> None:
    """Delete dated archives older than KEEP_DAYS. Never touches latest.log."""
    cutoff = time.time() - KEEP_DAYS * 86400
    for f in LOG_DIR.glob("*.log"):
        if f.name == "latest.log":
            continue
        try:
            if f.stat().st_mtime < cutoff:
                f.unlink()
        except OSError:
            pass


def setup(verbose: bool = False) -> logging.Logger:
    """Initialise file logging. Call once at startup, before anything logs."""
    global _configured
    logger = logging.getLogger(LOGGER_NAME)
    if _configured:
        return logger

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    _archive_previous()
    _cleanup_old()

    logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    logger.propagate = False

    # File: fresh latest.log every run (archiving already moved the old one aside).
    fh = RotatingFileHandler(LATEST, maxBytes=8 * 1024 * 1024, backupCount=3,
                             encoding="utf-8", mode="w")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(message)s",
                                      datefmt="%H:%M:%S"))
    logger.addHandler(fh)

    th = _TailHandler()
    th.setLevel(logging.INFO)
    th.setFormatter(logging.Formatter("%(asctime)s %(message)s", datefmt="%H:%M:%S"))
    logger.addHandler(th)

    _configured = True
    logger.info("=== log start %s (keep %d days) ===",
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"), KEEP_DAYS)
    return logger


def get_logger() -> logging.Logger:
    return logging.getLogger(LOGGER_NAME)
