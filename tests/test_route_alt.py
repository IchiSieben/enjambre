"""Prompt 21 §3: when `mac` stops answering (WORK VPN captures the LAN /24) and `mac6` answers,
watch switches the node to its host_alt and says so; when `mac` is back, it switches back."""
import json
import logging
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import presence as P

LOG = logging.getLogger("test.route")
T0 = 1_760_000_000.0


class RouteRound(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.route_json = Path(self.tmp.name) / "route.json"
        self.p = mock.patch.object(P, "ROUTE_JSON", self.route_json)
        self.p.start()
        P._ALT_TRIED.clear()
        self.nodes = {"mac": {"host": "mac", "host_alt": "mac6"}}
        self.sent, self.probed = [], []

    def tearDown(self):
        self.p.stop()
        self.tmp.cleanup()

    def round(self, primary_level, alt_level, t):
        state = {"nodes": {"mac": {"level": primary_level}}}
        route = P.route_round(self.nodes, state, t, lambda h: self.probed.append(h) or alt_level,
                              lambda *m: self.sent.append(m), LOG)
        return state["nodes"]["mac"], route

    def test_primary_silent_alt_answers_switches_and_warns(self):
        st, route = self.round("down", "ready", T0)
        self.assertEqual(route["mac"]["alias"], "mac6")
        self.assertEqual((st["level"], st["via"]), ("ready", "mac6"))
        self.assertEqual(json.loads(self.route_json.read_text())["mac"]["alias"], "mac6")
        self.assertEqual(len(self.sent), 1)
        self.assertIn("mac6", self.sent[0][0])

    def test_alt_rechecked_at_most_once_a_minute(self):
        self.round("down", "ready", T0)
        self.round("down", "ready", T0 + 30)
        self.round("down", "ready", T0 + 60)
        self.assertEqual(self.probed, ["mac6", "mac6"])
        self.assertEqual(len(self.sent), 1)      # notified once, not every round

    def test_primary_back_switches_back(self):
        self.round("down", "ready", T0)
        st, route = self.round("ready", "ready", T0 + 30)
        self.assertEqual(route, {})
        self.assertNotIn("via", st)
        self.assertEqual(json.loads(self.route_json.read_text()), {})
        self.assertIn("vuelve", self.sent[-1][0])

    def test_node_really_off_tries_alt_once_a_minute_and_writes_nothing(self):
        for i in range(4):
            self.round("down", "down", T0 + 30 * i)
        self.assertEqual(self.probed, ["mac6", "mac6"])
        self.assertFalse(self.route_json.exists())
        self.assertEqual(self.sent, [])

    def test_node_without_host_alt_is_untouched(self):
        self.nodes = {"mac": {"host": "mac"}}
        st, route = self.round("down", "ready", T0)
        self.assertEqual((route, self.probed, st["level"]), ({}, [], "down"))


class WithTick(RouteRound):
    def test_switched_node_does_not_page_down_every_few_rounds(self):
        # the via-level ("ready" through mac6) must not be what tick debounces the primary against
        prev, notes = {}, []
        for i in range(8):
            st = P.tick(self.nodes, prev, lambda h: "down", T0 + 30 * i, lambda *m: notes.append(m), LOG)
            P.route_round(self.nodes, st, T0 + 30 * i, lambda h: "ready", lambda *m: notes.append(m), LOG)
            prev = st
        self.assertEqual(st["nodes"]["mac"]["level"], "ready")
        self.assertEqual(st["nodes"]["mac"]["via"], "mac6")
        self.assertEqual([n[0] for n in notes], ["enjambre: mac por mac6"])   # once, nothing after
        st = P.tick(self.nodes, prev, lambda h: "ready", T0 + 300, lambda *m: notes.append(m), LOG)
        P.route_round(self.nodes, st, T0 + 300, lambda h: "ready", lambda *m: notes.append(m), LOG)
        self.assertNotIn("via", st["nodes"]["mac"])
        self.assertIn("vuelve", notes[-1][0])


class ApplyRoute(unittest.TestCase):
    def test_alt_applied_then_primary_restored(self):
        nodes = {"mac": {"host": "mac", "host_alt": "mac6"}, "win": {"host": "local"}}
        P.apply_route(nodes, {"mac": {"alias": "mac6"}})
        self.assertEqual((nodes["mac"]["host"], nodes["mac"]["host_fallback"]), ("mac6", True))
        P.apply_route(nodes, {})
        self.assertEqual(nodes["mac"]["host"], "mac")
        self.assertNotIn("host_override", nodes["mac"])
        self.assertEqual(nodes["win"]["host"], "local")

    def test_env_override_wins(self):
        nodes = {"mac": {"host": "macX", "host_alt": "mac6", "host_override": True}}
        P.apply_route(nodes, {"mac": {"alias": "mac6"}})
        self.assertEqual(nodes["mac"]["host"], "macX")

    def test_route_alias_must_be_the_declared_alt(self):
        nodes = {"mac": {"host": "mac", "host_alt": "mac6"}}
        P.apply_route(nodes, {"mac": {"alias": "evil; rm -rf ~"}})
        self.assertEqual(nodes["mac"]["host"], "mac")


class JobHost(unittest.TestCase):
    def test_job_launched_through_mac_is_reached_through_mac6_while_switched(self):
        with mock.patch.object(P, "read_route", return_value={"mac": {"alias": "mac6"}}), \
                mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ENJAMBRE_HOST_MAC", None)
            self.assertEqual(P.job_host({"node": "mac", "host": "mac"}), "mac6")
        with mock.patch.object(P, "read_route", return_value={}):
            self.assertEqual(P.job_host({"node": "mac", "host": "mac6"}), "mac")

    def test_unknown_node_keeps_stored_host(self):
        self.assertEqual(P.job_host({"node": "nope", "host": "h"}), "h")


if __name__ == "__main__":
    unittest.main()
