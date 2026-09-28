from __future__ import annotations

from copy import deepcopy

from sage.serving.integrations.audit import (
    AUDIT_SCHEMA_VERSION,
    GENESIS_DIGEST,
    build_decision_certificate_chain,
    payload_digest,
    verify_decision_certificate_chain,
)


def _trace_row(request_id: str) -> dict[str, object]:
    return {
        "request_id": request_id,
        "decision_trace": {
            "decision": "admitted",
            "policy_action": "dispatch",
            "requested_max_tokens": 64,
            "effective_max_tokens": 16,
            "dispatch_delay_s": 0.0,
            "policy_reason": "priority_bypass",
            "observed_load": {"num_requests_running": 4.0},
        },
    }


def test_build_decision_certificate_chain_is_deterministic_and_linked() -> None:
    source = [_trace_row("r1"), _trace_row("r2")]
    certified, chain = build_decision_certificate_chain(
        source,
        policy_identity="baseline:vamos",
        policy_config={"mode": "load-aware", "max_deferral_sec": 2.0},
    )

    assert source[0]["decision_trace"].get("certificate") is None
    first = certified[0]["decision_trace"]["certificate"]
    second = certified[1]["decision_trace"]["certificate"]
    assert first["schema_version"] == AUDIT_SCHEMA_VERSION
    assert first["previous_digest"] == GENESIS_DIGEST
    assert second["previous_digest"] == first["record_digest"]
    assert chain["root_digest"] == second["record_digest"]
    assert chain["record_count"] == 2

    repeated, repeated_chain = build_decision_certificate_chain(
        source,
        policy_identity="baseline:vamos",
        policy_config={"mode": "load-aware", "max_deferral_sec": 2.0},
    )
    assert repeated == certified
    assert repeated_chain == chain


def test_record_digest_detects_decision_tampering() -> None:
    certified, _ = build_decision_certificate_chain(
        [_trace_row("r1")],
        policy_identity="baseline:vamos",
        policy_config={"mode": "load-aware"},
    )
    tampered = deepcopy(certified[0])
    certificate = tampered["decision_trace"]["certificate"]
    expected = certificate.pop("record_digest")
    tampered["decision_trace"]["effective_max_tokens"] = 64
    assert payload_digest(tampered) != expected


def test_chain_verifier_rejects_tampered_decision() -> None:
    certified, chain = build_decision_certificate_chain(
        [_trace_row("r1"), _trace_row("r2")],
        policy_identity="baseline:vamos",
        policy_config={"mode": "load-aware"},
    )
    assert verify_decision_certificate_chain(certified, chain) == []
    tampered = deepcopy(certified)
    tampered[0]["decision_trace"]["effective_max_tokens"] = 999
    assert "trace row 0 record digest mismatch" in verify_decision_certificate_chain(
        tampered, chain
    )
