#!/usr/bin/env python3
"""capturar_demo.py — the public captures and video of the visor, from DEMO MODE only.

    python3 tools/visor/capturar_demo.py [OUT_DIR]      # default docs/img/

Builds the synthetic swarm (demo.make), keeps live.js moving (demo.serve in a thread), opens the
page in headless Chromium (Playwright) and writes:
    visor-demo.png        the page as it opens (cover of the README)
    visor-nodos.png       one node card: gauges, disks, jobs
    visor-trabajos.png    what is running now (jobs + the external render)
    visor-telemetria.png  a node's detail: gauges and the last hour's curves
    visor-demo.mp4        ~25 s walk through the page (webm when ffmpeg is missing)
Real-environment captures are never published (docs/CAPTURAS.md). Needs: pip install playwright
&& python3 -m playwright install chromium; ffmpeg for the mp4.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
sys.path.insert(0, str(HERE))
import demo  # noqa: E402

VIEW = {"width": 1440, "height": 900}


def no_console() -> dict:
    """Windows: no console window flashes for ffmpeg (same idiom as presence.py)."""
    return {"creationflags": 0x08000000} if sys.platform == "win32" else {}


def shot(loc, path: Path, tries: int = 5) -> None:
    for _ in range(tries):          # the live repaint (every few s) can replace the node mid-shot
        try:
            loc.screenshot(path=str(path), timeout=4000)
            return
        except Exception as e:      # noqa: BLE001 — Playwright raises several kinds here
            print("reintento", path.name, type(e).__name__)
    raise SystemExit(f"no pude capturar {path.name}")


def main(argv=None) -> int:
    from playwright.sync_api import sync_playwright
    argv = list(sys.argv[1:] if argv is None else argv)
    out = Path(argv[0]) if argv else REPO / "docs" / "img"
    out.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="enjambre-demo-"))
    demo.make(work)
    threading.Thread(target=demo.serve, args=(work,), daemon=True).start()
    page_url = (work / "visor.html").as_uri()
    vids = Path(tempfile.mkdtemp(prefix="enjambre-video-"))
    with sync_playwright() as p:
        b = p.chromium.launch()
        ctx = b.new_context(viewport=VIEW, device_scale_factor=2, color_scheme="dark")
        pg = ctx.new_page()
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto(page_url)
        pg.wait_for_timeout(3000)
        pg.screenshot(path=str(out / "visor-demo.png"))
        pg.add_style_tag(content=".top{position:static!important}")
        shot(pg.locator(".card", has=pg.locator('.tchips[data-tn="mac"]')).first, out / "visor-nodos.png")
        shot(pg.locator("#corriendo"), out / "visor-trabajos.png")
        pg.locator('[data-tdet="mac"]').first.click()
        pg.wait_for_timeout(800)
        shot(pg.locator("#teledet .card").first, out / "visor-telemetria.png")
        ctx.close()
        # the video: its own context, 1x, so the file stays small
        vctx = b.new_context(viewport=VIEW, color_scheme="dark", record_video_dir=str(vids), record_video_size=VIEW)
        vp = vctx.new_page()
        vp.goto(page_url)
        vp.wait_for_timeout(4000)
        for y in (500, 1100, 1700):
            vp.mouse.wheel(0, 600)
            vp.wait_for_timeout(2500)
        vp.evaluate("window.scrollTo({top: 0, behavior: 'smooth'})")
        vp.wait_for_timeout(1500)
        vp.locator('[data-tdet="mac"]').first.click()
        vp.wait_for_timeout(6000)
        vp.locator('[data-tdet="pc"]').first.click()
        vp.wait_for_timeout(4000)
        vctx.close()
        b.close()
    webm = next(vids.glob("*.webm"))
    if shutil.which("ffmpeg"):
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(webm), "-c:v", "libx264", "-pix_fmt", "yuv420p",
                        "-crf", "28", "-movflags", "+faststart", "-an", str(out / "visor-demo.mp4")], check=True, **no_console())
    else:
        shutil.copyfile(webm, out / "visor-demo.webm")
    print("errores de página:", errs or "ninguno")
    for f in sorted(out.glob("visor-*")):
        print(f.name, f.stat().st_size)
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main())
