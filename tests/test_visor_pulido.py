"""Prompt 22: what "now" means on the visor (§1), three counts that did not add up (§2), and
personal paths cut before anything is shown or published (§3)."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools" / "visor"))
import collect as C  # noqa: E402
import live as LV  # noqa: E402
import planner as PL  # noqa: E402
import scheduler as S  # noqa: E402

NOW = 1_800_000_000.0
NODES = {"win": {"host": "local", "label": "PC", "os": "windows"},
         "mac": {"host": "mac", "label": "Mac mini", "os": "macos"}}
HTML = (ROOT / "tools" / "visor" / "visor.html").read_text(encoding="utf-8")


def payloads(win_at, mac_at):
    return {"win": {"sessions": [], "detached": [], "telemetria": {"at": LV.iso(win_at), "cpu": {"pct": 10}}},
            "mac": {"sessions": [], "detached": [], "telemetria": {"at": LV.iso(mac_at), "cpu": {"pct": 50}}}}


# ------------------------------------------------------------------ §1
class TelemetryAge(unittest.TestCase):
    def test_levels(self):
        self.assertEqual(LV.tele_level({"at": LV.iso(NOW - 12)}, NOW), (12, "ok"))
        self.assertEqual(LV.tele_level({"at": LV.iso(NOW - 61)}, NOW), (61, "warn"))
        self.assertEqual(LV.tele_level({"at": LV.iso(NOW - 181)}, NOW), (181, "crit"))
        self.assertEqual(LV.tele_level(None, NOW), (None, None))
        self.assertEqual(LV.tele_level({"at": "garbage"}, NOW), (None, None))

    def test_old_reading_is_marked_on_its_node(self):
        # fixture: the Mac's last good read is 7 min old (ssh failing, tele_last kept)
        live = LV.assemble(NODES, payloads(NOW - 3, NOW - 420), [], {}, {}, NOW)
        self.assertEqual((live["nodos"]["win"]["tele_edad_s"], live["nodos"]["win"]["tele_nivel"]), (3, "ok"))
        self.assertEqual((live["nodos"]["mac"]["tele_edad_s"], live["nodos"]["mac"]["tele_nivel"]), (420, "crit"))
        self.assertEqual(live["tele_umbrales"], [LV.TELE_AMBER_S, LV.TELE_RED_S])

    def test_page_says_now_window_and_sample_age(self):
        for m in ("'lectura vieja' : 'ahora'", "· hace ${ago(g.a)}", "1 punto / ${ago(st)}",
                  "los gauges siempre son ahora; el selector solo cambia las curvas",
                  "muestra de hace ${tAgo}", "telemetría hace ${ta ? ago(ta.a)",
                  "Instantánea <span class=\"muted\">(discos, memoria, montajes)</span>", "tele_umbrales"):
            self.assertIn(m, HTML, m)


# ------------------------------------------------------------------ §2.1
class HeaderCountsExternals(unittest.TestCase):
    def test_three_running_cards_three_in_the_header(self):
        live = {"ahora": {"running": 2, "por_nodo": {"win": 1, "mac": 1}},
                "planificador": {"externos": [{"id": "lab:a1", "nodo": "mac", "estado": "running"},
                                              {"id": "lab:b2", "nodo": "mac", "estado": "paused"}]}}
        A = LV.count_externos(live)["ahora"]
        self.assertEqual((A["running"], A["por_nodo"], A["externos"]), (3, {"win": 1, "mac": 2}, 1))

    def test_planner_error_counts_nothing_extra(self):
        A = LV.count_externos({"ahora": {"running": 1, "por_nodo": {"win": 1}},
                               "planificador": {"error": "boom"}})["ahora"]
        self.assertEqual((A["running"], A["externos"]), (1, 0))

    def test_page_breaks_it_down(self):
        self.assertIn("${A.externos} externo${A.externos > 1 ? 's' : ''}", HTML)


# ------------------------------------------------------------------ §2.2
LAB_LOG = """$ lab-video render in.mp4 --preset dmt --log-every 50
dmt: in.mp4 -> out.mp4 · 520 frames
50/520 frames · 2.1 fps · 24 s elapsed · ETA 224 s
200/520 frames · 2.3 fps · 51 s elapsed · ETA 139 s
"""


class ExternalSignal(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        self.saved = PL.STATE, PL.STATE_FILE, PL.AUDIT, PL.ADOPTED
        PL.STATE, PL.STATE_FILE, PL.AUDIT, PL.ADOPTED = d, d / "scheduler.json", d / "scheduler.jsonl", d / "adopted.json"
        self.pl = PL.Planner({"mac": {"host": "mac", "os": "macos"}}, S.load(),
                             run=lambda *a, **k: (0, "", ""), log=lambda m: None)

    def tearDown(self):
        PL.STATE, PL.STATE_FILE, PL.AUDIT, PL.ADOPTED = self.saved
        self.tmp.cleanup()

    def test_lab_progress_from_its_log(self):
        log = Path(self.tmp.name) / "a1b2c3.log"
        log.write_text(LAB_LOG, encoding="utf-8")
        self.assertEqual(C.lab_progress(str(log)), {"frames": 200, "frames_total": 520, "eta_s": 139, "pct": 38.5})
        log.write_text("$ lab-video render\nloading weights\n", encoding="utf-8")
        self.assertIsNone(C.lab_progress(str(log)))
        self.assertIsNone(C.lab_progress(str(log) + ".missing"))

    def test_lab_render_signal_is_the_poll_and_carries_progress(self):
        lab = [{"id": "a1b2c3", "status": "running", "preset": "dmt", "elapsed_s": 51.0,
                "polled": NOW - 4, "frames": 200, "frames_total": 520, "pct": 38.5, "eta_s": 139}]
        out = self.pl.tick({"trabajos": [], "telemetria": {}},
                           {"mac": {"at": NOW - 9, "detached": [], "externos": {"render_lab": lab}}}, NOW)
        x = out["externos"][0]
        self.assertEqual((x["last_activity"], x["senal"], x["pct"], x["frames"]), (NOW - 4, "GET /jobs", 38.5, 200))

    def test_adopted_without_heartbeat_takes_the_process_poll(self):
        # fixture: an adopted process has no beat of its own; before 22 its card said "hace —"
        PL.P.write_json_atomic(PL.ADOPTED, {"adopted": [{"node": "mac", "pid": 4242, "priority": "baja",
                                                         "name": "ffmpeg"}]})
        out = self.pl.tick({"trabajos": [], "telemetria": {}}, {"mac": {"at": NOW - 7, "detached": []}}, NOW)
        x = out["externos"][0]
        self.assertEqual((x["id"], x["last_activity"], x["senal"]), ("pid:mac:4242", NOW - 7, "lista de procesos"))

    def test_card_shows_progress_and_where_the_signal_comes_from(self):
        for m in ("de render${x.pct != null", "cuadros", "(${esc(x.senal)})", "x.tipo !== 'sesion' && x.pct != null"):
            self.assertIn(m, HTML, m)


# ------------------------------------------------------------------ §2.3
TYPEPERF_OUT = r""""(PDH-CSV 4.0)","\\BOX-1\Network Interface(Intel[R] Ethernet Connection I219-V)\Bytes Received/sec","\\BOX-1\Network Interface(Realtek 8822BE Wireless LAN 802.11ac PCI-E NIC)\Bytes Received/sec","\\BOX-1\Network Interface(Intel[R] Ethernet Connection I219-V)\Bytes Sent/sec","\\BOX-1\Network Interface(Realtek 8822BE Wireless LAN 802.11ac PCI-E NIC)\Bytes Sent/sec","\\BOX-1\PhysicalDisk(_Total)\Disk Bytes/sec"
"10/11/2026 07:12:31.415","2097152.0","0.000000","1048576.0","0.000000","5242880.0"
Exiting, please wait...
The command completed successfully.
"""
NETSTAT_IF = """Name       Mtu   Network       Address            Ipkts Ierrs     Ibytes    Opkts Oerrs     Obytes  Coll
lo0        16384 <Link#1>                         100     0       5000      100     0       5000     0
gif0*      1280  <Link#2>                           0     0          0        0     0          0     0
en0        1500  <Link#11>   aa:bb:cc:dd:ee:ff  1000     0    {e0}      900     0    1000000     0
en1        1500  <Link#12>   aa:bb:cc:dd:ee:00  1000     0    {e1}      900     0    3000000     0
utun4      1380  <Link#20>                       50     0     {u4}       40     0      70000     0
"""
PORTS = "Hardware Port: Ethernet\nDevice: en0\nEthernet Address: x\n\nHardware Port: Wi-Fi\nDevice: en1\n"
IOSTAT = "      disk0          disk4\n KB/t xfrs   MB   KB/t xfrs MB\n 43.30 98805 4000.0  5.44 665 3.5\n"


class NetAndDisk(unittest.TestCase):
    def test_pc_typeperf_numbers_and_no_hostname(self):
        io = C.win_io(C.parse_typeperf(TYPEPERF_OUT))
        self.assertEqual((io["net_in_mb_s"], io["net_out_mb_s"], io["disk_mb_s"]), (2.0, 1.0, 5.0))
        self.assertEqual(io["iface_kind"], "Ethernet")
        self.assertNotIn("BOX-1", json.dumps(io))
        self.assertIsNone(C.parse_typeperf("Error: no valid counters.\n"))

    def test_mac_per_interface_and_link_without_address(self):
        per = C.parse_netstat_if(NETSTAT_IF.format(e0=2000000, e1=10, u4=90000))
        self.assertEqual(per["utun4"], (90000, 70000))          # one column less: not shifted
        self.assertEqual(per["gif0"], (0, 0))
        self.assertNotIn("lo0", per)
        self.assertEqual(C.parse_hw_ports(PORTS), {"en0": "Ethernet", "en1": "Wi-Fi"})
        self.assertEqual(C.parse_iostat_mb(IOSTAT), 4003.5)

    def test_mac_traffic_enters_by_en0_ethernet(self):
        base = C.parse_netstat_if(NETSTAT_IF.format(e0=0, e1=0, u4=0))
        prev = {"at": NOW - 10, "net_if": {k: list(v) for k, v in base.items()}, "disk_mb": 4000.0}
        per = C.parse_netstat_if(NETSTAT_IF.format(e0=10 * 2**20, e1=2**20, u4=0))
        io = C.mac_io(per, 4003.5, prev, NOW, C.parse_hw_ports(PORTS))
        self.assertEqual((io["iface"], io["iface_kind"], io["net_in_mb_s"], io["disk_mb_s"]),
                         ("en0", "Ethernet", 1.1, 0.35))
        self.assertIsNone(C.mac_io(per, 4003.5, None, NOW, {}))

    def test_page_shows_numbers_and_the_interface(self):
        for m in ("'entra por ' + esc(ifTxt(io))", "disco I/O: ${io.disk_mb_s == null ? 'n/d'",
                  "entra por ${esc(ifTxt(t.io))}"):
            self.assertIn(m, HTML, m)


# ------------------------------------------------------------------ §3
import build as B  # noqa: E402
import razones as RZ  # noqa: E402
import web as W  # noqa: E402

# the exact line the Atención table showed on 2026-10-11 (jobs.jsonl), with the login name replaced
LEDGER_WHY = ("exit 1; última línea: manifest: 9,858 ok, 0 distintos, 570 faltan -> "
              "/Users/someuser/.enjambre/catalogo/backup-plus-manifest.tsv")


class PersonalPaths(unittest.TestCase):
    def test_home_paths(self):
        self.assertEqual(RZ.home_paths(LEDGER_WHY), "exit 1; última línea: manifest: 9,858 ok, 0 distintos, "
                         "570 faltan -> ~/.enjambre/catalogo/backup-plus-manifest.tsv")
        self.assertEqual(RZ.home_paths(r"exit 2: C:\Users\someuser\Documents\x.py"), r"exit 2: ~\Documents\x.py")
        self.assertEqual(RZ.home_paths(r"C:\\Users\\someuser\\x"), r"~\\x")        # JSON-doubled
        self.assertEqual(RZ.home_paths("C:/Users/someuser/x /home/bob/y"), "~/x ~/y")
        self.assertEqual(RZ.home_paths("exit 255"), "exit 255")
        self.assertIsNone(RZ.home_paths(None))

    def test_new_causes_are_written_without_the_name(self):
        why = RZ.explain({"status": "failed", "exit": 1}, "manifest -> /Users/someuser/.enjambre/m.tsv\n")
        self.assertNotIn("someuser", why)
        self.assertIn("~/.enjambre/m.tsv", why)

    def test_attention_table_cuts_old_ledger_rows(self):
        row = {"id": "catalogo-1", "node": "mac", "repo": "catalogo", "status": "failed", "exit": 1,
               "start": "2026-10-10T10:00:00-05:00", "reason": LEDGER_WHY}
        out = json.dumps(B.alerts([row], NOW), ensure_ascii=False)
        self.assertNotIn("someuser", out)
        self.assertIn("~/.enjambre/catalogo/backup-plus-manifest.tsv", out)

    def test_web_never_carries_the_name(self):
        for t in (LEDGER_WHY, "/Users/someuser: fallo"):
            self.assertNotIn("someuser", W.clean_why(t, set()) or "")
        self.assertNotIn("someuser", W.clean_log("ok\nwrote /Users/someuser/out.mp4\n", set()) or "")

    def test_page_cuts_it_too(self):
        self.assertIn(r"const deid = (t) => { let x = String(t ?? '').replace(/(?:\/Users|\/home)", HTML)


class HomeFolderIsNotAProject(unittest.TestCase):
    """Owner decision after 22 (tutorial 31 §5): shown as inicio-<node>; history.db untouched."""
    def test_by_cwd_and_by_home_name(self):
        self.assertEqual(C.shown_project("someuser", "/Users/someuser", "mac"), "inicio-mac")
        self.assertEqual(C.shown_project("someuser", "C:\\Users\\someuser", "win"), "inicio-win")
        self.assertEqual(C.shown_project("enjambre", "/Users/someuser/dev/enjambre", "mac"), "enjambre")
        self.assertEqual(C.shown_project("someuser", None, "mac", "someuser"), "inicio-mac")
        self.assertEqual(C.shown_project("farmacias", None, "mac", "someuser"), "farmacias")

    def test_projects_table_merges_under_the_alias(self):
        g = {"mac": {"snap": {"home_name": "someuser", "history": {"projects": {
            "someuser": {"sessions": 2}, "enjambre": {"sessions": 1}}}}, "cfg": {}}}
        out = B.projects_all_time(g, [])
        self.assertIn("inicio-mac", out)
        self.assertNotIn("someuser", out)
        self.assertEqual(out["inicio-mac"]["sessions"], 2)

    def test_live_session_card(self):
        p = {"win": {"sessions": [], "detached": []},
             "mac": {"sessions": [{"session_id": "s1", "name": "x", "project": "someuser",
                                   "cwd": "/Users/someuser", "updated": NOW - 5}], "detached": []}}
        live = LV.assemble(NODES, p, [], {}, {}, NOW)
        self.assertEqual(live["sesiones"][0]["proyecto"], "inicio-mac")

    def test_pc_thermal_reason_is_not_sudo(self):
        self.assertIn("osOf(n) === 'darwin' ? 'temperatura en grados: n/d (sin sudo)'", HTML)
        self.assertIn("Windows no la expone en esta placa", HTML)


if __name__ == "__main__":
    unittest.main()
