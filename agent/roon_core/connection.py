import logging
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from roonapi import RoonApi

from app.data_paths import app_version
from roon_core.auth import (
    RoonAuthMixin,
    default_core_id_path,
    default_token_path,
)
from roon_core.browse import RoonBrowseMixin
from roon_core.events import RoonEventsMixin
from roon_core.playback import RoonPlaybackMixin
from roon_core.schemas import RoonCoreResultsSchema
from roon_core.zones import RoonZoneMixin

logger = logging.getLogger(__name__)


APP_INFO: Dict[str, str] = {
    "extension_id": "ai.paraseva.swarpius",
    "display_name": "Swarpius",
    "display_version": app_version(),
    "publisher": "Paraseva Ltd",
    "email": "hello@paraseva.ai",
    "website": "https://paraseva.ai",
}


class RoonConnection(RoonAuthMixin, RoonEventsMixin, RoonZoneMixin, RoonPlaybackMixin, RoonBrowseMixin):
    """High-level helper for connecting to and browsing a Roon Core.

    ``app_info`` and the auth file paths are parameterised so live
    tests can register as a distinct extension (``swarpius_test``)
    with its own persistent token files — avoids interference with
    a running production instance and stops the test harness from
    accumulating new extension entries in Roon Settings on each run.
    """

    def __init__(
        self,
        default_zone: Optional[str] = None,
        roon_core_host: Optional[str] = None,
        roon_core_port: Optional[int] = None,
        profile: Optional[str] = None,
        lifecycle_callback: Optional[Callable[[str], None]] = None,
        app_info: Optional[Dict[str, str]] = None,
        core_id_path: Optional[Path] = None,
        token_path: Optional[Path] = None,
    ) -> None:
        # Bound to self so the auth + discovery mixins can fire status
        # updates during the typically-silent ~2-120s connect window.
        # Default does nothing so call sites don't have to None-check.
        self._lifecycle_cb = lifecycle_callback or (lambda _msg: None)
        self._app_info: Dict[str, str] = app_info or APP_INFO
        self._core_id_path: Path = core_id_path or default_core_id_path()
        self._token_path: Path = token_path or default_token_path()

        configured_server = (
            (roon_core_host, int(roon_core_port))
            if roon_core_host and roon_core_port is not None
            else None
        )

        auth: Optional[dict] = None
        server = configured_server

        if not configured_server:
            try:
                auth = self._get_id_and_token()
            except OSError:
                auth = None

            if auth:
                server = self._lookup_known_core(auth["core_id"])

            if not auth or not server:
                logger.info("No valid ID/token or core discovery failed — attempting discovery and authorisation")
                server = self._discover_and_pair(self._app_info)

                try:
                    auth = self._get_id_and_token()
                except OSError as exc:
                    raise OSError("No ID or token files found... discovery and authorisation failed") from exc
        else:
            try:
                auth = self._get_id_and_token()
            except OSError:
                self._perform_auth(self._app_info, configured_server)
                try:
                    auth = self._get_id_and_token()
                except OSError as exc:
                    raise OSError("No ID or token files found... authorisation failed") from exc

        if not auth:
            raise OSError("No ID or token files found... discovery and authorisation failed")

        # A (None, None) tuple is truthy, so a bare ``not server`` check misses
        # it and we'd crash on ``int(None)`` below — guard the shape.
        if not server or server[0] is None or server[1] is None:
            raise ConnectionError("Discovery failed to find any Roon Cores on the network. Please ensure the Roon Core is running and accessible.")

        if configured_server:
            logger.info("Trying configured Roon Core at %s:%s", server[0], server[1])
        else:
            logger.info("Connecting to Roon Core at %s:%s", server[0], server[1])
        self._notify_lifecycle(f"Connecting to Roon Core at {server[0]}:{server[1]}…")
        self.roon_core_host = server[0]
        self.roon_core_port = int(server[1])

        try:
            self.api = RoonApi(self._app_info, auth["token"], server[0], server[1], True)
        except Exception as exc:
            raise ConnectionError(
                f"Failed to connect to Roon Core at {server[0]}:{server[1]} with provided token. "
                "Please ensure the token is correct and the Roon Core is running and accessible.",
            ) from exc

        # Self-heal: if the saved token was stale and the library
        # silently re-paired during ``RoonApi(...)``, ``self.api.token``
        # now holds the new value. Persist it so subsequent launches
        # don't redo the dance and the user doesn't see a fresh
        # "approve in Roon Settings" prompt every time.
        post_init_token = getattr(self.api, "token", None)
        post_init_core_id = getattr(self.api, "core_id", None)
        if (
            post_init_token
            and post_init_core_id
            and (post_init_token != auth["token"] or post_init_core_id != auth["core_id"])
        ):
            logger.info(
                "Roon library re-paired internally; persisting refreshed core_id + token to %s",
                self._token_path.parent,
            )
            self._write_id_and_token(post_init_core_id, post_init_token)

        self._default_zone_name: Optional[str] = (default_zone or "").strip() or None
        self._preferred_output_id: Optional[str] = None
        self._preferred_zone_label: Optional[str] = None
        self._resolve_default_zone()
        self._set_roon_profile(profile)
        self._init_browse_session()
        self.last_state_event: Optional[Dict[str, Any]] = None
        self.last_queue_event: Optional[Dict[str, Any]] = None
        self.last_queue_events_by_zone: Dict[str, Dict[str, Any]] = {}
        self._event_listeners: List[Callable[[Dict[str, Any]], None]] = []
        self._subscriptions_registered = False
        self._queue_subscribed_zones: set = set()
        self._queue_socket_id: int | None = None
        self._queue_items_cache: Dict[str, List[Dict[str, Any]]] = {}
        self._queue_ref_maps: Dict[str, Any] = {}
        self._ensure_live_subscriptions()

    def _notify_lifecycle(self, message: str) -> None:
        self._lifecycle_cb(message)

    @property
    def is_connected(self) -> bool:
        """Check if the Roon API WebSocket connection is active."""
        sock = getattr(self.api, "_roonsocket", None)
        return bool(sock and sock.connected)

    def wait_for_connection(self, timeout: float = 30.0) -> bool:
        """Wait for the WebSocket connection to be ready.

        Useful after a Roon-side disconnect triggers the roonapi library's
        automatic 20-second reconnect cycle. Returns True if connected
        within *timeout* seconds, False otherwise.
        """
        if self.is_connected:
            return True
        logger.warning("Roon connection lost — waiting up to %.0fs for reconnect...", timeout)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.is_connected:
                waited = timeout - (deadline - time.monotonic())
                logger.info("Roon connection restored after %.1fs", waited)
                return True
            time.sleep(0.5)
        logger.error("Roon connection not restored within %.0fs", timeout)
        return self.is_connected

    def _set_roon_profile(self, profile: Optional[str]) -> None:
        profile_name = (profile or "").strip()
        if not profile_name:
            return

        output_id = self._lookup_output_id(self.get_default_zone())
        opts = {
            "zone_or_output_id": output_id,
            "hierarchy": "browse",
        }

        path = ["Settings", "Profile", profile_name, "Profile", profile_name]

        try:
            self.api.browse_browse(opts | {"pop_all": True})

            while path:
                results = self.api.browse_load(opts)
                roon_core_results = RoonCoreResultsSchema(**results)
                field_value = path.pop(0)
                target = self.find_item_by_field(
                    items=roon_core_results.items,
                    field_name="title",
                    field_value=field_value,
                )
                if not target or not target.item_key:
                    raise ValueError(f"Unable to resolve profile path segment '{field_value}'")
                if path:
                    self.api.browse_browse(opts | {"item_key": target.item_key})
                elif target.subtitle == "selected":
                    logger.info(f"Successfully set profile '{profile}'")
                else:
                    raise ValueError(f"Could not confirm profile '{profile}' was set")

        except Exception as exc:
            logger.warning(
                "Unable to apply Roon profile '%s'; continuing with default profile. Cause: %s",
                profile_name,
                exc,
            )
