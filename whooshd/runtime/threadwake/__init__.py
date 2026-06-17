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
from .kv_lifecycle import KVEvent, KVLifecycleObserver, KVLifecycleStats
from .manager import ThreadWakeManager
from .mlx_tokenizer import MLXInProcessTokenizerAdapter
from .metrics import ThreadWakeMetrics, get_threadwake_metrics
from .policy import evaluate_threadwake_policy
from .candidate_selection import (
    CandidateConfidence,
    CandidateScore,
    CandidateSelectionReason,
    SnapshotCandidate,
    SnapshotCandidateSelector,
    SnapshotSelectionResult,
)
from .prefix_proof import PrefixProof, PrefixMismatchReason, StablePrefixProofEngine
from .replay_analysis import CandidateReplayAnalyzer, CandidateReplayRecord, CandidateReplaySummary
from .storage import (
    NoOpThreadWakeStorage,
    SQLiteThreadWakeStorage,
    ThreadWakeStorageProtocol,
)
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
    "CandidateConfidence",
    "CandidateScore",
    "CandidateSelectionReason",
    "CodexifySegmentMeta",
    "CodexifySegmentMetadata",
    "EntryStatus",
    "EphemeralResult",
    "FakeKVBackend",
    "FakeTokenizerAdapter",
    "ForwardingTokenizerAdapterStub",
    "KVCapability",
    "KVCapableBackend",
    "KVEvent",
    "KVHandle",
    "KVLifecycleObserver",
    "KVLifecycleStats",
    "LlamaCppTokenizerAdapterStub",
    "MLXInProcessTokenizerAdapter",
    "MlxLmServerTokenizerAdapterStub",
    "MLXTokenizerAdapterStub",
    "MlxVlmTokenizerAdapterStub",
    "NoOpKVBackendAdapter",
    "NoOpTokenizerAdapter",
    "CandidateReplayAnalyzer",
    "CandidateReplayRecord",
    "CandidateReplaySummary",
    "PrefixMismatchReason",
    "PrefixProof",
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
    "SnapshotCandidate",
    "SnapshotCandidateSelector",
    "SnapshotSelectionResult",
    "SQLiteThreadWakeStorage",
    "StablePrefixProofEngine",
    "ThreadWakeStats",
    "ThreadWakeTokenizerCapability",
    "TokenSpan",
    "ThreadWakeStorageProtocol",
    "TokenizedPrompt",
    "build_threadwake_cache_key",
    "compile_prompt_graph",
    "evaluate_threadwake_policy",
    "get_threadwake_metrics",
]

