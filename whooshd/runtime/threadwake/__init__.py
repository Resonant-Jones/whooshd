"""ThreadWake observe-mode foundation."""

from .backend import BackendKVAdapterRegistry, FakeKVBackend, KVCapableBackend, NoOpKVBackendAdapter
from .compiler import compile_prompt_graph
from .handles import KVCapability, KVHandle
from .index import (
    EntryStatus,
    ScopeContext,
    ThreadWakeIndex,
    ThreadWakeIndexEntry,
    ThreadWakeStats,
)
from .keys import build_threadwake_cache_key
from .manager import ThreadWakeManager
from .metrics import ThreadWakeMetrics, get_threadwake_metrics
from .policy import evaluate_threadwake_policy
from .types import (
    EphemeralResult,
    PromptGraph,
    PromptSegment,
    ThreadWakeMetadata,
    ThreadWakeMode,
    ThreadWakeObservation,
    ThreadWakeRequestConfig,
)

__all__ = [
    "BackendKVAdapterRegistry",
    "EntryStatus",
    "EphemeralResult",
    "FakeKVBackend",
    "KVCapability",
    "KVCapableBackend",
    "KVHandle",
    "NoOpKVBackendAdapter",
    "PromptGraph",
    "PromptSegment",
    "ScopeContext",
    "ThreadWakeIndex",
    "ThreadWakeIndexEntry",
    "ThreadWakeManager",
    "ThreadWakeMetadata",
    "ThreadWakeMetrics",
    "ThreadWakeMode",
    "ThreadWakeObservation",
    "ThreadWakeRequestConfig",
    "ThreadWakeStats",
    "build_threadwake_cache_key",
    "compile_prompt_graph",
    "evaluate_threadwake_policy",
    "get_threadwake_metrics",
]

