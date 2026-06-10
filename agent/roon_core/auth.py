import logging
import time
from pathlib import Path
from typing import Tuple

from roonapi import RoonApi, RoonDiscovery

from app.data_paths import config_dir
from roon_core.discovery import discover_cores, select_core

logger = logging.getLogger(__name__)

_AUTH_TIMEOUT_SECONDS = 120


def default_core_id_path() -> Path:
    """Canonical on-disk location for the persisted Roon ``core_id``.

    Evaluated lazily so ``SWARPIUS_DATA_DIR`` set at process startup
    is honoured. ``RoonConnection`` uses this as its default for the
    ``core_id_path`` constructor arg; live tests pass a separate path
    to keep test auth state isolated from production.
    """
    return config_dir() / "roon_core_id"


def default_token_path() -> Path:
    """Canonical on-disk location for the persisted Roon API token.
    See :func:`default_core_id_path` for the lazy-evaluation rationale.
    """
    return config_dir() / "roon_core_token"


class RoonAuthMixin:
    """Auth half of :class:`RoonConnection`. Not a standalone mixin —
    lives in its own module for navigability, composed only into
    :class:`RoonConnection` alongside the other Roon* mixins.

    The file paths for the saved core_id and token live on the
    instance (``self._core_id_path`` / ``self._token_path``) so the
    same connection class can serve production (single shared path)
    and live tests (separate paths under a distinct ``extension_id``).
    """

    _core_id_path: Path
    _token_path: Path

    def _get_id_and_token(self) -> dict:
        with open(self._core_id_path, encoding="utf-8") as core_f:
            core_id = core_f.read()

        with open(self._token_path, encoding="utf-8") as token_f:
            token = token_f.read()

        return {"core_id": core_id, "token": token}

    def _write_id_and_token(self, core_id: str, token: str) -> None:
        """Persist a fresh core_id + token pair. Called both after an
        explicit ``_perform_auth`` and after a library-internal
        re-pair (detected by a token mismatch post-``RoonApi()``).
        """
        self._core_id_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._core_id_path, "w", encoding="utf-8") as f:
            f.write(core_id)
        with open(self._token_path, "w", encoding="utf-8") as f:
            f.write(token)

    def _perform_auth(self, appinfo: dict, server: Tuple[str, int]) -> None:
        """Register as a pending extension against *server* and wait for
        the user to approve in Roon Settings > Extensions.

        A single ``RoonApi`` instance is created against the chosen Core,
        producing exactly one approval prompt. The fan-out-across-all-
        discovered-addresses pattern was removed because multiple SOOD
        responses for the same Core resulted in multiple prompts — see
        ``roon/discovery.py`` for the deduplicating discovery helper.
        """
        display = appinfo.get("display_name", "the extension")
        self._notify_lifecycle(
            f"Pairing — please approve the {display} extension in "
            f"Roon Settings → Extensions ({server[0]}:{server[1]})",
        )
        logger.info(
            "Pairing with Roon Core at %s:%s. Please approve the %s "
            "extension in Roon Settings > Extensions.",
            server[0], server[1], display,
        )

        roon_api = RoonApi(appinfo, None, server[0], server[1], False)
        deadline = time.monotonic() + _AUTH_TIMEOUT_SECONDS
        while roon_api.token is None:
            if time.monotonic() > deadline:
                roon_api.stop()
                raise ConnectionError(
                    f"No authorisation received within {_AUTH_TIMEOUT_SECONDS}s. "
                    f"Please approve the {display} extension in Roon Settings > "
                    "Extensions and restart.",
                )
            time.sleep(1)

        self._notify_lifecycle(f"Authorised on {roon_api.core_name}")
        logger.info("Authorised on %s (%s)", roon_api.core_name, roon_api.host)
        roon_api.stop()

        self._write_id_and_token(roon_api.core_id, roon_api.token)

    def _discover_and_pair(self, appinfo: dict) -> Tuple[str, int]:
        """Discover Cores on the network, pick one, and pair with it.

        Uses the deduplicating SOOD helper in ``roon/discovery.py`` so
        multiple network paths to the same Core collapse to one entry.
        Honours ``ROON_CORE_NAME`` for installations with multiple Cores
        on the same LAN. Returns the ``(host, port)`` of the paired
        Core, which is also written to the token/core_id files.
        """
        from app.settings import get_settings
        preferred_name = get_settings().roon_core_name
        self._notify_lifecycle("Discovering Roon Cores on the network…")
        cores = discover_cores()
        chosen = select_core(cores, preferred_name=preferred_name)
        self._notify_lifecycle(
            f"Found Roon Core '{chosen.core_name or 'unnamed'}' at "
            f"{chosen.host}:{chosen.port}",
        )
        logger.info(
            "Selected Roon Core '%s' at %s:%s (core_id=%s)",
            chosen.core_name or "<unnamed>",
            chosen.host, chosen.port, chosen.core_id,
        )
        self._perform_auth(appinfo, (chosen.host, chosen.port))
        return (chosen.host, chosen.port)

    def _lookup_known_core(self, core_id: str):
        """Locate a previously-paired Roon Core by its core_id.

        Used on the reconnect path where we already have a saved token
        bound to a specific core_id — the discovery step here is just
        the address lookup. First-time discovery goes through
        ``_discover_and_pair`` + the new ``discovery.py`` helpers,
        which handle multi-core dedup and selection.
        """
        discover = RoonDiscovery(core_id)
        server = discover.first()
        discover.stop()
        return server
