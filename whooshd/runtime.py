"""Stubbed runtime state manager.

In v0 this holds typed-but-static state so the API contracts can be validated.
Later phases will replace this with real model loading, memory monitoring, and
scheduler logic.
"""

from __future__ import annotations

import time
from typing import Optional

from whooshd.contracts import (
    ConcurrencyBudget,
    LoadedModelInfo,
    MemoryInfo,
    MemoryPressure,
    ModelCapability,
    ModelInfo,
    OllamaTagEntry,
    OllamaTagsResponse,
    OpenAIModelEntry,
    OpenAIModelListResponse,
    RunnerStatus,
    RuntimeResponse,
)

# Synthetic creation timestamp for stub models.
_STUB_MODEL_CREATED = 1700000000
# Synthetic size in bytes for stub models (~1.5 GB for a 1.5B 4-bit model).
_STUB_MODEL_SIZE = 1_500_000_000


class RuntimeState:
    """Singleton holder for the runner's live state.

    Every field exposed through the API lives here so the HTTP layer stays thin.
    """

    def __init__(self) -> None:
        self._started_at: float = time.monotonic()

        # ── Runner status ───────────────────────────────────────────────
        self.status: RunnerStatus = RunnerStatus.READY

        # ── Memory (stubbed) ────────────────────────────────────────────
        self.memory = MemoryInfo(
            pressure=MemoryPressure.NORMAL,
            total_gb=32.0,
            used_gb=4.2,
            available_gb=27.8,
        )

        # ── Models (stubbed) ────────────────────────────────────────────
        self._models: dict[str, ModelInfo] = {
            "qwen2.5-1.5b-instruct-mlx": ModelInfo(
                id="qwen2.5-1.5b-instruct-mlx",
                loaded=False,
                capabilities=[
                    ModelCapability.CHAT,
                    ModelCapability.STREAMING,
                    ModelCapability.JSON,
                ],
                max_concurrent_jobs=2,
                context_window=32768,
                quantization="4bit",
                memory_class="small",
            ),
        }

        # ── Loaded-model snapshots (stubbed) ────────────────────────────
        self._loaded_snapshots: list[LoadedModelInfo] = []

        # ── Concurrency (stubbed) ───────────────────────────────────────
        self.concurrency = ConcurrencyBudget(
            max_active_jobs=1,
            estimated_safe_concurrency=1,
            queue_capacity=32,
        )

        # ── Queue / jobs (stubbed — real scheduler later) ────────────────
        self.queue_depth: int = 0
        self.active_jobs: int = 0
        self.active_model: Optional[str] = None

    # ── API builders ────────────────────────────────────────────────────

    @property
    def uptime_seconds(self) -> float:
        return time.monotonic() - self._started_at

    def build_runtime_response(self) -> RuntimeResponse:
        return RuntimeResponse(
            memory=self.memory,
            loaded_models=list(self._loaded_snapshots),
            concurrency=self.concurrency,
            uptime_seconds=self.uptime_seconds,
        )

    def list_models(self) -> list[ModelInfo]:
        return list(self._models.values())

    def get_model(self, model_id: str) -> Optional[ModelInfo]:
        return self._models.get(model_id)

    # ── OpenAI-compatible model list ────────────────────────────────────

    def build_openai_model_list(self) -> OpenAIModelListResponse:
        """Return registered models in OpenAI /v1/models format."""
        entries: list[OpenAIModelEntry] = []
        for m in self._models.values():
            entries.append(
                OpenAIModelEntry(
                    id=m.id,
                    created=_STUB_MODEL_CREATED,
                    owned_by="whooshd",
                )
            )
        return OpenAIModelListResponse(data=entries)

    # ── Ollama-compatible tags ───────────────────────────────────────────

    def build_ollama_tags(self) -> OllamaTagsResponse:
        """Return registered models in Ollama /api/tags format."""
        entries: list[OllamaTagEntry] = []
        for m in self._models.values():
            entries.append(
                OllamaTagEntry(
                    name=f"{m.id}:latest",
                    modified_at="2024-01-01T00:00:00Z",
                    size=_STUB_MODEL_SIZE,
                )
            )
        return OllamaTagsResponse(models=entries)


# Module-level singleton for the app layer to import.
_runtime: Optional[RuntimeState] = None


def get_runtime() -> RuntimeState:
    global _runtime
    if _runtime is None:
        _runtime = RuntimeState()
    return _runtime
