"""Authoritative ingress-to-backend request boundary.

The public request model is intentionally permissive for compatibility with
OpenAI-shaped callers.  Backend execution is not permissive: adapters receive
an explicit representation containing only canonical inference fields and
declared provider extensions.  Whoosh'd control metadata remains on the
ingress request for internal consumers such as ThreadWake and never crosses
this boundary.
"""

from __future__ import annotations

import copy
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from whooshd.contracts import ChatCompletionRequest, ChatMessage, GenerateRequest


logger = logging.getLogger(__name__)

POLICY_VERSION = "cwc-006-v1"

# These are the fields that Whoosh'd knows how to represent at the backend
# boundary.  The adapter map below is intentionally explicit even though the
# current runtimes share the same serialized canonical set.
CHAT_CANONICAL_FIELDS = (
    "model",
    "messages",
    "stream",
    "temperature",
    "top_p",
    "max_tokens",
    "max_completion_tokens",
    "stop",
    "user",
    "tools",
    "tool_choice",
    "parallel_tool_calls",
    "response_format",
    "seed",
    "presence_penalty",
    "frequency_penalty",
    "logit_bias",
    "logprobs",
    "top_logprobs",
    "reasoning_effort",
)

INTERNAL_CONTROL_FIELDS = frozenset({"metadata", "threadwake"})

# Provider extensions are serialization capabilities, not a claim that every
# installed backend version implements them.  Live provider compatibility
# remains a separate proof surface.
ADAPTER_EXTENSIONS: dict[str, frozenset[str]] = {
    "stub": frozenset(),
    "mlx_lm": frozenset(),
    "mlx_lm_server": frozenset(),
    "mlx_vlm": frozenset(),
    "llama_cpp": frozenset({"min_p", "top_k", "repeat_penalty"}),
    "generic": frozenset(),
}

ADAPTER_CANONICAL_FIELDS: dict[str, frozenset[str]] = {
    kind: frozenset(CHAT_CANONICAL_FIELDS) for kind in ADAPTER_EXTENSIONS
}

_INTERNAL_EXTRA_PREFIXES = (
    "codexify_",
    "whooshd_",
    "threadwake",
    "routing_",
    "route_",
    "queue_",
    "admission_",
    "cache_",
    "trace_",
    "diagnostic_",
    "provenance",
    "identity_",
    "persona_",
    "memory_",
    "retrieval_",
    "lifecycle_",
    "cancel",
)

_INFERENCE_EXTRA_PREFIXES = (
    "temperature",
    "top_",
    "max_",
    "min_",
    "stop",
    "seed",
    "presence_",
    "frequency_",
    "logit_",
    "logprob",
    "tool",
    "response_",
    "reasoning",
    "sampling",
    "repeat_",
    "prompt",
    "message",
    "stream",
    "model",
)

_FIELD_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,80}$")


class BackendRequestPolicyError(ValueError):
    """Raised when an unknown field could change inference semantics."""

    def __init__(self, *, adapter_kind: str, rejected_fields: tuple[str, ...]):
        self.adapter_kind = adapter_kind
        self.rejected_fields = rejected_fields
        # Do not include field values or request text in exception strings.
        super().__init__("unsupported inference field policy")


@dataclass(frozen=True)
class BackendChatRequest:
    """Backend-facing chat request with no internal control attributes."""

    model: str
    messages: list[ChatMessage]
    stream: bool
    temperature: float | None
    top_p: float | None
    max_tokens: int | None
    max_completion_tokens: int | None
    stop: list[str] | None
    user: str | None
    tools: list[dict] | None
    tool_choice: str | dict | None
    parallel_tool_calls: bool | None
    response_format: dict | None
    seed: int | None
    presence_penalty: float | None
    frequency_penalty: float | None
    logit_bias: dict[str, float] | None
    logprobs: bool | None
    top_logprobs: int | None
    reasoning_effort: str | None
    extra_fields: dict[str, Any] = field(default_factory=dict)
    adapter_kind: str = field(default="generic", repr=False, compare=False)
    forwarded_fields: tuple[str, ...] = field(default_factory=tuple, repr=False, compare=False)
    stripped_fields: tuple[str, ...] = field(default_factory=tuple, repr=False, compare=False)
    rejected_fields: tuple[str, ...] = field(default_factory=tuple, repr=False, compare=False)


@dataclass(frozen=True)
class BackendGenerateRequest:
    """Backend-facing /v1/generate request.

    GenerateRequest is already strict today, but this separate type keeps the
    same ingress/backend distinction for every execution path.
    """

    prompt: str
    model_id: str | None
    max_tokens: int
    temperature: float
    top_p: float
    stop: list[str] | None
    request_id: str | None
    adapter_kind: str = field(default="generic", repr=False, compare=False)


def _adapter_policy(adapter_kind: str | None) -> tuple[frozenset[str], frozenset[str]]:
    kind = adapter_kind or "generic"
    return (
        ADAPTER_CANONICAL_FIELDS.get(kind, ADAPTER_CANONICAL_FIELDS["generic"]),
        ADAPTER_EXTENSIONS.get(kind, ADAPTER_EXTENSIONS["generic"]),
    )


def _is_internal_extra(name: str) -> bool:
    normalized = name.lower()
    return normalized in INTERNAL_CONTROL_FIELDS or normalized.startswith(_INTERNAL_EXTRA_PREFIXES)


