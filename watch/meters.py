"""Meters: record what the node process costs the machine, and report when a meter doubles within 6 hours.

Usage, from the repository root: python3 -m watch.meters [--config config.toml] [--quiet]
  --quiet  print only FAIL lines
Meant to run every few minutes. Linux and macOS. It makes no RPC call: it reads the process table and the
node's data folder. Exit code 1 when a meter has grown.

  meters.process  pid, up (s since the process started), rss_mb, threads, files (open files), and on macOS
                  footprint_mb (memory as Activity Monitor shows it; rss leaves out compressed memory there)
  meters.disk     state_gb (the node's data folder), wal_gb (database write-ahead logs in it), free_gb.
                  SKIP when node_state_dir is not set in the config.
  meters.growth   FAIL when a meter is at least growth_factor times its value of growth_window_hours ago
                  (free_gb: at most 1/growth_factor of it). Small values are ignored (FLOOR). Process meters
                  are compared only when the same process has run since the old reading and had been up for
                  WARMUP then, so a restart and its climb do not count as growth.

The node process is the one listening on the RPC port in node_url, so a new binary name needs no change here.
The history is this tool's own lines in results_log; without results_log there is nothing to compare with.
"""
import argparse
import os
import platform
import re
import shutil
import subprocess
import sys
import time
from urllib.parse import urlparse

from watch.config import load
from watch.report import FAIL, PASS, SKIP, emit

SLACK = 20 * 60      # s; accept an old reading this far from the window mark
WARMUP = 30 * 60     # s of uptime before process meters are compared
FLOOR = {"rss_mb": 500, "footprint_mb": 500, "threads": 100, "files": 200, "wal_gb": 1.5, "state_gb": 1.0}
PROCESS = ("rss_mb", "footprint_mb", "threads", "files")
DISK = ("state_gb", "wal_gb", "free_gb")
WAL_NAME = re.compile(r"^\d+\.log$")   # RocksDB write-ahead logs: 006594.log


def sh(args):
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=30).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


# --- finding the process -------------------------------------------------------------------------

def pid_from_ss(text):
    """pid from `ss -H -ltnp 'sport = :PORT'`: ... users:(("zebrad",pid=4242,fd=57))"""
    m = re.search(r"pid=(\d+)", text)
    return int(m.group(1)) if m else None


def pid_from_lsof(text):
    """pid from `lsof -nP -iTCP:PORT -sTCP:LISTEN -t`: one pid per line."""
    first = text.split()
    return int(first[0]) if first and first[0].isdigit() else None


