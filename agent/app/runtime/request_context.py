"""Request ID propagation via contextvars.

Set the request ID at the start of each request in ``request_flow.py``;
read it anywhere in the call stack (browse, action tools, etc.) without
threading it through method parameters.
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import Optional

_request_id_var: ContextVar[Optional[str]] = ContextVar("request_id", default=None)


def set_request_id(request_id: str) -> None:
    _request_id_var.set(request_id)


def clear_request_id() -> None:
    _request_id_var.set(None)
