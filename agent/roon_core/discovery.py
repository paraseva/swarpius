"""Roon Core discovery that preserves the Core's identity.

The bundled ``roonapi.RoonDiscovery.all()`` drops the SOOD response's
``unique_id`` (core_id) and ``name`` fields and returns bare
``(host, port)`` tuples. That loses two things we need:

1. The ability to deduplicate multiple addresses for the *same* Core
   (IPv4 + IPv6, multi-NIC, Docker bridge + host) — without which the
   first-time auth flow fans out across every response and generates
   one Roon extension-approval prompt per address.
2. A human-readable label so users with more than one Core can pick
   one via ``ROON_CORE_NAME`` in ``.env``.

This module reimplements the SOOD receive loop — retransmitting the
query so a single dropped UDP packet doesn't fail discovery — keeps the
extra properties, and provides a ``select_core`` helper that turns the
de-duplicated list into a single paired Core — or raises a clearly
actionable error when the choice is ambiguous.
"""

from __future__ import annotations

import logging
import os
import socket
import time
from dataclasses import dataclass
from typing import List, Optional

from roonapi.constants import SOOD_MULTICAST_IP, SOOD_PORT
from roonapi.soodmessage import FormatException, SOODMessage

_log = logging.getLogger("swarpius.core_discovery")


@dataclass(frozen=True)
class DiscoveredCore:
    """A Roon Core surfaced by a SOOD response.

    ``core_id`` is the authoritative identity — multiple entries with
    the same ``core_id`` are the same Core reachable via different
    network addresses.
    """

    host: str
    port: int
    core_id: str
    core_name: str


def dedupe_by_core_id(cores: List[DiscoveredCore]) -> List[DiscoveredCore]:
    """Keep the first-seen address per ``core_id``.

    Preserves the order of first sightings so subsequent selection is
    deterministic across runs (within the natural jitter of UDP arrival
    order).
    """
    seen: dict[str, DiscoveredCore] = {}
    for core in cores:
        if core.core_id not in seen:
            seen[core.core_id] = core
    return list(seen.values())


def select_core(
    cores: List[DiscoveredCore],
    *,
    preferred_name: Optional[str] = None,
) -> DiscoveredCore:
    """Pick a single Core or raise ``ConnectionError`` with guidance.

    Priority:
      1. If ``preferred_name`` is set (trim-robust, case-insensitive),
         return the uniquely matching Core. Raise if zero matches
         (listing available names) or if two Cores share the name
         (asking the user to fall back to ``ROON_CORE_URL``).
      2. If exactly one Core was discovered, return it.
      3. Otherwise raise, listing the discovered names so the user can
         set ``ROON_CORE_NAME``.
    """
    if not cores:
        raise ConnectionError(
            "No Roon Cores found on the network. Ensure your Core is "
            "running and reachable from this host, or set "
            'ROON_CORE_URL="http://<ROON_CORE_IP>:<PORT>" in your .env '
            "to bypass auto-discovery.",
        )

    clean_name = (preferred_name or "").strip()
    if clean_name:
        normalised = clean_name.lower()
        matches = [c for c in cores if c.core_name.strip().lower() == normalised]
        if len(matches) == 1:
            return matches[0]
        if not matches:
            available = ", ".join(f"'{c.core_name}'" for c in cores)
            raise ConnectionError(
                f"Roon Core '{clean_name}' not found. "
                f"Available Cores: {available}. "
                "Update ROON_CORE_NAME in .env to match one of these, "
                "or set ROON_CORE_URL directly.",
            )
        ids = ", ".join(c.core_id for c in matches)
        raise ConnectionError(
            f"Multiple Roon Cores match name '{clean_name}' "
            f"(core_ids: {ids}). Set ROON_CORE_URL in .env to target "
            "one directly.",
        )

    if len(cores) == 1:
        return cores[0]

    available = ", ".join(f"'{c.core_name}'" for c in cores)
    raise ConnectionError(
        f"Multiple Roon Cores found: {available}. "
        "Set ROON_CORE_NAME or ROON_CORE_URL in .env to pick one.",
    )


# SOOD is unacknowledged UDP: a single query can be silently dropped
# (routine on a cold multicast/ARP stack). Retransmit rather than bet
# discovery on one packet, and stop once no new Core has appeared for
# QUIET. QUIET clears two resend rounds so a lurking second Core gets
# re-probed before we give up — values at/below one round (≤2.0s) quantise
# back to a single round, so don't "tidy" it down.
_DISCOVERY_WINDOW_SECONDS = 5.0
_DISCOVERY_RESEND_SECONDS = 1.0
_DISCOVERY_QUIET_SECONDS = 2.5


def discover_cores(timeout: float = _DISCOVERY_WINDOW_SECONDS) -> List[DiscoveredCore]:
    """Broadcast a SOOD query and collect de-duplicated Core responses.

    Retains the ``unique_id`` and ``name`` from each SOOD payload (which
    ``roonapi.RoonDiscovery`` discards) so callers can pair by identity,
    not address. The query is retransmitted every
    ``_DISCOVERY_RESEND_SECONDS`` until no new Core has appeared for
    ``_DISCOVERY_QUIET_SECONDS`` or ``timeout`` elapses — so a single
    dropped UDP packet no longer fails discovery. Returns an empty list
    if nothing responds within ``timeout``.
    """
    roonapi_dir = os.path.dirname(
        os.path.abspath(__import__("roonapi.discovery").discovery.__file__),
    )
    sood_query_path = os.path.join(roonapi_dir, ".soodmsg")
    with open(sood_query_path) as f:
        query = f.read().encode()

    entries: list[DiscoveredCore] = []
    deadline = time.monotonic() + timeout
    seen_core_ids: set[str] = set()
    last_sent: Optional[float] = None
    last_new_core: Optional[float] = None

    with socket.socket(
        socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP,
    ) as sock:
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 32)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        while True:
            now = time.monotonic()
            if now >= deadline:
                break
            if last_sent is None or now - last_sent >= _DISCOVERY_RESEND_SECONDS:
                sock.sendto(query, (SOOD_MULTICAST_IP, SOOD_PORT))
                sock.sendto(query, ("<broadcast>", SOOD_PORT))
                last_sent = now
            sock.settimeout(min(_DISCOVERY_RESEND_SECONDS, deadline - now))
            try:
                data, server = sock.recvfrom(1024)
                message = SOODMessage(data).as_dictionary
            except socket.timeout:
                if (
                    last_new_core is not None
                    and time.monotonic() - last_new_core >= _DISCOVERY_QUIET_SECONDS
                ):
                    break
                continue
            except FormatException as exc:
                _log.warning("Malformed SOOD response: %s", exc)
                continue
            props = message.get("properties", {})
            unique_id = props.get("unique_id")
            if not unique_id:
                _log.debug("Skipping SOOD response without unique_id: %s", message)
                continue
            entries.append(DiscoveredCore(
                host=server[0],
                port=int(props["http_port"]),
                core_id=unique_id,
                core_name=props.get("name", ""),
            ))
            # Cores answer every probe, so the quiet window tracks the last
            # new Core, not the last packet — otherwise retransmits would keep
            # resetting it and discovery would always run the full timeout.
            if unique_id not in seen_core_ids:
                seen_core_ids.add(unique_id)
                last_new_core = time.monotonic()

    return dedupe_by_core_id(entries)
