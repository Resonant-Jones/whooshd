"""Bounded cross-system correlation identifiers."""

from __future__ import annotations

import re
import uuid
from typing import Any


MAX_IDENTIFIER_LENGTH = 128
SAFE_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


def is_safe_identifier(value: Any) -> bool:
    return isinstance(value, str) and bool(SAFE_IDENTIFIER_RE.fullmatch(value))


def generate_request_id() -> str:
    return f"req_{uuid.uuid4().hex}"


def generate_local_request_id() -> str:
    return f"whooshd_{uuid.uuid4().hex}"


def normalize_request_id(value: Any) -> tuple[str, bool]:
    candidate = value.strip() if isinstance(value, str) else ""
    if is_safe_identifier(candidate):
        return candidate, True
    return generate_request_id(), False


def normalize_optional_identifier(value: Any) -> str | None:
    candidate = value.strip() if isinstance(value, str) else ""
    return candidate if is_safe_identifier(candidate) else None
