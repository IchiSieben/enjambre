"""ES <-> EN parity of the tutorials (prompt 17a): an English lesson is the same lesson.

For every docs/tutorial/<nn-slug>.en.md next to its <nn-slug>.md it compares
  - the `lesson:` frontmatter block (identical) and `lang: en` in the English one;
  - the sequence of heading levels outside code blocks (#, ##, ### ... in the same order);
  - the number of fenced code blocks, and each block's content (commands are not translated);
  - the multiset of numbers in the whole file, normalizing the decimal comma (1,5 == 1.5).
Exit 0 when every pair matches, 1 otherwise (one line per difference).

    python tools/paridad.py                 # every pair under docs/tutorial
    python tools/paridad.py 25-trabajos-largos   # one lesson (stem or path)
    python tools/paridad.py --missing       # also fail on a `hecho` lesson without its .en.md
"""
from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TUT = ROOT / "docs" / "tutorial"
FENCE = re.compile(r"^(\s*)(`{3,}|~{3,})")
HEADING = re.compile(r"^(#{1,6})\s")
NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
README_ROW = re.compile(r"^\|\s*\[(\d\d)[^\]]*\]\(([^)]+)\)\s*\|.*\|\s*([^|]*?)\s*\|\s*$")


def split_front(text: str) -> tuple[str, str]:
    """(frontmatter without the --- lines, body). No frontmatter: ('', text)."""
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end != -1:
            return text[4:end], text[end + 5:]
    return "", text


def lesson_block(front: str) -> str:
    """The `lesson:` key and its indented lines, as written."""
    out, inside = [], False
    for line in front.splitlines():
        if line.startswith("lesson:"):
            inside = True
        elif inside and line and not line.startswith((" ", "\t")):
            inside = False
        if inside:
            out.append(line.rstrip())
    return "\n".join(out)


def structure(body: str) -> tuple[list, list]:
    """(heading levels outside code, code block contents)."""
    heads, blocks, cur, fence = [], [], None, None
    for line in body.splitlines():
        m = FENCE.match(line)
        if cur is None and m:
            cur, fence = [], m.group(2)[0] * len(m.group(2))
            continue
        if cur is not None:
            if m and m.group(2).startswith(fence):
                blocks.append("\n".join(cur).strip())
                cur = None
            else:
                cur.append(line.rstrip())
            continue
        h = HEADING.match(line)
        if h:
            heads.append(len(h.group(1)))
    return heads, blocks


def numbers(text: str) -> Counter:
    return Counter(n.replace(",", ".") for n in NUMBER.findall(text))


def compare(es_text: str, en_text: str) -> list[str]:
    errs = []
    es_front, es_body = split_front(es_text)
    en_front, en_body = split_front(en_text)
    if lesson_block(es_front) != lesson_block(en_front):
        errs.append("frontmatter `lesson:` differs")
    if not re.search(r"^lang:\s*en\s*$", en_front, re.M):
        errs.append("frontmatter has no `lang: en`")
    es_h, es_b = structure(es_body)
    en_h, en_b = structure(en_body)
    if es_h != en_h:
        first = next((i for i, (a, b) in enumerate(zip(es_h, en_h)) if a != b), min(len(es_h), len(en_h)))
        errs.append(f"headings differ: es {len(es_h)}, en {len(en_h)}, first difference at heading #{first + 1}")
    if len(es_b) != len(en_b):
        errs.append(f"code blocks: es {len(es_b)}, en {len(en_b)}")
    else:
        for i, (a, b) in enumerate(zip(es_b, en_b), 1):
            if a != b:
                errs.append(f"code block {i} differs (commands and outputs are not translated)")
    es_n, en_n = numbers(es_body), numbers(en_body)
    if es_n != en_n:
        only_es = sorted((es_n - en_n).elements())
        only_en = sorted((en_n - es_n).elements())
        errs.append(f"numbers differ: only es {only_es[:12]}, only en {only_en[:12]}")
    return errs


def done_lessons(readme: Path = TUT / "README.md") -> list[str]:
    """Stems of the README rows whose status starts with `hecho`."""
    out = []
    for line in readme.read_text(encoding="utf-8").splitlines():
        m = README_ROW.match(line)
        if m and m.group(3).lower().startswith("hecho"):
            out.append(Path(m.group(2)).stem)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("lessons", nargs="*", help="stems or paths; default: every pair")
    ap.add_argument("--missing", action="store_true", help="fail on a `hecho` lesson without .en.md")
    a = ap.parse_args(argv)
    stems = [Path(x).name.removesuffix(".md").removesuffix(".en") for x in a.lessons] or \
        sorted(p.name[:-len(".en.md")] for p in TUT.glob("[0-9]*.en.md"))
    bad = 0
    for stem in stems:
        es, en = TUT / f"{stem}.md", TUT / f"{stem}.en.md"
        if not es.exists() or not en.exists():
            print(f"{stem}: missing {'es' if not es.exists() else 'en'} file")
            bad += 1
            continue
        errs = compare(es.read_text(encoding="utf-8"), en.read_text(encoding="utf-8"))
        for e in errs:
            print(f"{stem}: {e}")
        bad += bool(errs)
        if not errs:
            print(f"{stem}: ok")
    if a.missing:
        for stem in done_lessons():
            if not (TUT / f"{stem}.en.md").exists():
                print(f"{stem}: `hecho` without .en.md")
                bad += 1
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
