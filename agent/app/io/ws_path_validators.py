"""Validators for WS payload strings that flow into sensitive sinks.

Strings from the WS payload reach two sensitive sinks unchanged:

- *Filesystem paths* — ``date``, ``conversation_id``, ``request_id``
  are concatenated into ``Path()`` joins for the per-conversation log
  files. Without shape validation, a crafted payload like
  ``date="../.."`` would let a LAN client read or write outside the
  data root.
- *Roon Core URLs* — ``image_key`` is interpolated into
  ``f"{base}/api/image/{image_key}?…"``. Without validation, ``..``
  or ``?`` characters can steer the request away from the image
  endpoint.

Each validator strips whitespace, fails fast on the empty string,
and matches a strict regex; a failure raises ``ValueError`` so the
WS dispatcher in ``websocket_flow._handle_json_request`` reports it
as ``{ok: false, error: <msg>}`` without crashing.
"""

from __future__ import annotations

import re
from typing import Optional

_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_CONVERSATION_ID_PATTERN = re.compile(r"^c\d+$")
_REQUEST_ID_PATTERN = re.compile(r"^rq-c\d+-\d+$")
_IMAGE_KEY_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,256}$")


def _coerce_strip(value: Optional[str]) -> str:
    if value is None:
        return ""
    return str(value).strip()


def validate_date(value: Optional[str]) -> str:
    s = _coerce_strip(value)
    if not _DATE_PATTERN.match(s):
        raise ValueError(f"date must be YYYY-MM-DD; got {value!r}")
    return s


def validate_conversation_id(value: Optional[str]) -> str:
    s = _coerce_strip(value)
    if not _CONVERSATION_ID_PATTERN.match(s):
        raise ValueError(f"conversation_id must match c<digits>; got {value!r}")
    return s


def validate_request_id(value: Optional[str]) -> str:
    s = _coerce_strip(value)
    if not _REQUEST_ID_PATTERN.match(s):
        raise ValueError(
            f"request_id must match rq-c<digits>-<digits>; got {value!r}",
        )
    return s


def validate_image_key(value: Optional[str]) -> str:
    s = _coerce_strip(value)
    if not _IMAGE_KEY_PATTERN.match(s):
        raise ValueError(
            f"image_key must be alphanumeric (with - or _), 1-256 chars; "
            f"got {value!r}",
        )
    return s
