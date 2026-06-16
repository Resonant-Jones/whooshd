"""ThreadWake observe-mode foundation."""

from .backend import BackendKVAdapterRegistry, FakeKVBackend, KVCapableBackend, NoOpKVBackendAdapter
from .compiler import compile_prompt_graph
from .handles import KVCapability, KVHandle
from .index import (
    EntryStatus,
    ScopeContext,
    ThreadTip,
    ThreadWakeIndex,
    ThreadWakeIndexEntry,
    ThreadWakeStats,
)
from .keys import build_threadwake_cache_key
from .manager import ThreadWakeManager
from .metrics import ThreadWakeMetrics, get_threadwake_metrics
from .policy import evaluate_threadwake_policy
from .tokenization import (
    BackendTokenizerAdapter,
    BackendTokenizerAdapterRegistry,
    FakeTokenizerAdapter,
    ForwardingTokenizerAdapterStub,
    LlamaCppTokenizerAdapterStub,
    MlxLmServerTokenizerAdapterStub,
    MLXTokenizerAdapterStub,
    MlxVlmTokenizerAdapterStub,
    NoOpTokenizerAdapter,
    TokenSpan,
    TokenizedPrompt,
    ThreadWakeTokenizerCapability,
)
from .types import (
    CodexifySegmentMeta,
    CodexifySegmentMetadata,
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
    "BackendTokenizerAdapter",
    "BackendTokenizerAdapterRegistry",
    "CodexifySegmentMeta",
    "CodexifySegmentMetadata",
    "EntryStatus",
    "EphemeralResult",
    "FakeKVBackend",
    "FakeTokenizerAdapter",
    "ForwardingTokenizerAdapterStub",
    "KVCapability",
    "KVCapableBackend",
    "KVHandle",
    "LlamaCppTokenizerAdapterStub",
    "MlxLmServerTokenizerAdapterStub",
    "MLXTokenizerAdapterStub",
    "MlxVlmTokenizerAdapterStub",
    "NoOpKVBackendAdapter",
    "NoOpTokenizerAdapter",
    "PromptGraph",
    "PromptSegment",
    "ScopeContext",
    "ThreadTip",
    "ThreadWakeIndex",
    "ThreadWakeIndexEntry",
    "ThreadWakeManager",
    "ThreadWakeMetadata",
    "ThreadWakeMetrics",
    "ThreadWakeMode",
    "ThreadWakeObservation",
    "ThreadWakeRequestConfig",
    "ThreadWakeStats",
    "ThreadWakeTokenizerCapability",
    "TokenSpan",
    "TokenizedPrompt",
    "build_threadwake_cache_key",
    "compile_prompt_graph",
    "evaluate_threadwake_policy",
    "get_threadwake_metrics",
]

