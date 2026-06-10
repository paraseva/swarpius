"""Tests for multi-Core-aware SOOD discovery and selection.

The roonapi library's RoonDiscovery.all() returns bare (host, port) tuples
and drops the core_id + name that the SOOD response carries. We want
richer discovery so Swarpius can:

- Deduplicate multiple addresses for the same Core (IPv4 + IPv6, multi-NIC)
  into a single pairing candidate — fixes the long-standing "two approval
  prompts" bug for users with one Core on a multi-interface host.
- Let users with multiple Cores pick by friendly name via ROON_CORE_NAME,
  or fall back to an explicit error listing available Core names when
  ambiguous.
"""

from __future__ import annotations

import socket
import unittest
from unittest.mock import patch

try:
    from tests.stub_modules import install_common_test_stubs
except ModuleNotFoundError:
    from stub_modules import install_common_test_stubs

install_common_test_stubs()

from roonapi.constants import SOOD_MULTICAST_IP  # noqa: E402

from roon_core.discovery import (  # noqa: E402
    DiscoveredCore,
    dedupe_by_core_id,
    discover_cores,
    select_core,
)


def _core(host: str, port: int, core_id: str, core_name: str) -> DiscoveredCore:
    return DiscoveredCore(host=host, port=port, core_id=core_id, core_name=core_name)


class TestDedupeByCoreId(unittest.TestCase):

    def test_empty_input_returns_empty(self):
        self.assertEqual(dedupe_by_core_id([]), [])

    def test_same_core_id_across_paths_collapses_to_one(self):
        """IPv4 + IPv6 responses from the same Core must become one entry."""
        v4 = _core("192.168.1.5", 9330, "abc", "Living Room Core")
        v6 = _core("fe80::1", 9330, "abc", "Living Room Core")
        result = dedupe_by_core_id([v4, v6])
        self.assertEqual(len(result), 1)
        # First-seen address wins — arbitrary but deterministic.
        self.assertEqual(result[0], v4)

    def test_different_core_ids_preserved(self):
        a = _core("192.168.1.5", 9330, "core-a", "Living Room Core")
        b = _core("192.168.1.9", 9330, "core-b", "Studio")
        result = dedupe_by_core_id([a, b])
        self.assertEqual(result, [a, b])

    def test_order_preserved_across_duplicates(self):
        """Preserve the order of unique_id first-sightings even when
        duplicates appear interleaved — caller may sort after this."""
        a1 = _core("192.168.1.5", 9330, "core-a", "A")
        b = _core("192.168.1.9", 9330, "core-b", "B")
        a2 = _core("192.168.1.6", 9330, "core-a", "A")
        result = dedupe_by_core_id([a1, b, a2])
        self.assertEqual(result, [a1, b])


class TestSelectCore(unittest.TestCase):

    def test_empty_list_raises_with_actionable_message(self):
        """Message should include both the general guidance and the
        concrete ROON_CORE_URL override — the latter is how Docker users
        on Windows (where SOOD broadcast doesn't reach the container)
        get unstuck."""
        with self.assertRaises(ConnectionError) as ctx:
            select_core([])
        msg = str(ctx.exception)
        self.assertIn("No Roon Cores found", msg)
        self.assertIn("ROON_CORE_URL", msg)

    def test_multiple_cores_no_preference_raises_with_names(self):
        cores = [
            _core("192.168.1.5", 9330, "core-a", "Living Room Core"),
            _core("192.168.1.9", 9330, "core-b", "Studio"),
        ]
        with self.assertRaises(ConnectionError) as ctx:
            select_core(cores)
        msg = str(ctx.exception)
        self.assertIn("Living Room Core", msg)
        self.assertIn("Studio", msg)
        self.assertIn("ROON_CORE_NAME", msg)
        self.assertIn("ROON_CORE_URL", msg)

    def test_name_match_selects_matching_core(self):
        cores = [
            _core("192.168.1.5", 9330, "core-a", "Living Room Core"),
            _core("192.168.1.9", 9330, "core-b", "Studio"),
        ]
        self.assertEqual(
            select_core(cores, preferred_name="Studio"),
            cores[1],
        )

    def test_name_match_is_case_insensitive_and_trims(self):
        cores = [_core("192.168.1.5", 9330, "abc", "Living Room Core")]
        self.assertEqual(
            select_core(cores, preferred_name="  living room CORE "),
            cores[0],
        )

    def test_name_match_not_found_raises_with_available_names(self):
        cores = [
            _core("192.168.1.5", 9330, "core-a", "Living Room Core"),
            _core("192.168.1.9", 9330, "core-b", "Studio"),
        ]
        with self.assertRaises(ConnectionError) as ctx:
            select_core(cores, preferred_name="Kitchen")
        msg = str(ctx.exception)
        self.assertIn("Kitchen", msg)
        self.assertIn("Living Room Core", msg)
        self.assertIn("Studio", msg)

    def test_duplicate_names_with_different_core_ids_raises(self):
        """If two Cores actually share a name (possible — user renamed
        both machines "Roon"), we must not pick arbitrarily. Force the
        user to disambiguate via ROON_CORE_URL."""
        cores = [
            _core("192.168.1.5", 9330, "core-a", "Roon"),
            _core("192.168.1.9", 9330, "core-b", "Roon"),
        ]
        with self.assertRaises(ConnectionError) as ctx:
            select_core(cores, preferred_name="Roon")
        msg = str(ctx.exception)
        self.assertIn("ROON_CORE_URL", msg)
        # Core IDs surfaced so the user can at least identify them
        # in ifconfig/DHCP or Roon's own UI.
        self.assertIn("core-a", msg)
        self.assertIn("core-b", msg)

    def test_empty_preferred_name_ignored_like_unset(self):
        """Defensive: env var read as empty string should behave like
        unset — not match a Core whose name is accidentally blank."""
        cores = [_core("192.168.1.5", 9330, "abc", "Living Room Core")]
        self.assertEqual(select_core(cores, preferred_name=""), cores[0])
        self.assertEqual(select_core(cores, preferred_name="   "), cores[0])


