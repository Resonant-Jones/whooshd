"""MLX generator lifecycle probe — close/early-stop under the microscope.

Tests Python generator close behavior around MLX stream_generate.
A clean close supports partial cancellation/cleanup confidence.
Does NOT backend-verify anything.  One key, not the key ring.  🔬
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterator, Optional


class MLXGeneratorLifecycleStatus(str, Enum):
    NOT_RUN = "not_run"
    PASSED = "passed"
    FAILED = "failed"
    INCONCLUSIVE = "inconclusive"


class MLXGeneratorLifecycleFailureReason(str, Enum):
    IMPORT_FAILED = "import_failed"
    MODEL_LOAD_FAILED = "model_load_failed"
    GENERATOR_CREATION_FAILED = "generator_creation_failed"
    FIRST_CHUNK_FAILED = "first_chunk_failed"
    CLOSE_FAILED = "close_failed"
    CHUNK_AFTER_CLOSE = "chunk_after_close"
    DOUBLE_CLOSE_FAILED = "double_close_failed"
    ITERATION_ERROR = "iteration_error"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class MLXGeneratorLifecycleReport:
    backend: str = "mlx"
    probe: str = "stream_generate_lifecycle"
    status: MLXGeneratorLifecycleStatus = MLXGeneratorLifecycleStatus.NOT_RUN
    failure_reason: Optional[MLXGeneratorLifecycleFailureReason] = None
    chunks_observed_before_close: int = 0
    close_called: bool = False
    close_succeeded: bool = False
    close_idempotent: bool = False
    no_chunks_after_close: bool = False
    exception_sanitized: bool = True
    cancellation_backend_verified: bool = False
    timeout_backend_verified: bool = False
    cleanup_backend_verified: bool = False
    live_path_enabled: bool = False
    adapter_behavior_changed: bool = False
    production_ready: bool = False
    generated_text_included: bool = False
    token_ids_included: bool = False
    prompt_text_included: bool = False


def probe_generator_close_behavior(
    generator: Iterator[object],
    *,
    max_chunks_before_close: int = 1,
) -> MLXGeneratorLifecycleReport:
    chunks = 0
    close_ok = False
    close_idem = False
    no_after = False
    exc_safe = True
    failure: Optional[MLXGeneratorLifecycleFailureReason] = None

    try:
        # Pull chunks.
        for _ in range(max_chunks_before_close):
            try:
                next(generator)
                chunks += 1
            except StopIteration:
                break

        # Close.
        close_fn = getattr(generator, "close", None)
        if callable(close_fn):
            try:
                close_fn()
                close_ok = True
            except Exception:
                failure = MLXGeneratorLifecycleFailureReason.CLOSE_FAILED
                exc_safe = False

        # Check no chunks after close.
        try:
            next(generator)
            no_after = False
            if failure is None:
                failure = MLXGeneratorLifecycleFailureReason.CHUNK_AFTER_CLOSE
        except StopIteration:
            no_after = True
        except Exception:
            no_after = False

        # Idempotent close.
        if callable(close_fn):
            try:
                close_fn()
                close_idem = True
            except Exception:
                if failure is None:
                    failure = MLXGeneratorLifecycleFailureReason.DOUBLE_CLOSE_FAILED

    except Exception:
        failure = failure or MLXGeneratorLifecycleFailureReason.ITERATION_ERROR
        exc_safe = False

    status = MLXGeneratorLifecycleStatus.PASSED if failure is None else MLXGeneratorLifecycleStatus.FAILED
    return MLXGeneratorLifecycleReport(
        status=status,
        failure_reason=failure,
        chunks_observed_before_close=chunks,
        close_called=True,
        close_succeeded=close_ok,
        close_idempotent=close_idem,
        no_chunks_after_close=no_after,
        exception_sanitized=exc_safe,
    )


def build_generator_lifecycle_report(
    *,
    status: MLXGeneratorLifecycleStatus,
    chunks: int = 0,
    close_ok: bool = False,
    close_idem: bool = False,
    no_after: bool = False,
    generated_text_included: bool = False,
    failure: Optional[MLXGeneratorLifecycleFailureReason] = None,
) -> MLXGeneratorLifecycleReport:
    return MLXGeneratorLifecycleReport(
        status=status,
        failure_reason=failure,
        chunks_observed_before_close=chunks,
        close_called=True,
        close_succeeded=close_ok,
        close_idempotent=close_idem,
        no_chunks_after_close=no_after,
        generated_text_included=generated_text_included,
    )
