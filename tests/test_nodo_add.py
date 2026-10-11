"""`enjambre nodo add|doctor|rm` (prompt 19 step 5) against a fake ssh: no network, no Mac.
The fake keeps a tiny remote filesystem so add -> doctor -> rm runs end to end."""
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import nodo as N  # noqa: E402

TOML = """# nodes
[nodes.mac]
label = "Mac mini"
host = "mac"
os = "macos"
"""
NOW = 1_800_000_000.0


class FakeMac:
    """Answers the commands nodo.py sends, keeping files and loaded agents in dicts."""

    def __init__(self, reachable=True, snapshot_after=0, bootstrap_rc=0):
        self.files, self.loaded, self.calls = {}, set(), []
        self.reachable, self.snapshot_after, self.bootstrap_rc = reachable, snapshot_after, bootstrap_rc
        self.cats = 0

    def __call__(self, cmd, stdin=None, timeout=20):
        c = cmd[-1]
        self.calls.append(c)
        if not self.reachable:
            return 255, "", "ssh: connect to host mac port 22: Connection timed out"
        if c == "true":
            return 0, "", ""
        if c.startswith("sysctl"):
            return 0, "17179869184\n10\narm64\n", ""
        if "collect.py.tmp" in c:
            self.files["collect.py"] = stdin
            return 0, "", ""
        if c.startswith("shasum"):
            if "collect.py" not in self.files:
                return 1, "", ""
            return 0, hashlib.sha256(self.files["collect.py"].encode("utf-8")).hexdigest() + "  x\n", ""
        if c.startswith("test -e"):
            return 0, "exists\n" if "plist" in self.files else "", ""
        if "launchctl bootstrap" in c:
            self.files["plist"] = stdin
            if self.bootstrap_rc == 0:
                self.loaded.add("dev.enjambre.visor.laptop")
            return self.bootstrap_rc, "", "Bootstrap failed: 5: Input/output error"
        if c.startswith("launchctl print"):
            ok = "dev.enjambre.visor.laptop" in self.loaded
            return (0, "\tstate = not running\n\tlast exit code = 0\n", "") if ok else (113, "", "")
        if c.startswith("cat ~/.enjambre/visor/snapshot-laptop.json"):
            self.cats += 1
            if self.cats <= self.snapshot_after:
                return 1, "", "No such file or directory"
            return 0, json.dumps({"schema": 1, "node": "laptop", "collected": NOW, "os": "darwin",
                                  "sessions": [], "memory": {}, "disks": []}), ""
        if "launchctl bootout" in c:
            self.loaded.discard("dev.enjambre.visor.laptop")
            self.files.pop("plist", None)
            return 0, "gone\n", ""
        raise AssertionError("unexpected command: " + c)


class Pure(unittest.TestCase):
    def test_measure_parsers(self):
        self.assertEqual(N.parse_measure("macos", "17179869184\n10\narm64\n"), {"ram_gb": 16, "cores": 10, "arch": "arm64"})
        self.assertEqual(N.parse_measure("linux", "x86_64\n8\n32736124\n"), {"arch": "x86_64", "cores": 8, "ram_gb": 31})
        self.assertEqual(N.parse_measure("macos", ""), {"ram_gb": None, "cores": None, "arch": None})

    def test_unmeasured_fields_are_left_out(self):
        t = N.toml_table("lap", "lap", "macos", "Laptop", {"ram_gb": None, "cores": 8, "arch": None}, False, "2026-10-10")
        self.assertNotIn("ram_gb", t)
        self.assertIn("cores = 8", t)

    def test_table_round_trip_is_byte_identical(self):
        t = N.toml_table("laptop", "laptop", "macos", "Laptop", {"ram_gb": 16, "cores": 10, "arch": "arm64"}, False, "2026-10-10")
        added = N.add_table(TOML, t)
        self.assertIn("[nodes.laptop]", added)
        self.assertEqual(N.remove_table(added, "laptop"), TOML)
        self.assertIsNone(N.remove_table(TOML, "mac"), "a hand-written table is never removed")

    def test_plist_gets_its_own_label_node_and_snapshot(self):
        p = N.plist_for("laptop", N.PLIST.read_text(encoding="utf-8"))
        self.assertIn("<string>dev.enjambre.visor.laptop</string>", p)
        self.assertIn("<string>--node</string><string>laptop</string>", p)
        self.assertIn("snapshot-laptop.json", p)
        self.assertNotIn("--models", p)
        self.assertNotIn("<string>dev.enjambre.visor</string>", p)

    def test_snapshot_shape(self):
        good = json.dumps({"schema": 1, "node": "x", "collected": NOW, "os": "darwin", "sessions": [], "memory": {}, "disks": []})
        self.assertIsNone(N.check_snapshot(good, "x", NOW, 60))
        self.assertIn("expected", N.check_snapshot(good, "y", NOW, 60))
        self.assertIn("older", N.check_snapshot(good, "x", NOW + 600, 60))
        self.assertIn("missing", N.check_snapshot("{}", "x", NOW, 60))


