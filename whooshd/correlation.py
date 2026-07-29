"""Bounded request-correlation identifiers for Whoosh'd-owned surfaces."""

from __future__ import annotations

import re
import uuid
from typing import Any


MAX_IDENTIFIER_LENGTH = 128
UPSTREAM_REQUEST_ID_HEADER = "X-Request-ID"
WHOOSH_REQUEST_ID_HEADER = "X-Whoosh-Request-ID"
_SAFE_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


def normalize_identifier(value: Any) -> str | None:
    """Return a safe identifier, or ``None`` without reflecting unsafe input.

    Deliberately do not trim or coerce input.  A caller either supplied an
    identifier that already conforms to the wire contract or supplied nothing
    usable; normalization must not turn an unsafe value into echoed metadata.
    """

    if isinstance(value, str) and _SAFE_IDENTIFIER_RE.fullmatch(value):
        return value
    return None


def is_valid_identifier(value: Any) -> bool:
    """Whether *value* can be retained as bounded operational metadata."""

    return normalize_identifier(value) is not None


def generate_whoosh_request_id() -> str:
    """Create a fresh Whoosh'd-owned lifecycle identifier."""

    return f"whoosh-{uuid.uuid4().hex}"


def correlation_response_headers(
    *,
    upstream_request_id: Any = None,
    whoosh_request_id: Any = None,
) -> dict[str, str]:
    """Build safe correlation headers without replacing either identity."""

    headers: dict[str, str] = {}
    upstream = normalize_identifier(upstream_request_id)
    whoosh = normalize_identifier(whoosh_request_id)
    if upstream is not None:
        headers[UPSTREAM_REQUEST_ID_HEADER] = upstream
    if whoosh is not None:
        headers[WHOOSH_REQUEST_ID_HEADER] = whoosh
    return headers
