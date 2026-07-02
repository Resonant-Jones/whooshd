"""Tests for MLX sampling isolation probe — metal or cardboard?"""

from whooshd.mlx_sampling_isolation import (
    MLXSamplingIsolationFailureReason,
    MLXSamplingIsolationReport,
    MLXSamplingIsolationStatus,
    MLXSamplingState,
    build_sampling_state_signature,
    build_stop_signature,
    normalize_mlx_sampling_kwargs,
    validate_sampling_states_are_isolated,
)


class TestReportMetadataOnly:
    def test_report_no_leakage(self):
        states = [
            normalize_mlx_sampling_kwargs(request_id="a", temperature=0.7),
            normalize_mlx_sampling_kwargs(request_id="b", temperature=0.9),
        ]
        r = validate_sampling_states_are_isolated(states)
        s = str(r)
        for f in ("raw_prompt", "rendered", "messages", "generated_text_full",
                   "token_ids_list", "cache_repr", "model_repr", "kv_handle"):
            assert f not in s.lower()


class TestSignatureStability:
    def test_identical_state_stable_signature(self):
        a = normalize_mlx_sampling_kwargs(request_id="a", temperature=0.7, max_tokens=256)
        b = normalize_mlx_sampling_kwargs(request_id="b", temperature=0.7, max_tokens=256)
        assert build_sampling_state_signature(a) == build_sampling_state_signature(b)

    def test_different_state_different_signature(self):
        a = normalize_mlx_sampling_kwargs(request_id="a", temperature=0.7)
        b = normalize_mlx_sampling_kwargs(request_id="b", temperature=0.9)
        assert build_sampling_state_signature(a) != build_sampling_state_signature(b)

    def test_different_max_tokens_different_signature(self):
        a = normalize_mlx_sampling_kwargs(request_id="a", max_tokens=64)
        b = normalize_mlx_sampling_kwargs(request_id="b", max_tokens=128)
        assert build_sampling_state_signature(a) != build_sampling_state_signature(b)


class TestStopSignature:
    def test_stop_signature_no_leakage(self):
        sig = build_stop_signature(["SECRET_A", "SECRET_B"])
        assert sig is not None
        assert "SECRET" not in sig

    def test_stop_signature_stable(self):
        a = build_stop_signature(["A", "B"])
        b = build_stop_signature(["A", "B"])
        assert a == b

    def test_stop_signature_different(self):
        a = build_stop_signature(["A"])
        b = build_stop_signature(["B"])
        assert a != b

    def test_stop_signature_none_for_empty(self):
        assert build_stop_signature([]) is None
        assert build_stop_signature(None) is None


class TestDuplicateRejection:
    def test_duplicate_request_id_fails(self):
        states = [
            normalize_mlx_sampling_kwargs(request_id="a"),
            normalize_mlx_sampling_kwargs(request_id="a"),
        ]
        r = validate_sampling_states_are_isolated(states)
        assert r.status == MLXSamplingIsolationStatus.FAILED
        assert r.failure_reason == MLXSamplingIsolationFailureReason.DUPLICATE_REQUEST_STATE


class TestBackendUnverified:
    def test_sampling_not_backend_verified(self):
        r = validate_sampling_states_are_isolated([
            normalize_mlx_sampling_kwargs(request_id="a"),
            normalize_mlx_sampling_kwargs(request_id="b"),
        ])
        assert r.sampling_backend_verified is False
        assert r.shared_decode_loop_verified is False
        assert r.production_ready is False
        assert r.live_path_enabled is False
        assert r.adapter_behavior_changed is False


class TestFrozenState:
    def test_state_is_frozen(self):
        state = normalize_mlx_sampling_kwargs(request_id="a", temperature=0.7)
        try:
            state.temperature = 0.9  # type: ignore[misc]
            assert False, "should have raised"
        except Exception:
            pass  # Expected — frozen dataclass


class TestGeneratedTextOptIn:
    def test_default_no_text(self):
        r = validate_sampling_states_are_isolated([
            normalize_mlx_sampling_kwargs(request_id="a"),
        ])
        assert r.generated_text_included is False
