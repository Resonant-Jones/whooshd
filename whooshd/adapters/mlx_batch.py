"""MLX explicit batch generation feasibility probe.

Answers: can MLX-LM's batch_generate satisfy Whoosh'd's batch execution
contract?  This is probe-only — does not enable live path batching.
"""

from __future__ import annotations

from whooshd.batching import (
    RealBatchBackend,
    RealBatchFeasibilityReport,
    RealBatchFeasibilityStatus,
)
from whooshd.log_safety import exception_metadata


def probe_mlx_batch_generate_capability() -> RealBatchFeasibilityReport:
    """Probe whether MLX-LM's batch_generate API is available and
    compatible with Whoosh'd's batch execution contract.

    This probe checks import availability and API shape only.
    It does not load a model, render prompts, or execute inference.
    """
    notes: list[str] = []

    # Check import availability.
    try:
        from mlx_lm import batch_generate  # noqa: F401
    except ImportError:
        return RealBatchFeasibilityReport(
            backend=RealBatchBackend.MLX,
            status=RealBatchFeasibilityStatus.UNSUPPORTED,
            notes=("mlx_lm.batch_generate not importable",),
        )

    # Check function signature compatibility.
    try:
        import inspect
        sig = inspect.signature(batch_generate)
        params = sig.parameters
        notes.append(f"batch_generate parameters: {list(params.keys())}")

        # batch_generate(model, tokenizer, prompts, ...) signature check.
        has_model = "model" in params
        has_tokenizer = "tokenizer" in params
        has_prompts = "prompts" in params

        if has_model and has_tokenizer and has_prompts:
            notes.append("batch_generate accepts model, tokenizer, prompts")
        else:
            return RealBatchFeasibilityReport(
                backend=RealBatchBackend.MLX,
                status=RealBatchFeasibilityStatus.INCONCLUSIVE,
                explicit_batch_contract=False,
                notes=tuple(notes),
            )
    except Exception as exc:
        return RealBatchFeasibilityReport(
            backend=RealBatchBackend.MLX,
            status=RealBatchFeasibilityStatus.INCONCLUSIVE,
            notes=(f"signature inspection failed ({exception_metadata(exc)})",),
        )

    # API shape looks compatible.
    return RealBatchFeasibilityReport(
        backend=RealBatchBackend.MLX,
        status=RealBatchFeasibilityStatus.FEASIBLE,
        explicit_batch_contract=True,
        prompt_cache_supported=_check_batch_prompt_cache(batch_generate),
        notes=tuple(notes),
    )


def _check_batch_prompt_cache(batch_generate) -> bool:
    """Check if batch_generate supports prompt_cache kwarg."""
    try:
        import inspect
        src = inspect.getsource(batch_generate)
        return "prompt_cache" in src or "prompt_caches" in src
    except Exception:
        return False
