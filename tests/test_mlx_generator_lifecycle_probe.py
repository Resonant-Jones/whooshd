"""Tests for MLX generator lifecycle probe — fake generators, real invariants."""

from whooshd.mlx_generator_lifecycle import (
    MLXGeneratorLifecycleFailureReason,
    MLXGeneratorLifecycleReport,
    MLXGeneratorLifecycleStatus,
    build_generator_lifecycle_report,
    probe_generator_close_behavior,
)


def _clean_gen(chunks=1):
    for i in range(chunks):
        yield f"chunk_{i}"


def _bad_close_gen():
    yield "ok"
    def _close():
        raise RuntimeError("simulated close failure")
    g = _bad_close_gen
    g.close = _close
    yield "ok"


class _BadCloseGenerator:
    def __init__(self):
        self.i = 0

    def __iter__(self):
        return self

    def __next__(self):
        if self.i < 2:
            self.i += 1
            return f"chunk_{self.i}"
        raise StopIteration

    def close(self):
        raise RuntimeError("simulated")


def _naughty_gen():
    yield "before"
    # No close method — yields after iteration ends.


def _error_gen():
    yield "first"
    raise RuntimeError("simulated iteration error")


class TestReportMetadataOnly:
    def test_report_no_leakage(self):
        r = build_generator_lifecycle_report(status=MLXGeneratorLifecycleStatus.PASSED)
        s = str(r)
        for f in ("raw_prompt", "rendered", "messages", "generated_text_full",
                   "token_ids_list", "cache_repr", "model_repr", "tokenizer_repr", "kv_handle"):
            assert f not in s.lower()


class TestCleanClose:
    def test_clean_close_passes(self):
        r = probe_generator_close_behavior(_clean_gen(3), max_chunks_before_close=1)
        assert r.status == MLXGeneratorLifecycleStatus.PASSED
        assert r.close_called is True
        assert r.close_succeeded is True
        assert r.close_idempotent is True
        assert r.no_chunks_after_close is True
        assert r.chunks_observed_before_close == 1

    def test_double_close_idempotent(self):
        r = probe_generator_close_behavior(_clean_gen(3), max_chunks_before_close=2)
        assert r.close_idempotent is True


class TestCloseFailure:
    def test_close_failure_sanitized(self):
        r = probe_generator_close_behavior(_BadCloseGenerator(), max_chunks_before_close=1)
        assert r.status == MLXGeneratorLifecycleStatus.FAILED
        assert r.failure_reason == MLXGeneratorLifecycleFailureReason.CLOSE_FAILED
        assert r.exception_sanitized is False

    def test_chunk_after_close_fails(self):
        # Custom iterator that ignores close and keeps yielding.
        class _LeakyIter:
            def __init__(self): self.i = 0
            def __iter__(self): return self
            def __next__(self):
                self.i += 1
                if self.i > 3: raise StopIteration
                return f"c{self.i}"
            def close(self): pass  # No-op close
        r = probe_generator_close_behavior(_LeakyIter(), max_chunks_before_close=1)
        assert r.status == MLXGeneratorLifecycleStatus.FAILED
        assert r.failure_reason == MLXGeneratorLifecycleFailureReason.CHUNK_AFTER_CLOSE


class TestIterationError:
    def test_iteration_error_sanitized(self):
        r = probe_generator_close_behavior(_error_gen(), max_chunks_before_close=2)
        assert r.status == MLXGeneratorLifecycleStatus.FAILED
        assert r.exception_sanitized is False


class TestBackendVerificationUnchanged:
    def test_cancellation_not_verified(self):
        r = build_generator_lifecycle_report(status=MLXGeneratorLifecycleStatus.PASSED)
        assert r.cancellation_backend_verified is False
        assert r.timeout_backend_verified is False
        assert r.cleanup_backend_verified is False
        assert r.production_ready is False
        assert r.live_path_enabled is False
        assert r.adapter_behavior_changed is False


class TestGeneratedTextOptIn:
    def test_default_no_text(self):
        r = build_generator_lifecycle_report(status=MLXGeneratorLifecycleStatus.PASSED)
        assert r.generated_text_included is False

    def test_opt_in_text(self):
        r = build_generator_lifecycle_report(status=MLXGeneratorLifecycleStatus.PASSED, generated_text_included=True)
        assert r.generated_text_included is True
