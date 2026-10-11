"""Prompt 21 §1: nodes are reached by ssh alias, never by a LAN address written in config or code.
The router's DHCP reservation and ~/.ssh/config are the only places an address may live."""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LAN = re.compile(r"\b(?:192\.168|10|172\.(?:1[6-9]|2\d|3[01]))\.\d{1,3}\.\d{1,3}\b")
WATCHED = ["nodes.toml", "routing.toml", "tools/smb-mount.sh", "worker.sh", "config/*.toml",
           "config/*.plist", "agent/*.py", "agent/*.sh", "*.py", "tools/*.sh", "tools/*.ps1",
           "tools/visor/*.py"]


class NoFixedIps(unittest.TestCase):
    def test_no_lan_address_in_config_or_code(self):
        hits = []
        for pat in WATCHED:
            for p in ROOT.glob(pat):
                for n, line in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                    if LAN.search(line):
                        hits.append(f"{p.relative_to(ROOT)}:{n}: {line.strip()[:100]}")
        self.assertEqual(hits, [], "use the ssh alias (mac, mac6), not an address")


if __name__ == "__main__":
    unittest.main()