class Commands(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.toml = Path(self.d.name) / "nodes.toml"
        self.toml.write_bytes(TOML.encode("utf-8"))
        self.said = []

    def tearDown(self):
        self.d.cleanup()

    def add(self, fake, **kw):
        return N.add("laptop", "laptop", "macos", "Laptop", run=fake, toml=self.toml, say=self.said.append,
                     sleep=lambda s: None, clock=lambda: NOW, **kw)

    def nodes(self):
        import tomllib
        return tomllib.loads(self.toml.read_text(encoding="utf-8"))["nodes"]

    def test_add_doctor_rm(self):
        fake = FakeMac(snapshot_after=2)
        self.assertEqual(self.add(fake), 0, self.said)
        n = self.nodes()["laptop"]
        self.assertEqual((n["ram_gb"], n["cores"], n["arch"], n["runnable"]), (16, 10, "arm64", False))
        self.assertEqual(n["visor_snapshot"], "~/.enjambre/visor/snapshot-laptop.json")
        self.assertTrue(any(x.startswith("4 ok") for x in self.said), self.said)
        self.assertIn("live --stop", self.said[-1])           # new node: the loops must re-read nodes.toml
        self.said.clear()
        self.assertEqual(N.doctor("laptop", self.nodes(), run=fake, say=self.said.append), 0, self.said)
        self.assertEqual(N.rm("laptop", self.nodes(), run=fake, toml=self.toml, say=self.said.append), 0)
        self.assertEqual(self.toml.read_bytes(), TOML.encode("utf-8"))
        self.assertEqual(fake.loaded, set())

    def test_unreachable_writes_nothing_and_says_step_1(self):
        self.assertEqual(self.add(FakeMac(reachable=False)), 3)
        self.assertIn("paso 1", self.said[-1])
        self.assertEqual(self.toml.read_bytes(), TOML.encode("utf-8"))

    def test_launcher_failure_names_step_3(self):
        self.assertEqual(self.add(FakeMac(bootstrap_rc=5)), 3)
        self.assertIn("paso 3", self.said[-1])
        self.assertIn("nodo rm laptop", self.said[-1])

    def test_snapshot_never_arrives_names_step_4(self):
        clock = iter(range(0, 10_000, 10))
        r = N.add("laptop", "laptop", "macos", None, run=FakeMac(snapshot_after=10**6), toml=self.toml,
                  say=self.said.append, sleep=lambda s: None, clock=lambda: NOW + next(clock))
        self.assertEqual(r, 3)
        self.assertIn("paso 4", self.said[-1])

    def test_refusals_before_writing(self):
        self.assertEqual(N.add("mac", "mac", "macos", None, run=FakeMac(), toml=self.toml, say=self.said.append), 2)
        self.assertEqual(N.add("Bad Name", "x", "macos", None, run=FakeMac(), toml=self.toml, say=self.said.append), 2)
        self.assertEqual(N.add("lin", "lin", "linux", None, run=FakeMac(), toml=self.toml, say=self.said.append), 2)
        self.assertEqual(self.toml.read_bytes(), TOML.encode("utf-8"))


if __name__ == "__main__":
    unittest.main()
