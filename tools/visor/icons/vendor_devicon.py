#!/usr/bin/env python3
"""vendor_devicon.py — the official multicolour logos the visor shows, vendored from devicon (MIT).
Run by hand when the set changes:  python -I tools/visor/icons/vendor_devicon.py

The Landing paints every logo in one brand colour (its design choice); the owner asked the visor
for the official colour SVGs. They live here, not in the Landing. A logo that is black on the dark
theme (three.js) or has no devicon version (Lucide pictograms: scraping, etl...) is left out and
the visor falls back to the Landing's brand-colour glyph.

Downloads are untrusted: each file is parsed only after refusing DOCTYPE/ENTITY, then rebuilt from
an element/attribute allowlist (no script, no foreignObject, no event handlers, no external href).
Every id and url(#id) is prefixed with the icon key, so two inline SVGs on one page cannot take
each other's gradients. Sources and licence: SOURCES.json.
"""
from __future__ import annotations

import json
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASE = "https://cdn.jsdelivr.net/gh/devicons/devicon@v2.16.0/icons/{0}/{0}-{1}.svg"
LICENCE = "MIT, devicon (https://github.com/devicons/devicon)"
# visor icon id (the Landing sprite id or os-*) -> (devicon name, variant)
MAP = {
    "python": ("python", "original"), "javascript": ("javascript", "original"),
    # astro: its "A" is black and vanishes on the dark theme; the brand-purple glyph stays
    "githubactions": ("githubactions", "original"),
    "sqlite": ("sqlite", "original"), "duckdb": ("duckdb", "original"),
    # linux / os-linux: Tux is ~200 KB of paths; the brand-colour glyph stays
    "os-windows": ("windows11", "original"), "apacheecharts": ("apacheecharts", "original"),
    "beautifulsoup": ("beautifulsoup", "original"), "pandas": ("pandas", "original"),
    "numpy": ("numpy", "original"), "docker": ("docker", "original"), "git": ("git", "original"),
    "postgresql": ("postgresql", "original"), "mysql": ("mysql", "original"),
    "react": ("react", "original"), "typescript": ("typescript", "original"),
    "streamlit": ("streamlit", "original"), "tailwindcss": ("tailwindcss", "original"),
    "html5": ("html5", "original"), "css3": ("css3", "original"), "php": ("php", "original"),
    "jupyter": ("jupyter", "original"), "scikitlearn": ("scikitlearn", "original"),
    "r": ("r", "original"), "vitejs": ("vitejs", "original"), "nodedotjs": ("nodejs", "original"),
    "blender": ("blender", "original"), "oracle": ("oracle", "original"),
}
SVG = "{http://www.w3.org/2000/svg}"
ELEMENTS = {"svg", "g", "path", "circle", "ellipse", "rect", "polygon", "polyline", "line", "defs",
            "linearGradient", "radialGradient", "stop", "clipPath", "mask", "use", "title"}
ATTRS = {"d", "fill", "fill-rule", "clip-rule", "fill-opacity", "stroke", "stroke-width",
         "stroke-linecap", "stroke-linejoin", "stroke-miterlimit", "stroke-opacity", "opacity",
         "transform", "cx", "cy", "r", "rx", "ry", "x", "y", "x1", "x2", "y1", "y2", "width",
         "height", "points", "offset", "stop-color", "stop-opacity", "gradientUnits",
         "gradientTransform", "fx", "fy", "spreadMethod", "clip-path", "mask", "id", "viewBox",
         "style", "href", "maskUnits", "maskContentUnits", "clipPathUnits", "class"}
STYLE_OK = re.compile(r"^[\w\s:;#%.,()\-]*$")


def clean(raw: str, key: str) -> tuple[str, str]:
    """(viewBox, inner markup) of a sanitised SVG, ids prefixed with `vi-<key>-`."""
    if re.search(r"<!(DOCTYPE|ENTITY)", raw, re.I):
        raise ValueError("doctype/entity refused")
    root = ET.fromstring(raw)
    if root.tag != SVG + "svg":
        raise ValueError("not an svg")
    vb = root.get("viewBox") or "0 0 128 128"
    if not re.fullmatch(r"[\d.\s-]+", vb):
        raise ValueError("bad viewBox")
    pre = "vi-%s-" % re.sub(r"[^\w-]", "", key)

    def fix_refs(v: str) -> str:
        return re.sub(r"url\(\s*#([\w.-]+)\s*\)", lambda m: "url(#%s%s)" % (pre, m.group(1)), v)

    def emit(el) -> str:
        tag = el.tag.replace(SVG, "")
        if tag not in ELEMENTS or tag == "svg":
            return ""
        attrs = []
        for k, v in el.attrib.items():
            k = k.replace("{http://www.w3.org/1999/xlink}", "")
            if k not in ATTRS or k == "viewBox":
                continue
            if k == "id":
                v = pre + v
            elif k == "href":
                if not v.startswith("#"):
                    continue                     # no external references
                v = "#" + pre + v[1:]
            elif k == "style" and not STYLE_OK.match(v):
                continue
            if "url(" in v:
                if re.search(r"url\(\s*[^#\s]", v):
                    continue
                v = fix_refs(v)
            attrs.append('%s="%s"' % (k, v.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;")))
        inner = "".join(emit(c) for c in el)
        if tag == "title":
            return ""
        return "<%s%s>%s</%s>" % (tag, "".join(" " + a for a in attrs), inner, tag)

    return vb, "".join(emit(c) for c in root)


def main(keys: list[str]) -> int:
    sources = {}
    for key in keys or sorted(MAP):
        name, variant = MAP[key]
        url = BASE.format(name, variant)
        try:
            with urllib.request.urlopen(url, timeout=15) as r:
                raw = r.read(200_000).decode("utf-8")
        except Exception as e:
            print("skip %-16s %s" % (key, type(e).__name__))
            continue
        try:
            vb, inner = clean(raw, key)
        except (ValueError, ET.ParseError) as e:
            print("refuse %-14s %s" % (key, e))
            continue
        (HERE / (key + ".svg")).write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="%s">%s</svg>\n' % (vb, inner), encoding="utf-8", newline="\n")
        sources[key] = {"file": key + ".svg", "source": url, "licence": LICENCE}
        print("ok     %-16s %6d bytes" % (key, len(inner)))
    old = {}
    try:
        old = json.loads((HERE / "SOURCES.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    old.update(sources)
    (HERE / "SOURCES.json").write_text(json.dumps(dict(sorted(old.items())), indent=1) + "\n", encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
