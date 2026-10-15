"""collect.py telemetry (prompt 19): parsers, tagging by process tree, the ring. No command is run.
VM_STAT, SWAP, IOREG and THERM_QUIET are what the mac really printed on 2026-10-10
(docs/tutorial/28-telemetria.md §1); THERM_HOT, NETSTAT and the PS rows are synthetic, shaped
like the real output (no thermal event was recorded that day; the ps numbers are made up)."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools" / "visor"))
import collect as C  # noqa: E402

VM_STAT = """Mach Virtual Memory Statistics: (page size of 16384 bytes)
Pages free:                                     9061.
Pages active:                                 209859.
Pages inactive:                               205042.
Pages speculative:                              3981.
Pages wired down:                             510144.
"Translation faults":                      921204472.
Pages occupied by compressor:                  76961.
"""
SWAP = "total = 5120.00M  used = 3969.38M  free = 1150.62M  (encrypted)"
IOREG = ('"PerformanceStatistics" = {"In use system memory (driver)"=0,"Alloc system memory"=8407318528,'
         '"Tiler Utilization %"=4,"Renderer Utilization %"=4,"Device Utilization %"=98,"In use system memory"=6654509056}')
THERM_QUIET = """Note: No thermal warning level has been recorded
Note: No performance warning level has been recorded
Note: No CPU power status has been recorded
"""
THERM_HOT = """2026-10-10 20:00:00 -0500 CPU Power notify
\tCPU_Scheduler_Limit \t= 100
\tCPU_Available_CPUs \t= 10
\tCPU_Speed_Limit \t= 70
"""
PS = """  PID  PPID  PGID  %CPU    RSS COMM
73413  1196 73413   0.0   2048 bash
74189 73413 73413 380.5 6500000 Python
82461     1 82461   8.4  16944 sshd-session: <usuario> [priv]
43632 43624 43624   0.2 156144 claude
90565 90543 90565  53.0  80000 playwright --viewport-size 1280x800
"""
NETSTAT = """Name       Mtu   Network       Address            Ipkts Ierrs     Ibytes    Opkts Oerrs     Obytes  Coll
lo0        16384 <Link#1>                         100     0       5000      100     0       5000     0
en0        1500  <Link#11>   aa:bb:cc:dd:ee:ff  1000     0    2000000      900     0    1000000     0
en0        1500  192.168.1     192.0.2.20        1000     -    2000000      900     -    1000000     -
"""


class Parsers(unittest.TestCase):
    def test_vm_stat_uses_the_page_size_in_the_header(self):
        m = C.parse_vm_stat(VM_STAT)
        self.assertAlmostEqual(m["wired_gb"], 510144 * 16384 / 2**30, places=2)
        self.assertAlmostEqual(m["compressed_gb"], round(76961 * 16384 / 2**30, 2))
        self.assertEqual(C.parse_vm_stat("")["wired_gb"], None)       # unreadable -> null, not 0

    def test_swap_gb(self):
        self.assertEqual(C.parse_swap(SWAP), 3.88)
        self.assertIsNone(C.parse_swap("nothing here"))

    def test_gpu_from_ioreg_without_sudo(self):
        g = C.parse_ioreg_gpu(IOREG)
        self.assertEqual(g["pct"], 98)
        self.assertEqual(g["mem_in_use_gb"], 6.2)
        self.assertIsNone(C.parse_ioreg_gpu("")["pct"])

    def test_thermal_unknown_is_null_not_100(self):
        q = C.parse_pmset_therm(THERM_QUIET)
        self.assertIsNone(q["cpu_speed_limit"])
        self.assertIs(q["throttled"], False)            # macOS says: no warning recorded
        self.assertIsNone(q["temp_c"])
        hot = C.parse_pmset_therm(THERM_HOT)
        self.assertEqual(hot["cpu_speed_limit"], 70)
        self.assertIs(hot["throttled"], True)
        self.assertIsNone(C.parse_pmset_therm("")["throttled"])   # nothing read: unknown

    def test_nvidia_smi(self):
        g = C.parse_nvidia("16, 2679, 4096, 50\n")
        self.assertEqual((g["pct"], g["vram_used_gb"], g["vram_total_gb"], g["temp_c"]), (16, 2.62, 4.0, 50.0))
        self.assertIsNone(C.parse_nvidia(""))
        self.assertIsNone(C.parse_nvidia("NVIDIA-SMI has failed"))

    def test_ps_rows_and_names_hide_the_login_and_flags(self):
        rows = C.parse_ps(PS)
        self.assertEqual(len(rows), 5)
        by = {r["pid"]: r for r in rows}
        self.assertEqual(by[74189]["cpu"], 380.5)
        self.assertEqual(by[82461]["name"], "sshd-session")       # no '<usuario>'
        self.assertEqual(by[90565]["name"], "playwright")

    def test_netstat_sums_real_links_once(self):
        self.assertEqual(C.parse_netstat(NETSTAT), (2000000, 1000000))
        self.assertIsNone(C.parse_netstat(""))

    def test_cpu_pct_from_ticks(self):
        pct, per = C._cpu_pct([[10, 100], [0, 100]], [[60, 200], [100, 200]])
        self.assertEqual(per, [50, 100])
        self.assertEqual(pct, 75)
        self.assertEqual(C._cpu_pct(None, [[1, 2]]), (None, None))


class Tagging(unittest.TestCase):
    def test_a_render_inside_the_enjambre_tmux_is_labelled(self):
        rows = C.parse_ps(PS)
        C.tag_processes(rows, {73413: "tmux enjambre: cohbench", 43632: "sesión: lab-video-5b",
                               90565: "trabajo: live-probe2"})
        by = {r["pid"]: r.get("tag") for r in rows}
        self.assertEqual(by[74189], "tmux enjambre: cohbench")   # child of the pane
        self.assertEqual(by[43632], "sesión: lab-video-5b")
        self.assertEqual(by[90565], "trabajo: live-probe2")
        self.assertIsNone(by[82461])

    def test_process_group_counts_when_the_parent_is_gone(self):
        rows = [{"pid": 5, "ppid": 1, "pgid": 4, "cpu": 99.0}]
        C.tag_processes(rows, {4: "trabajo: x"})
        self.assertEqual(rows[0]["tag"], "trabajo: x")


class Ring(unittest.TestCase):
    def test_ring_trims_itself(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "r.jsonl")
            for i in range(25):
                n = C.ring_append(p, {"i": i}, keep=10)
            lines = Path(p).read_text(encoding="utf-8").splitlines()
            self.assertLessEqual(len(lines), 11)
            self.assertEqual(json.loads(lines[-1])["i"], 24)
            self.assertEqual(n, len(lines))

    def test_ring_line_names_nothing(self):
        line = C.ring_line({"at": "t", "cpu": {"pct": 5}, "mem": {"used_gb": 3.1, "pressure": "warn"},
                            "top": [{"name": "secret-project"}]})
        self.assertEqual(line["cpu"], 5)
        self.assertEqual(line["pr"], "warn")
        self.assertNotIn("secret-project", json.dumps(line))
        self.assertIsNone(line["gpu"])


FAST_MS = 1500        # one reading on an idle machine
LOADED_PCT = 70       # above this CPU % (measured by the same reading) the timing says nothing about us


def timing_verdict(tele: dict, strict: bool = False) -> str | None:
    """None = assert the timing; else the reason to skip it. A busy machine (5 Claude windows,
    a build) makes the reading slow without the collector being slow: 2834 ms and 31 s were seen
    on 2026-10-15 at 88-100 % CPU. ENJAMBRE_TELE_STRICT=1 asserts it anyway."""
    pct = (tele.get("cpu") or {}).get("pct")
    if strict or pct is None or pct < LOADED_PCT:
        return None
    return f"timing not judged: CPU {pct} % >= {LOADED_PCT} % (reading took {tele['ms']} ms)"


class Telemetry(unittest.TestCase):
    def test_this_machine_answers_fast_and_never_crashes(self):
        tele, prev = C.collect_telemetry(None)
        self.assertIn("cpu", tele)
        self.assertIsInstance(tele["ms"], int)
        if sys.platform in ("win32", "darwin"):
            self.assertIsNotNone(tele["cpu"])
            self.assertIn("ticks", prev)
            skip = timing_verdict(tele, os.environ.get("ENJAMBRE_TELE_STRICT") == "1")
            if skip:
                sys.stderr.write(f"\nWARNING test_telemetria: {skip}\n")
                self.skipTest(skip)
            self.assertLess(tele["ms"], FAST_MS)

    def test_the_skip_rule(self):
        self.assertIsNone(timing_verdict({"cpu": {"pct": 20}, "ms": 900}))
        self.assertIsNone(timing_verdict({"cpu": None, "ms": 900}))
        self.assertIn("CPU 88 %", timing_verdict({"cpu": {"pct": 88}, "ms": 2834}))
        self.assertIsNone(timing_verdict({"cpu": {"pct": 88}, "ms": 2834}, strict=True))


if __name__ == "__main__":
    unittest.main()