def node_pid(port, system, run=sh):
    pid = pid_from_ss(run(["ss", "-H", "-ltnp", f"sport = :{port}"])) if system == "Linux" else None
    return pid or pid_from_lsof(run(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"]))


# --- reading the process -------------------------------------------------------------------------

def etime_seconds(text):
    """ps etime, [[dd-]hh:]mm:ss, as seconds."""
    parts = [int(x) for x in re.split(r"[-:]", text.strip())]
    return sum(v * w for v, w in zip(reversed(parts), (1, 60, 3600, 86400)))


def to_mb(text):
    """top's sizes: '5773M', '1.2G', '900K+' -> MB."""
    m = re.match(r"([\d.]+)([KMGB]?)", text or "")
    if not m:
        return 0
    return round(float(m.group(1)) * {"K": 1 / 1024, "M": 1, "G": 1024, "B": 1 / 1048576, "": 1 / 1048576}[m.group(2)])


def footprint_from_top(text):
    """MB from `top -l 1 -pid PID -stats mem`: the last line is the value, e.g. '6336M'."""
    lines = text.strip().splitlines()
    return to_mb(lines[-1].split()[0]) if lines and re.match(r"[\d.]", lines[-1]) else 0


def lines_minus_header(text):
    return max(len(text.splitlines()) - 1, 0)


def read_macos(pid, run=sh):
    ps = run(["ps", "-o", "etime=,rss=", "-p", str(pid)]).split()
    if len(ps) != 2:
        return None
    files = 0
    for _ in range(2):      # lsof now and then returns nothing; ask twice
        files = files or lines_minus_header(run(["lsof", "-p", str(pid)]))
    return {"pid": pid, "up": etime_seconds(ps[0]), "rss_mb": round(int(ps[1]) / 1024),
            "footprint_mb": footprint_from_top(run(["top", "-l", "1", "-pid", str(pid), "-stats", "mem"])),
            "threads": lines_minus_header(run(["ps", "-M", "-p", str(pid)])), "files": files}


def status_fields(text):
    """VmRSS (kB) and Threads from /proc/PID/status."""
    found = dict(re.findall(r"^(VmRSS|Threads):\s+(\d+)", text, re.M))
    return int(found.get("VmRSS", 0)), int(found.get("Threads", 0))


def start_ticks(stat_text):
    """Field 22 of /proc/PID/stat: start time in clock ticks since boot. The name in field 2 may hold spaces."""
    return int(stat_text.rsplit(")", 1)[1].split()[19])


def read_linux(pid, proc="/proc"):
    base = os.path.join(proc, str(pid))
    try:
        with open(os.path.join(base, "status")) as f:
            rss_kb, threads = status_fields(f.read())
        with open(os.path.join(base, "stat")) as f:
            ticks = start_ticks(f.read())
        with open(os.path.join(proc, "uptime")) as f:
            since_boot = float(f.read().split()[0])
        files = len(os.listdir(os.path.join(base, "fd")))
    except (OSError, ValueError, IndexError):
        return None
    return {"pid": pid, "up": round(since_boot - ticks / os.sysconf("SC_CLK_TCK")),
            "rss_mb": round(rss_kb / 1024), "threads": threads, "files": files}


def read_disk(state_dir):
    total = wal = 0
    for folder, _dirs, names in os.walk(state_dir):
        for name in names:
            try:
                size = os.path.getsize(os.path.join(folder, name))
            except OSError:      # a file the database removed while we walked
                continue
            total += size
            if WAL_NAME.match(name):
                wal += size
    gb = 1024 ** 3
    return {"state_gb": round(total / gb, 2), "wal_gb": round(wal / gb, 2),
            "free_gb": round(shutil.disk_usage(state_dir).free / gb, 1)}


# --- growth --------------------------------------------------------------------------------------

def history(lines):
    """{'process': [(unix time, {name: number})], 'disk': [...]} from this tool's own result lines."""
    out = {"process": [], "disk": []}
    for line in lines:
        m = re.match(r"(\S+ \S+) meters\.(process|disk) PASS (.*)", line)
        if not m:
            continue
        try:
            t = time.mktime(time.strptime(m.group(1), "%Y-%m-%d %H:%M:%S"))
            out[m.group(2)].append((t, {k: float(v) for k, v in (pair.split("=", 1) for pair in m.group(3).split())}))
        except ValueError:
            continue
    return out


def reading_from(readings, when):
    """The reading closest to `when`, if one is within SLACK of it."""
    near = [(abs(t - when), t, values) for t, values in readings if abs(t - when) <= SLACK]
    return min(near, key=lambda x: x[0])[1:] if near else None


def growth(now, process, disk, past, factor=2.0, window=6 * 3600):
    """(status, detail) for the growth check. `process` / `disk` are this run's readings or None."""
    grown, compared = [], 0
    old = reading_from(past["process"], now - window)
    # the same process, and it was past its start-up climb at the old reading
    if process and old and old[1].get("up", 0) >= WARMUP and process["up"] >= old[1]["up"] + (now - old[0]) - SLACK:
        compared += 1
        for name in PROCESS:
            was, is_ = old[1].get(name), process.get(name)
            if was and is_ and is_ >= FLOOR[name] and is_ >= was * factor:
                grown.append(f"{name} {was:g} -> {is_:g}")
    old = reading_from(past["disk"], now - window)
    if disk and old:
        compared += 1
        for name in ("state_gb", "wal_gb"):
            was, is_ = old[1].get(name), disk[name]
            if was and is_ >= FLOOR[name] and is_ >= was * factor:
                grown.append(f"{name} {was:g} -> {is_:g}")
        was = old[1].get("free_gb")
        if was and disk["free_gb"] <= was / factor:
            grown.append(f"free_gb {was:g} -> {disk['free_gb']:g}")
    hours = f"{window / 3600:g} h"
    if grown:
        return FAIL, f"in {hours}: " + ", ".join(grown)
    if not compared:
        return SKIP, f"no comparable reading from {hours} ago"
    return PASS, f"window_h={window / 3600:g} factor={factor:g}"


def pairs(values, names):
    return " ".join(f"{k}={values[k]}" for k in names if k in values)


def run(cfg, now=None, system=None):
    """This run's lines as (name, status, detail)."""
    now = time.time() if now is None else now
    system = system or platform.system()
    port = urlparse(cfg["node_url"]).port
    pid = node_pid(port, system)
    process = (read_linux(pid) if system == "Linux" else read_macos(pid)) if pid else None
    res = [("process", PASS, pairs(process, ("pid", "up") + PROCESS)) if process else
           ("process", SKIP, f"no process is listening on port {port}")]
    disk = None
    if not cfg["node_state_dir"]:
        res.append(("disk", SKIP, "node_state_dir is not set in the config"))
    elif not os.path.isdir(cfg["node_state_dir"]):
        res.append(("disk", SKIP, f"node_state_dir is not a folder: {cfg['node_state_dir']}"))
    else:
        disk = read_disk(cfg["node_state_dir"])
        res.append(("disk", PASS, pairs(disk, DISK)))
    if not cfg["results_log"]:
        res.append(("growth", SKIP, "results_log is not set in the config, so there is no history"))
    else:
        try:
            with open(cfg["results_log"], errors="replace") as f:
                past = history(f)
        except FileNotFoundError:
            past = history([])
        res.append(("growth", *growth(now, process, disk, past, cfg["growth_factor"],
                                      cfg["growth_window_hours"] * 3600)))
    return res


def main(argv=None):
    ap = argparse.ArgumentParser(description="Record what the node process costs the machine.")
    ap.add_argument("--config", default="config.toml")
    ap.add_argument("--quiet", action="store_true", help="print only FAIL lines")
    args = ap.parse_args(argv)
    cfg = load(args.config)
    return emit("meters", run(cfg), cfg["results_log"], args.quiet)


if __name__ == "__main__":
    sys.exit(main())
