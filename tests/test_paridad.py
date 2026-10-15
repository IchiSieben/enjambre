"""tools/paridad.py (prompt 17a): every docs/tutorial/<nn-slug>.en.md is the same lesson as its
Spanish twin: same `lesson:` block, `lang: en`, same heading levels in order, same code blocks,
same numbers (decimal comma normalized). The last test runs it over every pair in the repo."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import paridad as R  # noqa: E402

ES = """---
lesson:
  route: build-stack
  order: 24
  level: 3
---

# 25 — Trabajos largos

## Objetivo

Un trabajo de 3,5 h con tope de US$2.

```
PS> python enjambre.py run --detach --budget 2
```

## Qué salió mal de verdad

El 7 de octubre falló 3 veces.
"""
EN = """---
lesson:
  route: build-stack
  order: 24
  level: 3
lang: en
---

# 25 — Long jobs

## Goal

A 3.5 h job capped at US$2.

```
PS> python enjambre.py run --detach --budget 2
```

## What actually went wrong

On October 7 it failed 3 times.
"""


class Compare(unittest.TestCase):
    def test_a_faithful_pair_passes(self):
        self.assertEqual(R.compare(ES, EN), [])

    def test_each_rule_catches_its_difference(self):
        cases = {
            "frontmatter `lesson:` differs": EN.replace("order: 24", "order: 25"),
            "frontmatter has no `lang: en`": EN.replace("lang: en\n", ""),
            "headings differ": EN.replace("## Goal", "### Goal"),
            "code blocks": EN.replace("```\nPS> python enjambre.py run --detach --budget 2\n```\n", ""),
            "code block 1 differs": EN.replace("--budget 2\n```", "--budget 3\n```"),
            "numbers differ": EN.replace("failed 3 times", "failed three times"),
        }
        for want, en in cases.items():
            errs = R.compare(ES, en)
            self.assertTrue(any(e.startswith(want) for e in errs), (want, errs))

    def test_headings_inside_code_do_not_count(self):
        es = ES.replace("PS> python", "# a shell comment\nPS> python")
        en = EN.replace("PS> python", "# a shell comment\nPS> python")
        self.assertEqual(R.compare(es, en), [])

    def test_done_lessons_read_the_readme(self):
        done = R.done_lessons()
        self.assertIn("25-trabajos-largos", done)
        self.assertNotIn("10-remote-ssh", done)          # pendiente
        self.assertNotIn("03-ssh", done)                 # a medias


class Repo(unittest.TestCase):
    def test_every_english_lesson_in_the_repo_matches_its_twin(self):
        self.assertEqual(R.main([]), 0)


if __name__ == "__main__":
    unittest.main()
