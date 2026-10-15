"""The publication audit (tools/publicar/auditar.py): what it rejects, what it lets through."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools" / "publicar"))
import auditar as A  # noqa: E402

KEY = b"test-key"
HASHES = {A.word_hash(KEY, w) for w in ("secreto", "cliente uno")}


def rules(line: str) -> list:
    return A.line_findings(line, KEY, HASHES)


class Rules(unittest.TestCase):
    def test_addresses(self):
        self.assertIn("ip", rules("ssh 10.1" ".2.3"))
        self.assertIn("ip", rules("fe80::1c" "2a:3b4c:5d6e:7f80%en0"))
        self.assertIn("ip", rules("2800:200" ":e840:1234::5"))
        for ok in ("127.0.0.1", "0.0.0.0", "192.0.2.7", "255.255.255.0", "v1.2.3.4", "::1", "nums[2::3]",
                   "12:30:45", "2001:db8::42"):
            self.assertNotIn("ip", rules(ok), ok)

    def test_svg_path_data_is_not_an_address(self):
        self.assertEqual(rules('<path d="M8.489 1.921.96.96 0 0 1 2 3"/>'), [])

    def test_hardware(self):
        self.assertIn("mac", rules("ether 3c:22:fb" ":01:02:03"))
        self.assertIn("mac", rules("Physical Address: 3C-22-FB" "-01-02-03"))
        self.assertNotIn("mac", rules("aa:bb:cc:dd:ee:01"))
        self.assertIn("serial", rules("Serial Number (system)" ": C02XK1ABCD12"))
        self.assertIn("host", rules("DESKTOP" "-AB12CD3"))
        self.assertIn("host", rules("ping minimac" ".local"))
        self.assertNotIn("host", rules(".claude/settings.local.json"))

    def test_homes(self):
        self.assertIn("home", rules("/Users" "/jdoe/Documents"))
        self.assertIn("home", rules(r"C:\Users" "\\jdoe\\x"))
        self.assertIn("home", rules("C:\\\\Users\\\\" "jdoe\\\\x"))
        for ok in ("/Users/<usuario>/x", r"C:\Users\<usuario>\x", "/Users/$USER/x", "orquestador/home/.claude"):
            self.assertNotIn("home", rules(ok), ok)

    def test_emails_and_keys(self):
        self.assertIn("email", rules("contact: someone" "@gmail.com"))
        self.assertNotIn("email", rules("devicon@v2.16.0/icons"))
        self.assertNotIn("email", rules("+@unittest.skipIf(x)"))     # a diff line, not an address
        self.assertNotIn("email", rules("IchiSieben@users.noreply.github.com"))
        self.assertIn("secret", rules("token ghp_" + "a" * 36))

    def test_words_by_keyed_hash_on_token_boundaries(self):
        self.assertIn("word", rules("el repo-secreto"))
        self.assertIn("word", rules("is_secreto_path"))
        self.assertIn("word", rules("para Cliente Uno"))
        self.assertNotIn("word", rules("secretos y clientes"))
        self.assertEqual(A.line_findings("el repo-secreto", b"other-key", HASHES), [])


class Tree(unittest.TestCase):
    def test_unreviewed_binary_and_a_name(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "a.png").write_bytes(b"\x89PNG\0\0")
            (Path(d) / "notas-10.9" ".8.7.md").write_text("ok\n", encoding="utf-8")
            found = {(r, rule) for r, _, rule in A.audit_tree(Path(d))}
        self.assertIn(("a.png", "binary"), found)
        self.assertIn(("notas-10.9" ".8.7.md", "name:ip"), found)

    def test_no_key_never_green(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {A.KEY_ENV: ""}), \
                mock.patch.object(A, "KEY_FILE", Path(d) / "none"):
            self.assertEqual(A.main([d]), 2)

    def test_wrong_key_never_green(self):
        # a mistyped CI secret matches no word: without the witness it would pass as green
        with tempfile.TemporaryDirectory() as d:
            words = Path(d) / "prohibidas.txt"
            words.write_text(A.word_hash(KEY, "secreto") + "\n", encoding="utf-8")
            with mock.patch.dict(os.environ, {A.KEY_ENV: KEY.decode()}), mock.patch.object(A, "WORDS_FILE", words):
                self.assertEqual(A.main([d]), 2)
                words.write_text(words.read_text(encoding="utf-8") + A.word_hash(KEY, A.CANARY) + "\n",
                                 encoding="utf-8")
                self.assertEqual(A.main([d]), 0)
            with mock.patch.dict(os.environ, {A.KEY_ENV: "other-key"}), mock.patch.object(A, "WORDS_FILE", words):
                self.assertEqual(A.main([d]), 2)


if __name__ == "__main__":
    unittest.main()
