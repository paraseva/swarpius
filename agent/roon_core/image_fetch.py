"""Fetch artwork bytes from the Roon Core.

Extracted from ``RoonBrowseMixin`` — image-fetching has nothing
structurally to do with browsing the Roon library, it just happened
to share the connection's host/port/api. Lives as a free function
here so callers (zone_artwork_service.py via the RoonConnection
facade method) reach it through a clean dependency rather than a
mixin method.

Tries the roonapi instance's ``get_image`` first (signature varies
across roonapi versions, so we filter kwargs to whatever the bound
method accepts); falls back to a direct HTTP call against Roon's
``/api/image/<key>`` endpoint, walking through query-param variants
until one returns 200.
"""

from __future__ import annotations

import inspect
from typing import Any, Tuple

import requests

from app.exceptions import ExternalServiceError


def fetch_image_bytes(
    api: Any,
    host: str,
    port: int,
    image_key: str,
    width: int = 400,
    height: int = 400,
) -> Tuple[bytes, str]:
    """Return ``(content, content_type)`` for *image_key*. Raises
    :class:`ValueError` if *image_key* is empty, or
    :class:`ExternalServiceError` if every path failed."""
    if not image_key:
        raise ValueError("image_key is required")

    get_image = getattr(api, "get_image", None)
    if callable(get_image):
        call_variants = [
            {"image_key": image_key, "width": width, "height": height},
            {"image_key": image_key, "w": width, "h": height},
            {"key": image_key, "width": width, "height": height},
            {"key": image_key, "w": width, "h": height},
            {"image_key": image_key},
            {"key": image_key},
        ]
        for kwargs in call_variants:
            try:
                signature = inspect.signature(get_image)
                bound = {
                    k: v
                    for k, v in kwargs.items()
                    if k in signature.parameters
                }
                response = get_image(**bound)
                if isinstance(response, bytes):
                    return response, "image/jpeg"
                if (
                    isinstance(response, tuple)
                    and len(response) == 2
                    and isinstance(response[0], bytes)
                ):
                    return response[0], str(response[1] or "image/jpeg")
            except Exception:
                continue

    base = f"http://{host}:{port}"
    token = getattr(api, "token", None)
    urls = [
        f"{base}/api/image/{image_key}?scale=fill&width={width}&height={height}",
        f"{base}/api/image/{image_key}?scale=fill&w={width}&h={height}",
        f"{base}/api/image/{image_key}",
    ]
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    for url in urls:
        try:
            response = requests.get(url, headers=headers, timeout=4)
            if response.status_code == 200 and response.content:
                return response.content, response.headers.get(
                    "Content-Type", "image/jpeg",
                )
        except Exception:
            continue

    raise ExternalServiceError(
        f"Unable to fetch image for image_key '{image_key}'",
    )
