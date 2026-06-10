"""Image-byte cache for browser artwork requests.

Caches base64-encoded image bytes per ``(image_key, width, height)``
so the browser can fetch artwork via the request-response flow
without re-hitting Roon for every render.
"""

from __future__ import annotations

import base64
import threading
from typing import Any, Dict

from app.exceptions import RoonConnectionUnavailableError
from app.runtime.state_internals import _BoundedDict


class ZoneArtworkCache:

    def __init__(self, max_entries: int) -> None:
        self.image_base64_cache: Dict[str, Dict[str, str]] = _BoundedDict(max_entries)
        self.lock = threading.Lock()

    def get_image_base64_payload(
        self,
        roon_connection: Any,
        image_key: str,
        width: int = 400,
        height: int = 400,
    ) -> Dict[str, Any]:
        if not roon_connection:
            raise RoonConnectionUnavailableError("Roon connection is not available")
        cache_key = f"{image_key}:{width}:{height}"
        cached = self.image_base64_cache.get(cache_key)
        if cached:
            return {
                "image_key": image_key,
                "width": width,
                "height": height,
                **cached,
            }

        image_bytes, mime_type = roon_connection.fetch_image_bytes(
            image_key=image_key, width=width, height=height,
        )
        encoded = base64.b64encode(image_bytes).decode("ascii")
        payload = {"mime_type": mime_type, "base64_data": encoded}
        self.image_base64_cache[cache_key] = payload
        return {
            "image_key": image_key,
            "width": width,
            "height": height,
            **payload,
        }
