#!/usr/bin/env python3
"""Paper sampler for H1 sweep. Public Bitget klines only. No orders."""
from __future__ import annotations

import os
import subprocess
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PID = ROOT / "sampler.pid"
LOG = ROOT / "logs" / "sampler.log"
SCAN_EVERY = 75.0
PUBLISH_EVERY = 600.0


def alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def read_pid() -> int:
    try:
        return int(PID.read_text().strip())
    except (OSError, ValueError):
        return 0


def log(msg: str) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    line = time.strftime("%Y-%m-%dT%H:%M:%S") + " " + msg
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def run_cmd(args: list[str], label: str) -> None:
    proc = subprocess.run(args, cwd=str(ROOT), check=False, capture_output=True, text=True)
    tail = (proc.stdout or "").strip().replace("\n", " ")
    if proc.returncode != 0:
        err = (proc.stderr or "").strip().replace("\n", " ")
        log(f"{label} exit={proc.returncode} {tail[-400:]} {err[-300:]}")
        return
    log(f"{label} {tail[-500:]}")


def run_scan() -> None:
    run_cmd([sys.executable, str(ROOT / "scan.py"), "--once"], "scan")


def run_publish(deploy: bool) -> None:
    args = [sys.executable, str(ROOT / "publish.py"), "--write"]
    if deploy:
        args.append("--deploy")
    run_cmd(args, "publish")


def loop() -> None:
    log(f"sampler loop pid={os.getpid()} every={SCAN_EVERY:.0f}s")
    last_pub = 0.0
    while True:
        t0 = time.time()
        try:
            run_scan()
            if t0 - last_pub >= PUBLISH_EVERY:
                run_publish(deploy=bool(os.environ.get("VERCEL_TOKEN")))
                last_pub = t0
        except Exception:
            log("loop error\n" + traceback.format_exc())
        wait = SCAN_EVERY - (time.time() - t0)
        if wait > 0:
            time.sleep(wait)


def main() -> None:
    if "--loop" in sys.argv:
        PID.write_text(str(os.getpid()))
        loop()
        return
    old = read_pid()
    if alive(old):
        print(f"sampler already running pid={old}")
        return
    LOG.parent.mkdir(parents=True, exist_ok=True)
    print("usage: sampler.py --loop", file=sys.stderr)
    raise SystemExit(2)


if __name__ == "__main__":
    main()
