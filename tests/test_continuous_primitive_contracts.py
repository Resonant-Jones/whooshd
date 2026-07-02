"""Tests for continuous batching primitive contracts — six locked doors."""

from whooshd.continuous_primitives import (
    CancellationHookContract,
    CancellationPhase,
    CleanupHookContract,
    CleanupReason,
    ContinuousPrimitiveReadinessReport,
    ContinuousPrimitiveReport,
    FailureIsolationContract,
    FailureScope,
    PrimitiveInvariantViolation,
    PrimitiveStatus,
    SamplingStateContract,
    SlotOwnershipClaim,
    SlotReleaseClaim,
    TimeoutHookContract,
    TimeoutPhase,
    build_primitive_readiness_report,
    validate_cancellation_contract,
    validate_cleanup_contract,
    validate_failure_isolation_contract,
    validate_sampling_state_isolation,
    validate_slot_ownership_claims,
    validate_timeout_contract,
)


class TestReportsMetadataOnly:
    def test_report_no_leakage(self):
        r = ContinuousPrimitiveReport(primitive="slot_ownership")
        s = str(r)
        for f in ("prompt", "token_ids", "generated_text", "cache", "model_repr", "kv_handle"):
            assert f not in s.lower()

    def test_all_reports_not_production_ready(self):
        for p in ("slot_ownership", "cancellation_hook", "timeout_hook",
                   "sampling_state", "failure_isolation", "cleanup_hook"):
            r = ContinuousPrimitiveReport(primitive=p)
            assert r.production_ready is False
            assert r.live_path_enabled is False


class TestSlotOwnership:
    def test_duplicate_slot_rejected(self):
        claims = [
            SlotOwnershipClaim("a", "s1", "mlx", 1.0),
            SlotOwnershipClaim("b", "s1", "mlx", 2.0),
        ]
        v = validate_slot_ownership_claims(claims)
        assert PrimitiveInvariantViolation.SLOT_ALREADY_OWNED in v

    def test_duplicate_request_rejected(self):
        claims = [
            SlotOwnershipClaim("a", "s1", "mlx", 1.0),
            SlotOwnershipClaim("a", "s2", "mlx", 2.0),
        ]
        v = validate_slot_ownership_claims(claims)
        assert PrimitiveInvariantViolation.REQUEST_ALREADY_HAS_SLOT in v

    def test_released_slot_clears_owner(self):
        claims = [SlotOwnershipClaim("a", "s1", "mlx", 1.0)]
        releases = [SlotReleaseClaim("a", "s1", "done", 2.0)]
        v = validate_slot_ownership_claims(claims, releases)
        assert len(v) == 0

    def test_release_wrong_owner_violated(self):
        claims = [SlotOwnershipClaim("a", "s1", "mlx", 1.0)]
        releases = [SlotReleaseClaim("b", "s1", "wrong", 2.0)]
        v = validate_slot_ownership_claims(claims, releases)
        assert PrimitiveInvariantViolation.RELEASED_SLOT_RETAINED_OWNER in v


class TestCancellation:
    def test_decode_cancel_forbids_output(self):
        c = CancellationHookContract(CancellationPhase.DURING_DECODE, output_after_cancel_allowed=True)
        v = validate_cancellation_contract(c)
        assert PrimitiveInvariantViolation.CANCELLED_EMITTED_OUTPUT in v

    def test_before_prefill_cancel_ok(self):
        c = CancellationHookContract(CancellationPhase.BEFORE_PREFILL)
        assert len(validate_cancellation_contract(c)) == 0


class TestTimeout:
    def test_decode_timeout_forbids_output(self):
        c = TimeoutHookContract(TimeoutPhase.DURING_DECODE, output_after_timeout_allowed=True)
        v = validate_timeout_contract(c)
        assert PrimitiveInvariantViolation.TIMED_OUT_EMITTED_OUTPUT in v


class TestSampling:
    def test_duplicate_request_id_rejected(self):
        states = [
            SamplingStateContract("a"), SamplingStateContract("a"),
        ]
        v = validate_sampling_state_isolation(states)
        assert PrimitiveInvariantViolation.SAMPLING_STATE_MISMATCH in v

    def test_unique_requests_ok(self):
        v = validate_sampling_state_isolation([SamplingStateContract("a"), SamplingStateContract("b")])
        assert len(v) == 0


class TestFailureIsolation:
    def test_non_per_request_must_declare_affected(self):
        c = FailureIsolationContract(FailureScope.WHOLE_DECODE_STEP)
        v = validate_failure_isolation_contract(c)
        assert PrimitiveInvariantViolation.FAILURE_ESCALATION_UNDECLARED in v

    def test_per_request_isolation_ok(self):
        c = FailureIsolationContract(FailureScope.PER_REQUEST, failed_request_id="a")
        assert len(validate_failure_isolation_contract(c)) == 0


class TestCleanup:
    def test_cleanup_must_be_idempotent(self):
        c = CleanupHookContract(idempotent=False)
        v = validate_cleanup_contract(c)
        assert PrimitiveInvariantViolation.CLEANUP_NOT_IDEMPOTENT in v

    def test_cleanup_ok(self):
        c = CleanupHookContract()
        assert len(validate_cleanup_contract(c)) == 0


class TestReadiness:
    def test_all_contracts_still_not_production_ready(self):
        reports = [ContinuousPrimitiveReport(primitive=p) for p in (
            "slot_ownership", "cancellation_hook", "timeout_hook",
            "sampling_state", "failure_isolation", "cleanup_hook",
        )]
        r = build_primitive_readiness_report("mlx", reports)
        assert r.all_contracts_defined is True
        assert r.all_backend_verified is False
        assert r.production_ready is False
        assert r.live_path_enabled is False
        assert len(r.blocking_primitives) == 6

    def test_missing_primitive_blocks(self):
        reports = [ContinuousPrimitiveReport(primitive="slot_ownership")]
        r = build_primitive_readiness_report("mlx", reports)
        assert r.all_contracts_defined is False
        assert len(r.blocking_primitives) >= 5