def _is_inference_affecting_extra(name: str) -> bool:
    normalized = name.lower()
    if normalized in CHAT_CANONICAL_FIELDS or normalized.startswith(_INFERENCE_EXTRA_PREFIXES):
        return True
    # Reject names that wrap a canonical control (for example
    # ``unknown_temperature``) instead of silently changing its semantics.
    tokens = set(filter(None, re.split(r"[^a-z0-9]+", normalized)))
    return bool(
        tokens
        & {
            "temperature",
            "top",
            "tokens",
            "stop",
            "seed",
            "tool",
            "tools",
            "reasoning",
            "sampling",
            "prompt",
            "message",
            "stream",
            "model",
        }
    )


def _field_names(values: list[str]) -> tuple[str, ...]:
    return tuple(sorted(set(values)))


def _log_policy(
    *,
    adapter_kind: str,
    request_id: str | None,
    forwarded: tuple[str, ...],
    stripped: tuple[str, ...],
    rejected: tuple[str, ...],
) -> None:
    logger.info(
        "backend_request.policy policy_version=%s adapter_kind=%s request_id=%s "
        "forwarded_field_names=%s stripped_field_names=%s rejected_field_names=%s "
        "forwarded_field_count=%s stripped_field_count=%s rejected_field_count=%s",
        POLICY_VERSION,
        adapter_kind,
        request_id or "unavailable",
        ",".join(forwarded),
        ",".join(stripped),
        ",".join(rejected),
        len(forwarded),
        len(stripped),
        len(rejected),
    )


def sanitize_chat_request(
    request: ChatCompletionRequest | BackendChatRequest,
    *,
    adapter_kind: str | None = None,
    request_id: str | None = None,
) -> BackendChatRequest:
    """Return a new backend request without controls or undeclared extras."""
    if isinstance(request, BackendChatRequest):
        if request_id:
            _log_policy(
                adapter_kind=adapter_kind or request.adapter_kind,
                request_id=request_id,
                forwarded=request.forwarded_fields,
                stripped=request.stripped_fields,
                rejected=request.rejected_fields,
            )
        return request

    kind = adapter_kind or "generic"
    canonical, extensions = _adapter_policy(kind)
    forwarded = [
        name
        for name in CHAT_CANONICAL_FIELDS
        if name in canonical and (name in {"model", "messages", "stream"} or getattr(request, name, None) is not None)
    ]
    stripped: list[str] = []
    if request.metadata is not None:
        stripped.append("metadata")
    if request.threadwake is not None:
        stripped.append("threadwake")

    allowed_extensions: dict[str, Any] = {}
    rejected: list[str] = []
    for raw_name, value in (getattr(request, "extra_fields", None) or {}).items():
        name = str(raw_name)
        normalized = name.lower()
        if name in extensions and _FIELD_NAME_RE.fullmatch(name):
            allowed_extensions[name] = copy.deepcopy(value)
            forwarded.append(name)
        elif _is_internal_extra(name):
            stripped.append(name)
        elif _is_inference_affecting_extra(name):
            rejected.append(name)
        else:
            # This includes all reserved/control names.  Values are never
            # inspected for diagnostics or forwarded to execution.
            stripped.append(name)

    forwarded_names = _field_names(forwarded)
    stripped_names = _field_names(stripped)
    rejected_names = _field_names(rejected)
    _log_policy(
        adapter_kind=kind,
        request_id=request_id,
        forwarded=forwarded_names,
        stripped=stripped_names,
        rejected=rejected_names,
    )
    if rejected_names:
        raise BackendRequestPolicyError(
            adapter_kind=kind,
            rejected_fields=rejected_names,
        )

    values = {
        name: copy.deepcopy(getattr(request, name, None))
        for name in CHAT_CANONICAL_FIELDS
    }
    values["extra_fields"] = allowed_extensions
    values["adapter_kind"] = kind
    values["forwarded_fields"] = forwarded_names
    values["stripped_fields"] = stripped_names
    values["rejected_fields"] = rejected_names
    return BackendChatRequest(**values)


def ensure_backend_chat_request(
    request: ChatCompletionRequest | BackendChatRequest,
    *,
    adapter_kind: str | None = None,
    request_id: str | None = None,
) -> BackendChatRequest:
    """Idempotent backend boundary for adapter and forwarding call sites."""
    return sanitize_chat_request(
        request,
        adapter_kind=adapter_kind,
        request_id=request_id,
    )


def sanitize_generate_request(
    request: GenerateRequest | BackendGenerateRequest,
    *,
    adapter_kind: str | None = None,
) -> BackendGenerateRequest:
    """Return a new strict backend representation for /v1/generate."""
    if isinstance(request, BackendGenerateRequest):
        return request
    kind = adapter_kind or "generic"
    backend_request = BackendGenerateRequest(
        prompt=request.prompt,
        model_id=request.model_id,
        max_tokens=request.max_tokens,
        temperature=request.temperature,
        top_p=request.top_p,
        stop=copy.deepcopy(request.stop),
        request_id=request.request_id,
        adapter_kind=kind,
    )
    _log_policy(
        adapter_kind=kind,
        request_id=request.request_id,
        forwarded=("max_tokens", "model_id", "prompt", "stop", "temperature", "top_p"),
        stripped=(),
        rejected=(),
    )
    return backend_request


def ensure_backend_generate_request(
    request: GenerateRequest | BackendGenerateRequest,
    *,
    adapter_kind: str | None = None,
) -> BackendGenerateRequest:
    return sanitize_generate_request(request, adapter_kind=adapter_kind)


# Names used by documentation and contract tests.
SUPPORTED_ADAPTER_EXTENSIONS = ADAPTER_EXTENSIONS
INTERNAL_CONTROL_NAMES = INTERNAL_CONTROL_FIELDS
