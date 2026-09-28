from __future__ import annotations

from copy import deepcopy

from sage.serving.integrations.service_modes import (
    ApplicationServiceContract,
    ServiceMode,
    ServiceRuntimeSnapshot,
    compile_service_contract,
    parse_vllm_metrics,
    validate_mode_decision,
)


def _contract(*, grounded_only: bool = False) -> ApplicationServiceContract:
    modes = (
        ServiceMode("evidence-grounded-verdict", 3.3, 2, 2),
    )
    if not grounded_only:
        modes += (ServiceMode("rapid-verdict", 0.55, 1, 37),)
    return ApplicationServiceContract(
        request_id="fever-1",
        service_class="evidence-required" if grounded_only else "rapid-response",
        deadline_seconds=8.3,
        legal_modes=modes,
    )


def _snapshot(grounded: int) -> ServiceRuntimeSnapshot:
    return ServiceRuntimeSnapshot(
        num_requests_running=float(grounded),
        num_requests_waiting=0.0,
        kv_cache_usage_perc=0.1,
        reserved_by_mode={"evidence-grounded-verdict": grounded, "rapid-verdict": 0},
        observed_at_ns=1,
    )


def test_compiler_preserves_grounded_quality_when_capacity_is_available() -> None:
    contract = _contract()
    decision = compile_service_contract(contract, _snapshot(0), policy_identity="vamos:v1")
    assert decision["selected_mode"] == "evidence-grounded-verdict"
    assert decision["disposition"] == "dispatch"
    assert validate_mode_decision(contract, decision) == []


def test_compiler_selects_rapid_when_grounded_capacity_is_reserved() -> None:
    decision = compile_service_contract(_contract(), _snapshot(2), policy_identity="vamos:v1")
    assert decision["selected_mode"] == "rapid-verdict"
    assert decision["reason"] == "lower_utility_mode_due_live_capacity"


def test_grounded_only_contract_rejects_when_capacity_is_reserved() -> None:
    decision = compile_service_contract(
        _contract(grounded_only=True), _snapshot(2), policy_identity="vamos:v1"
    )
    assert decision["selected_mode"] is None
    assert decision["disposition"] == "reject"


def test_decision_digest_detects_mode_tampering() -> None:
    contract = _contract()
    decision = compile_service_contract(contract, _snapshot(0), policy_identity="vamos:v1")
    tampered = deepcopy(decision)
    tampered["selected_mode"] = "rapid-verdict"
    assert "decision digest mismatch" in validate_mode_decision(contract, tampered)


def test_parse_vllm_metrics_aggregates_engine_rows() -> None:
    metrics = parse_vllm_metrics(
        """
vllm:num_requests_running{engine="0"} 2
vllm:num_requests_running{engine="1"} 1
vllm:num_requests_waiting{engine="0"} 4
vllm:kv_cache_usage_perc{engine="0"} 0.25
"""
    )
    assert metrics == {
        "num_requests_running": 3.0,
        "num_requests_waiting": 4.0,
        "kv_cache_usage_perc": 0.25,
    }
