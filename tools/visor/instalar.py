"""The local visor as a Windows app (17b).

    python enjambre.py visor --install     # icon + "Visor enjambre.lnk" on the Desktop and Start menu
    python enjambre.py visor               # open it now, same window the shortcut opens
    python enjambre.py visor --follow      # open it and rebuild every 30 s until Ctrl+C (F3 §17)

1. Rasterizes tools/visor/icon.svg into state/visor/visor.ico (16/32/48/256). The SVG is read with
   the standard library and drawn with Pillow at 8x, then downsampled: no browser, no cairo. Only
   the three shapes the icon uses are understood (rect with rx, polygon with stroke, circle).
2. Finds Brave (App Paths in the registry, then the usual folders); if it is not there, Edge.
3. Writes the shortcut with WScript.Shell (pywin32): target = the browser, arguments =
   --app=file:///<state/visor/visor.html>, icon = visor.ico, "Iniciar en" = the visor folder.
   --app gives a window without tabs or address bar; the taskbar shows the page favicon, which
   is the same hexagon (inline in visor.html).
"""
from __future__ import annotations

import argparse
import os
import sys
import winreg
import xml.etree.ElementTree as ET
from pathlib import Path

from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
SVG = HERE / "icon.svg"
OUT = REPO / "state" / "visor"
ICO = OUT / "visor.ico"
HTML = OUT / "visor.html"
NAME = "Visor enjambre"
SIZES = (16, 32, 48, 256)
SS = 8                                   # supersampling factor

BROWSERS = (
    ("brave.exe", (r"BraveSoftware\Brave-Browser\Application\brave.exe",)),
    ("msedge.exe", (r"Microsoft\Edge\Application\msedge.exe",)),
)


def _rgb(c: str) -> tuple:
    c = c.lstrip("#")
    return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4)) + (255,)


def render_svg(size: int, svg: Path = SVG) -> Image.Image:
    """One square RGBA image of the icon at `size` px."""
    root = ET.parse(svg).getroot()
    vb = [float(x) for x in root.get("viewBox", "0 0 256 256").split()]
    big = size * SS
    k = big / vb[2]
    im = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    for el in root.iter():
        tag = el.tag.split("}")[-1]
        fill = el.get("fill")
        if tag == "rect":
            x, y, w, h = (float(el.get(a, 0)) * k for a in ("x", "y", "width", "height"))
            d.rounded_rectangle((x, y, x + w - 1, y + h - 1), radius=float(el.get("rx", 0)) * k, fill=_rgb(fill))
        elif tag == "polygon":
            pts = [tuple(float(v) * k for v in p.split(",")) for p in el.get("points").split()]
            if fill and fill != "none":
                d.polygon(pts, fill=_rgb(fill))
            if el.get("stroke"):
                w = round(float(el.get("stroke-width", 1)) * k)
                d.line(pts + pts[:2], fill=_rgb(el.get("stroke")), width=w, joint="curve")
        elif tag == "circle":
            cx, cy, r = (float(el.get(a)) * k for a in ("cx", "cy", "r"))
            d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=_rgb(fill))
    return im.resize((size, size), Image.LANCZOS)


def build_icon(path: Path = ICO) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    imgs = [render_svg(s) for s in SIZES]
    imgs[-1].save(path, format="ICO", sizes=[(s, s) for s in SIZES], append_images=imgs[:-1])
    return path


def find_browser() -> Path | None:
    """Brave first, Edge second: App Paths (HKCU, HKLM, WOW6432Node), then Program Files."""
    keys = ((winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\App Paths"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths"))
    roots = [os.environ.get(v) for v in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA")]
    for exe, rels in BROWSERS:
        for hive, base in keys:
            try:
                with winreg.OpenKey(hive, base + "\\" + exe) as kk:
                    p = Path(winreg.QueryValue(kk, None).strip('"'))
                if p.is_file():
                    return p
            except OSError:
                pass
        for r in filter(None, roots):
            for rel in rels:
                p = Path(r) / rel
                if p.is_file():
                    return p
    return None


def app_args(html: Path = HTML) -> str:
    return '--app="%s" --window-size=1280,860' % html.resolve().as_uri()


def shortcut_dirs() -> list[Path]:
    import win32com.client
    sh = win32com.client.Dispatch("WScript.Shell")
    return [Path(sh.SpecialFolders("Desktop")), Path(sh.SpecialFolders("Programs"))]


def make_shortcut(lnk: Path, target: Path, args: str, icon: Path, workdir: Path) -> None:
    import win32com.client
    sc = win32com.client.Dispatch("WScript.Shell").CreateShortcut(str(lnk))
    sc.TargetPath = str(target)
    sc.Arguments = args
    sc.IconLocation = "%s,0" % icon
    sc.WorkingDirectory = str(workdir)
    sc.Description = "Visor del enjambre (copia local, se recarga sola)"
    sc.Save()


def install() -> int:
    if not HTML.is_file():
        print("no existe %s: corre antes `python tools/visor/build.py`" % HTML, file=sys.stderr)
        return 2
    browser = find_browser()
    if not browser:
        print("no encontré Brave ni Edge", file=sys.stderr)
        return 2
    icon = build_icon()
    print("icono   %s (%s px)" % (icon, "/".join(map(str, SIZES))))
    print("destino %s %s" % (browser, app_args()))
    for folder in shortcut_dirs():
        lnk = folder / (NAME + ".lnk")
        make_shortcut(lnk, browser, app_args(), icon, OUT)
        print("acceso  %s" % lnk)
    return 0


def open_app() -> int:
    browser = find_browser()
    if not browser or not HTML.is_file():
        print("falta el navegador o %s" % HTML, file=sys.stderr)
        return 2
    os.startfile(str(browser), arguments=app_args(), cwd=str(OUT))   # a GUI app: no console, no child to wait for
    return 0


def follow(every_s: int = 30) -> int:
    """Open the app and rebuild the snapshot every `every_s` seconds; the page's "en vivo" poll
    (visor-stamp.js) reloads it after each build. In-process, no push to the nodes."""
    import time
    import build
    rc = open_app()
    while rc == 0:
        t = time.time()
        try:
            build.main(["--no-push"])
        except Exception as e:                        # one bad round must not end the follow
            print("build: %s" % e, file=sys.stderr)
        try:
            time.sleep(max(1.0, every_s - (time.time() - t)))
        except KeyboardInterrupt:
            return 0
    return rc


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--install", action="store_true", help="icon + shortcuts on the Desktop and Start menu")
    ap.add_argument("--icon-only", action="store_true", help="only (re)build state/visor/visor.ico")
    ap.add_argument("--follow", action="store_true", help="open it and rebuild every 30 s until Ctrl+C")
    a = ap.parse_args(argv)
    if a.icon_only:
        print(build_icon())
        return 0
    return install() if a.install else follow() if a.follow else open_app()


if __name__ == "__main__":
    raise SystemExit(main())