def _sood_response(*, name: str, unique_id: str, http_port: int) -> bytes:
    """Build a real SOOD 'R' response so the production SOODMessage parser
    runs on the call path rather than being stubbed out."""
    out = bytearray(b"SOOD\x02R")
    for key, value in (
        ("name", name),
        ("unique_id", unique_id),
        ("http_port", str(http_port)),
    ):
        k, v = key.encode(), value.encode()
        out += len(k).to_bytes(1, "big") + k + len(v).to_bytes(2, "big") + v
    return bytes(out)


class _FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class _FakeSocket:
    """A SOOD exchange. Each Core in ``cores`` answers *every* query it
    receives, so a Core reachable from probe ``available_from`` onward
    enqueues a fresh response on every (re)send from that probe on — which
    is exactly what makes a naive 'quiet since last packet' exit never
    fire. ``available_from > 1`` models the early probes being dropped.
    recvfrom drains the queue, advancing the fake clock to stand in for
    the blocking wait: by the per-recv timeout on silence, a hair on
    delivery.
    """

    def __init__(self, clock: _FakeClock, cores) -> None:
        self._clock = clock
        self._cores = list(cores)  # (available_from, data, address)
        self._queue: list = []
        self._mcast_sends = 0
        self._timeout = 0.0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def setsockopt(self, *args):
        pass

    def settimeout(self, seconds):
        self._timeout = seconds

    def sendto(self, data, address):
        if address[0] != SOOD_MULTICAST_IP:
            return
        self._mcast_sends += 1
        for available_from, response, source in self._cores:
            if self._mcast_sends >= available_from:
                self._queue.append((response, source))

    def recvfrom(self, bufsize):
        if self._queue:
            self._clock.advance(0.01)
            return self._queue.pop(0)
        self._clock.advance(self._timeout)
        raise socket.timeout()


def _run_discovery(script):
    clock = _FakeClock()
    sock = _FakeSocket(clock, script)
    with patch("roon_core.discovery.socket.socket", return_value=sock), patch(
        "time.monotonic", clock.monotonic
    ):
        return discover_cores(), clock


class TestDiscoverCores(unittest.TestCase):

    _CEILING_SECONDS = 5.0  # mirrors discover_cores's default retry window

    def test_finds_core_when_first_query_is_lost(self):
        """The core contract: a single dropped query must not doom discovery.
        A Core that only answers from the second probe onward is still found —
        proving the query is retransmitted, not sent once."""
        resp = _sood_response(name="Living Room", unique_id="core-a", http_port=9330)
        result, _ = _run_discovery([(2, resp, ("192.168.1.5", 9003))])
        self.assertEqual([c.core_id for c in result], ["core-a"])

    def test_success_returns_before_the_full_window(self):
        """Once the Cores are known, discovery returns promptly instead of
        waiting out the whole ceiling — even though the Core keeps answering
        every retransmit (the quiet window must track new Cores, not packets)."""
        resp = _sood_response(name="Living Room", unique_id="core-a", http_port=9330)
        result, clock = _run_discovery([(1, resp, ("192.168.1.5", 9003))])
        self.assertEqual([c.core_id for c in result], ["core-a"])
        self.assertLess(clock.now, self._CEILING_SECONDS)

    def test_silent_network_returns_empty(self):
        """No responses → empty list (the caller raises the actionable error);
        must not hang or raise."""
        result, _ = _run_discovery([])
        self.assertEqual(result, [])

    def test_repeated_responses_collapse_to_one_core(self):
        """A present Core answers every retransmit, so we hear it many times;
        the result must stay a single entry, not one pairing candidate per
        probe (else first-time auth fans out into duplicate approval prompts)."""
        resp = _sood_response(name="Living Room", unique_id="core-a", http_port=9330)
        result, _ = _run_discovery([(1, resp, ("192.168.1.5", 9003))])
        self.assertEqual([c.core_id for c in result], ["core-a"])

    def test_second_core_answering_later_is_not_truncated(self):
        """Early-exit must not return the instant the first Core replies and
        miss a second Core that answers a probe later."""
        a = _sood_response(name="Living Room", unique_id="core-a", http_port=9330)
        b = _sood_response(name="Studio", unique_id="core-b", http_port=9330)
        result, _ = _run_discovery(
            [(1, a, ("192.168.1.5", 9003)), (2, b, ("192.168.1.9", 9003))]
        )
        self.assertEqual({c.core_id for c in result}, {"core-a", "core-b"})


if __name__ == "__main__":
    unittest.main()
