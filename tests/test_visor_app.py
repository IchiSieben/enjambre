"""17b: the visor as a Windows app (icon from the SVG, browser args, reload stamp)."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "visor"))


@unittest.skipUnless(sys.platform == "win32", "Windows shortcuts")
class VisorApp(unittest.TestCase):
    def setUp(self):
        import instalar
        self.I = instalar

    def test_icon_has_four_sizes_and_the_accent(self):
        with tempfile.TemporaryDirectory() as d:
            from PIL import Image
            p = self.I.build_icon(Path(d) / "v.ico")
            with Image.open(p) as im:
                self.assertEqual(im.info["sizes"], {(16, 16), (32, 32), (48, 48), (256, 256)})
                im.size = (256, 256); im.load()
                self.assertEqual(im.convert("RGB").getpixel((128, 128)), (0x2D, 0xD4, 0xBF))   # the dot
                self.assertEqual(im.convert("RGBA").getpixel((0, 0))[3], 0)                    # rounded corner

    def test_follow_builds_until_ctrl_c(self):
        from unittest import mock
        import build
        calls = []
        with mock.patch.object(self.I, "open_app", return_value=0),              mock.patch.object(build, "main", side_effect=lambda argv: calls.append(argv)),              mock.patch("time.sleep", side_effect=[None, KeyboardInterrupt]):
            self.assertEqual(self.I.follow(30), 0)
        self.assertEqual(calls, [["--no-push"], ["--no-push"]])

    def test_app_args_point_at_the_file(self):
        a = self.I.app_args(Path("C:/x/state/visor/visor.html"))
        self.assertIn('--app="file:///C:/x/state/visor/visor.html"', a)


class ReloadStamp(unittest.TestCase):
    def test_template_polls_the_stamp_and_keeps_a_button(self):
        html = (ROOT / "tools" / "visor" / "visor.html").read_text(encoding="utf-8")
        self.assertIn("visor-stamp.js?t=", html)
        self.assertIn('id="reload"', html)
        self.assertIn('rel="icon"', html)
        self.assertNotIn("location.reload(), 120000", html)   # no blind reload any more


class TemplateF3(unittest.TestCase):
    """F3 part 1 in the page: alerts grouped by cause, tests apart, web log or "solo en local",
    stale quota in grey with its age."""
    def setUp(self):
        self.h = (ROOT / "tools" / "visor" / "visor.html").read_text(encoding="utf-8")

    def test_markers(self):
        for m in ("id=\"live\"", "visor-live", "function countUp()", "prefers-reduced-motion: reduce", "D.every_min", "A.groups", "details class=\"tests\"", "D.job_logs", "Log solo en local", "qq.stale",
                  "kv('Por qué'"):
            self.assertIn(m, self.h, m)
        self.assertNotIn("ninguna statusline renderizó en los últimos 30 min", self.h)


class PageSize(unittest.TestCase):
    """F3 rule: the page the browser downloads (template + fonts + OS icons, no data) grows at most
    20 % over the F2 page (162 247 bytes at fde12b7). DECISIONES 227."""
    BASE = 162247
    LIVE_BLOCK = 16000
    SCHED_BLOCK = 6000   # prompt 20: priority chips, reasons, local controls, unmanaged list (~5.5 KB)
    RED_BLOCK = 1500     # prompt 21: `vía mac6` chip (§3) + real HANDOFF age per session (§4)
    POLISH_BLOCK = 3000  # prompt 22: reading ages, curve windows, externals' signal, PC net/disk

    def test_page_within_budget(self):
        import build
        n = len(build.render_html(None).encode("utf-8"))
        # + LIVE_BLOCK: prompts 18-19 (strip, running cards, node detail with gauges, sparklines and
        # processes), ~13 KB measured 2026-10-10, + ~2 KB reposo block (19 step 4, DECISIONES 268).
        # A separate allowance, not a new base (DECISIONES 252).
        self.assertLessEqual(n, int(self.BASE * 1.2) + self.LIVE_BLOCK + self.SCHED_BLOCK + self.RED_BLOCK
                             + self.POLISH_BLOCK,
                             "%d bytes" % n)

    def test_projects_table_markers(self):
        h = (ROOT / "tools" / "visor" / "visor.html").read_text(encoding="utf-8")
        for m in ("function usageCard()", "function heatCard()", "function localWork()", "data-cg=", "PCOLS", "id=\"ptb\"", "data-s=", "'pq', 'pd', 'pf', 'pt'", "--pc:", "function split("):
            self.assertIn(m, h, m)


class ModelFamilies(unittest.TestCase):
    """F3 part 4: family per model from the HF id, an icon for every family, web copy keeps only
    brand glyphs and the closed list of states."""
    def test_family_of_the_real_catalog_ids(self):
        import build
        for repo, key, fam in (("mlx-community/gemma-4-12B-it-qat-OptiQ-4bit", "llm_alt", "gemma"),
                               ("mlx-community/Qwen3.5-9B-MLX-4bit", "llm", "qwen"),
                               ("mlx-community/whisper-large-v3-turbo", "stt", "whisper"),
                               ("mlx-community/Nemotron-3-Diarization", "diar", "nvidia"),
                               ("mflux-community/z-image-turbo-mflux-q4", "zimage", "zimage"),
                               ("mlx-community/mini-coder-4b-OptiQ-4bit", "minicoder", "hf")):
            self.assertEqual(build.model_family(repo, key), fam, repo)
        icons = build.model_icons()
        self.assertEqual({"mf-" + f[1] for f in build.MODEL_FAMILIES}, set(icons))
        self.assertTrue(all("<svg" not in i["svg"] and "script" not in i["svg"] for i in icons.values()))

    def test_web_model_rows(self):
        import web as W
        b = {"nodes": {"mac": {"alive": True}}, "icons": {"mf-qwen": {"svg": '<path d="M0 0"/>', "name": "Qwen · Alibaba",
                                                                    "color": {"d": "#6950EF", "l": "#6950EF"}},
                                                         "mf-bad": {"svg": "<script>x</script>", "name": "x", "color": {"d": "#000", "l": "#000"}},
                                                         "python": {"svg": "<path/>", "name": "Python", "color": {"d": "#000", "l": "#000"}}},
             "models_inventory": {"mac": {"models": [{"key": "llm", "task": "text", "repo": "mlx-community/Qwen3.5-9B-MLX-4bit",
                                                      "status": "sirve", "nota": "ok", "family": "qwen"},
                                                     {"key": "x", "task": "text", "repo": "/Users/someone/w", "status": "genial"}]}}}
        pub = W.public_snapshot(b, set(), b"k" * 32, {})
        self.assertEqual(set(pub["icons"]), {"mf-qwen"})
        rows = pub["models_inventory"]["mac"]["models"]
        self.assertEqual((rows[0]["name"], rows[0]["status"], rows[0]["family"]), ("Qwen3.5-9B-MLX-4bit", "sirve", "qwen"))
        self.assertEqual((rows[1]["name"], rows[1]["status"]), ("w", None))

    def test_glyphs_pass_leak_check_but_names_do_not(self):
        """Path data ("1.01.1.223") looked like an IP and blocked every upload (F3 deploy)."""
        import build
        import web
        icons = build.model_icons()
        self.assertTrue(all(web.glyph_ok(v["svg"]) for v in icons.values()))
        self.assertFalse(web.glyph_ok('<path d="M0 0"/><script/>'))
        snap = {"nodes": {"mac": {"alive": True}}, "icons": {"mf-xx": {"name": "X", "svg": '<path d="M1.01.1.223 0 0z"/>', "color": {"d": "#000", "l": "#000"}}}}
        pub = json.loads(web.safe_payload(snap, set(), b"k", (), {}))
        self.assertIn("mf-xx", pub["icons"])
        snap["icons"]["mf-xx"]["name"] = "X 192.0.2.1"
        with self.assertRaises(web.LeakError):
            web.safe_payload(snap, set(), b"k", (), {})


if __name__ == "__main__":
    unittest.main()
