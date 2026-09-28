"""Experimental endpoint-pressure policy for VAMOS service modes.

The archived reservation-only compiler intentionally remains unchanged. This
module adds a separately identified policy that accounts for endpoint requests
which are visible in vLLM metrics but absent from the carrier's local
reservations, as can happen with bypass traffic or another gateway.
"""

from __future__ import annotations

import math
import threading
from typing import Any

from .service_modes import (
    ApplicationServiceContract,
    ServiceMode,
    ServiceRuntimeSnapshot,
    _digest,
)


POLICY_IDENTITY = "sage:vamos-endpoint-pressure-v1"


def estimate_external_inflight(snapshot: ServiceRuntimeSnapshot) -> int:
    """Conservatively estimate requests not represented by local reservations."""
    observed = math.ceil(snapshot.num_requests_running + snapshot.num_requests_waiting)
    locally_reserved = sum(int(value) for value in snapshot.reserved_by_mode.values())
    return max(0, observed - locally_reserved)


def compile_with_endpoint_pressure(
    contract: ApplicationServiceContract,
    snapshot: ServiceRuntimeSnapshot,
    *,
    policy_identity: str = POLICY_IDENTITY,
) -> dict[str, Any]:
    """Select a legal mode after charging unowned endpoint work to every mode.

    The endpoint metrics do not identify which semantic mode produced an
    external request. Charging the estimate to every candidate is deliberately
    conservative and prevents unknown bypass work from being treated as free
    capacity. Local reservations still close the metrics-scrape race.
    """
    ranked = sorted(contract.legal_modes, key=lambda mode: (-mode.utility_rank, mode.mode_id))
    external_inflight = estimate_external_inflight(snapshot)
    candidates = []
    selected: ServiceMode | None = None
    for mode in ranked:
        local_reserved = int(snapshot.reserved_by_mode.get(mode.mode_id, 0))
        effective_reserved = local_reserved + external_inflight
        slot_available = effective_reserved < mode.concurrency_limit
        deadline_feasible = mode.expected_seconds <= contract.deadline_seconds
        feasible = slot_available and deadline_feasible
        candidates.append({
            "mode_id": mode.mode_id,
            "utility_rank": mode.utility_rank,
            "expected_seconds": mode.expected_seconds,
            "local_reserved": local_reserved,
            "external_inflight_estimate": external_inflight,
            "effective_reserved": effective_reserved,
            "concurrency_limit": mode.concurrency_limit,
            "slot_available": slot_available,
            "deadline_feasible": deadline_feasible,
            "feasible": feasible,
        })
        if selected is None and feasible:
            selected = mode

    if selected is not None:
        disposition = "dispatch"
        reason = (
            "highest_utility_feasible"
            if selected.mode_id == ranked[0].mode_id
            else "lower_utility_mode_due_endpoint_pressure"
        )
    elif "defer" in contract.allowed_dispositions:
        disposition = "defer"
        reason = "no_mode_currently_feasible"
    elif "reject" in contract.allowed_dispositions:
        disposition = "reject"
        reason = "no_mode_currently_feasible"
    else:
        raise ValueError("contract permits no legal disposition for an infeasible request")

    decision = {
        "schema": "sage.service-mode-decision.v1",
        "request_id": contract.request_id,
        "policy_identity": policy_identity,
        "requested_contract_digest": contract.digest,
        "runtime_snapshot": snapshot.to_dict(),
        "external_inflight_estimate": external_inflight,
        "candidates": candidates,
        "selected_mode": selected.mode_id if selected else None,
        "disposition": disposition,
        "reason": reason,
    }
    decision["decision_digest"] = _digest(decision)
    return decision


class EndpointPressureCompiler:
    """Stateful compiler that closes the metrics/reservation visibility race.

    A newly reserved local request may not yet appear in the next metrics
    scrape. Subtracting all local reservations would then incorrectly erase
    known external work. While any local request is reserved, retain the
    largest external lower bound observed in that carrier busy period. Once
    local reservations drain to zero, metrics establish a fresh baseline.
    """

    def __init__(self, *, policy_identity: str = POLICY_IDENTITY) -> None:
        self.policy_identity = policy_identity
        self.external_floor = 0
        self._lock = threading.Lock()

    def __call__(
        self,
        contract: ApplicationServiceContract,
        snapshot: ServiceRuntimeSnapshot,
        *,
        policy_identity: str | None = None,
    ) -> dict[str, Any]:
        observed = math.ceil(snapshot.num_requests_running + snapshot.num_requests_waiting)
        locally_reserved = sum(int(value) for value in snapshot.reserved_by_mode.values())
        lower_bound = max(0, observed - locally_reserved)
        with self._lock:
            if locally_reserved == 0:
                self.external_floor = observed
            else:
                self.external_floor = max(self.external_floor, lower_bound)
            external_floor = self.external_floor
        adjusted = ServiceRuntimeSnapshot(
            num_requests_running=float(external_floor + locally_reserved),
            num_requests_waiting=0.0,
            kv_cache_usage_perc=snapshot.kv_cache_usage_perc,
            reserved_by_mode=snapshot.reserved_by_mode,
            observed_at_ns=snapshot.observed_at_ns,
            source=snapshot.source + "+sticky-external-floor",
        )
        decision = compile_with_endpoint_pressure(
            contract,
            adjusted,
            policy_identity=policy_identity or self.policy_identity,
        )
        decision["observed_runtime_snapshot"] = snapshot.to_dict()
        decision["external_inflight_floor"] = external_floor
        unsigned = dict(decision)
        unsigned.pop("decision_digest")
        decision["decision_digest"] = _digest(unsigned)
        return decision

