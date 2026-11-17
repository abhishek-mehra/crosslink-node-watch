"""The meters: the parsers on saved command output, the growth rule on made-up histories, and one live
reading of this test's own process. Run from the repository root: python3 -m unittest discover tests"""
import os
import platform
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from watch import meters
from watch.report import FAIL, PASS, SKIP

# Captured on macOS 27, Nov 17 2026.
TOP = "Disks: 23035605/888G read, 5279436/85G written.\n\nMEM   CMPRS\n6336M 5380M\n"
# The format ss(8) prints; not captured from a node.
SS = 'LISTEN 0      128        127.0.0.1:8232      0.0.0.0:*    users:(("zebrad",pid=4242,fd=57))\n'
PROC_STATUS = "Name:\tzebrad\nVmPeak:\t 9000000 kB\nVmRSS:\t 2097152 kB\nThreads:\t48\n"
PROC_STAT = "4242 (zebrad worker) S 1 4242 4242 0 -1 4194560 " + " ".join(["0"] * 12) + " 123456 " + "0 " * 30


class Parsers(unittest.TestCase):
    def test_pid_from_ss(self):
        self.assertEqual(meters.pid_from_ss(SS), 4242)
        self.assertIsNone(meters.pid_from_ss(""))

    def test_pid_from_lsof(self):
        self.assertEqual(meters.pid_from_lsof("17195\n"), 17195)
        self.assertIsNone(meters.pid_from_lsof(""))

    def test_linux_falls_back_to_lsof_when_ss_finds_nothing(self):
        answers = {"ss": "", "lsof": "99\n"}
        self.assertEqual(meters.node_pid(8232, "Linux", run=lambda args: answers[args[0]]), 99)

    def test_etime(self):
        self.assertEqual(meters.etime_seconds("10:51"), 651)
        self.assertEqual(meters.etime_seconds("01:02:03"), 3723)
        self.assertEqual(meters.etime_seconds("1-17:30:50"), 149450)

    def test_footprint_from_top(self):
        self.assertEqual(meters.footprint_from_top(TOP), 6336)
        self.assertEqual(meters.footprint_from_top("1.5G\n"), 1536)
        self.assertEqual(meters.footprint_from_top(""), 0)

    def test_proc_status(self):
        self.assertEqual(meters.status_fields(PROC_STATUS), (2097152, 48))

    def test_proc_stat_with_a_space_in_the_name(self):
        self.assertEqual(meters.start_ticks(PROC_STAT), 123456)

    def test_macos_reading_from_saved_output(self):
        answers = {"ps -o": " 10:51 946048\n", "ps -M": "USER PID\n" + "x\n" * 72, "lsof": "COMMAND\n" + "x\n" * 281, "top": TOP}
        got = meters.read_macos(17195, run=lambda args: answers[" ".join(args[:2]) if args[0] == "ps" else args[0]])
        self.assertEqual(got, {"pid": 17195, "up": 651, "rss_mb": 924, "footprint_mb": 6336, "threads": 72, "files": 281})

    def test_process_gone_gives_no_reading(self):
        self.assertIsNone(meters.read_macos(1, run=lambda args: ""))
        self.assertIsNone(meters.read_linux(1, proc="/nonexistent"))


class LiveReading(unittest.TestCase):
    def test_this_process(self):
        got = meters.read_linux(os.getpid()) if platform.system() == "Linux" else meters.read_macos(os.getpid())
        self.assertGreater(got["rss_mb"], 0)
        self.assertGreater(got["threads"], 0)
        self.assertGreater(got["files"], 0)
        self.assertGreaterEqual(got["up"], 0)

    def test_disk(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "v31", "net"))
            for name, size in (("006594.log", 3000), ("000012.sst", 5000), ("LOG", 100)):
                with open(os.path.join(d, "v31", "net", name), "wb") as f:
                    f.write(b"x" * size)
            got = meters.read_disk(d)
        self.assertEqual(got["state_gb"], round(8100 / 1024 ** 3, 2))
        self.assertGreater(got["free_gb"], 0)


NOW = time.mktime(time.strptime("2026-11-17 12:00:00", "%Y-%m-%d %H:%M:%S"))
HOUR = 3600


def line(hours_ago, group, **values):
    stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(NOW - hours_ago * HOUR))
    return f"{stamp} meters.{group} PASS " + " ".join(f"{k}={v}" for k, v in values.items())


def proc(up, rss=1000, threads=50, files=250):
    return {"pid": 1, "up": up, "rss_mb": rss, "threads": threads, "files": files}


DISK = {"state_gb": 3.0, "wal_gb": 0.9, "free_gb": 46.0}


class Growth(unittest.TestCase):
    def check(self, lines, process=None, disk=None):
        return meters.growth(NOW, process, disk, meters.history(lines))

    def test_no_history_skips(self):
        self.assertEqual(self.check([], proc(10 * HOUR), DISK)[0], SKIP)

    def test_history_too_recent_skips(self):
        self.assertEqual(self.check([line(1, "process", up=9 * HOUR, rss_mb=1000)], proc(10 * HOUR))[0], SKIP)

    def test_steady_memory_passes(self):
        old = line(6, "process", up=4 * HOUR, rss_mb=1000, threads=50, files=250)
        self.assertEqual(self.check([old], proc(10 * HOUR, rss=1100))[0], PASS)

    def test_memory_doubled_fails(self):
        old = line(6, "process", up=4 * HOUR, rss_mb=1000, threads=50, files=250)
        status, detail = self.check([old], proc(10 * HOUR, rss=2100))
        self.assertEqual(status, FAIL)
        self.assertIn("rss_mb 1000 -> 2100", detail)

    def test_small_values_are_ignored(self):
        old = line(6, "process", up=4 * HOUR, rss_mb=100, threads=20, files=50)
        self.assertEqual(self.check([old], proc(10 * HOUR, rss=300, threads=60, files=150))[0], PASS)

    def test_a_restart_in_between_is_not_growth(self):
        old = line(6, "process", up=4 * HOUR, rss_mb=1000)
        self.assertEqual(self.check([old], proc(1 * HOUR, rss=5000))[0], SKIP)

    def test_old_reading_during_start_up_is_not_compared(self):
        old = line(6, "process", up=600, rss_mb=1000)
        self.assertEqual(self.check([old], proc(600 + 6 * HOUR, rss=5000))[0], SKIP)

    def test_free_disk_halved_fails(self):
        old = line(6, "disk", state_gb=3.0, wal_gb=0.9, free_gb=46.0)
        status, detail = self.check([old], disk={**DISK, "free_gb": 20.0})
        self.assertEqual(status, FAIL)
        self.assertIn("free_gb 46 -> 20", detail)

    def test_write_ahead_logs_doubled_fails(self):
        old = line(6, "disk", state_gb=3.0, wal_gb=1.0, free_gb=46.0)
        self.assertEqual(self.check([old], disk={**DISK, "wal_gb": 2.5})[0], FAIL)

    def test_other_checks_lines_and_broken_lines_are_ignored(self):
        lines = ["2026-11-17 06:00:00 invariants.finality_lag PASS tip=1 final=1 lag=0 limit=50",
                 "garbage", "2026-11-17 06:00:00 meters.process PASS up=oops"]
        self.assertEqual(meters.history(lines), {"process": [], "disk": []})


if __name__ == "__main__":
    unittest.main()
