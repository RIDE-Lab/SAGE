from sage.serving.integrations.endpoint_pressure import (
    EndpointPressureCompiler,
    compile_with_endpoint_pressure,
    estimate_external_inflight,
)
from sage.serving.integrations.service_modes import (
    ApplicationServiceContract,
    ServiceMode,
    ServiceRuntimeSnapshot,
    validate_mode_decision,
)


GROUND = ServiceMode("grounded", expected_seconds=3.0, utility_rank=2, concurrency_limit=2)
RAPID = ServiceMode("rapid", expected_seconds=0.5, utility_rank=1, concurrency_limit=8)


def snapshot(*, running=0.0, waiting=0.0, grounded=0, rapid=0):
    return ServiceRuntimeSnapshot(
        num_requests_running=running,
        num_requests_waiting=waiting,
        kv_cache_usage_perc=0.0,
        reserved_by_mode={"grounded": grounded, "rapid": rapid},
        observed_at_ns=1,
    )


def contract(*modes):
    return ApplicationServiceContract(
        request_id="r1",
        service_class="rapid-response",
        deadline_seconds=8.0,
        legal_modes=modes,
    )


def test_external_estimate_subtracts_requests_already_reserved_locally():
    assert estimate_external_inflight(snapshot(running=3, waiting=2, grounded=1, rapid=2)) == 2
    assert estimate_external_inflight(snapshot(running=1, grounded=2)) == 0


def test_external_request_can_force_legal_fallback():
    decision = compile_with_endpoint_pressure(contract(GROUND, RAPID), snapshot(running=1, grounded=1))
    assert decision["external_inflight_estimate"] == 0
    assert decision["selected_mode"] == "grounded"

    decision = compile_with_endpoint_pressure(contract(GROUND, RAPID), snapshot(running=2, grounded=1))
    assert decision["external_inflight_estimate"] == 1
    assert decision["selected_mode"] == "rapid"
    assert decision["reason"] == "lower_utility_mode_due_endpoint_pressure"
    assert validate_mode_decision(contract(GROUND, RAPID), decision) == []


def test_grounded_only_contract_rejects_when_external_work_consumes_slot():
    decision = compile_with_endpoint_pressure(contract(GROUND), snapshot(running=2, grounded=1))
    assert decision["selected_mode"] is None
    assert decision["disposition"] == "reject"


def test_sticky_floor_survives_metrics_lag_until_local_drain():
    compiler = EndpointPressureCompiler()
    first = compiler(contract(GROUND, RAPID), snapshot(running=4), policy_identity="test")
    lagged = compiler(
        contract(GROUND, RAPID),
        snapshot(running=4, rapid=3),
        policy_identity="test",
    )
    reset = compiler(contract(GROUND, RAPID), snapshot(running=0), policy_identity="test")
    assert first["external_inflight_floor"] == 4
    assert lagged["external_inflight_floor"] == 4
    assert lagged["selected_mode"] == "rapid"
    assert reset["external_inflight_floor"] == 0

