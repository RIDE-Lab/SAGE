from __future__ import annotations

import hashlib
import json
import time
import urllib.error
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


SERVICE_CONTRACT_SCHEMA = "sage.application-service-contract.v1"
RUNTIME_SNAPSHOT_SCHEMA = "sage.service-runtime-snapshot.v1"
MODE_DECISION_SCHEMA = "sage.service-mode-decision.v1"


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ServiceMode:
    mode_id: str
    expected_seconds: float
    utility_rank: int
    concurrency_limit: int

    def __post_init__(self) -> None:
        if not self.mode_id:
            raise ValueError("mode_id must be non-empty")
        if self.expected_seconds <= 0:
            raise ValueError("expected_seconds must be positive")
        if self.concurrency_limit <= 0:
            raise ValueError("concurrency_limit must be positive")

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode_id": self.mode_id,
            "expected_seconds": self.expected_seconds,
            "utility_rank": self.utility_rank,
            "concurrency_limit": self.concurrency_limit,
        }


@dataclass(frozen=True)
class ApplicationServiceContract:
    request_id: str
    service_class: str
    deadline_seconds: float
    legal_modes: tuple[ServiceMode, ...]
    allowed_dispositions: tuple[str, ...] = ("dispatch", "reject")
    schema: str = SERVICE_CONTRACT_SCHEMA

    def __post_init__(self) -> None:
        if not self.request_id or not self.service_class:
            raise ValueError("request_id and service_class must be non-empty")
        if self.deadline_seconds <= 0:
            raise ValueError("deadline_seconds must be positive")
        if not self.legal_modes:
            raise ValueError("legal_modes must be non-empty")
        mode_ids = [mode.mode_id for mode in self.legal_modes]
        if len(mode_ids) != len(set(mode_ids)):
            raise ValueError("legal mode IDs must be unique")
        allowed = {"dispatch", "defer", "reject"}
        if not self.allowed_dispositions or set(self.allowed_dispositions) - allowed:
            raise ValueError("allowed_dispositions contains an unsupported value")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "request_id": self.request_id,
            "service_class": self.service_class,
            "deadline_seconds": self.deadline_seconds,
            "legal_modes": [mode.to_dict() for mode in self.legal_modes],
            "allowed_dispositions": list(self.allowed_dispositions),
        }

    @property
    def digest(self) -> str:
        return _digest(self.to_dict())


@dataclass(frozen=True)
class ServiceRuntimeSnapshot:
    num_requests_running: float
    num_requests_waiting: float
    kv_cache_usage_perc: float
    reserved_by_mode: Mapping[str, int]
    observed_at_ns: int
    source: str = "vllm-metrics+carrier-reservations"
    schema: str = RUNTIME_SNAPSHOT_SCHEMA

    def __post_init__(self) -> None:
        for field_name in (
            "num_requests_running",
            "num_requests_waiting",
            "kv_cache_usage_perc",
        ):
            if float(getattr(self, field_name)) < 0:
                raise ValueError(f"{field_name} must be non-negative")
        if any(int(value) < 0 for value in self.reserved_by_mode.values()):
            raise ValueError("mode reservations must be non-negative")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "num_requests_running": self.num_requests_running,
            "num_requests_waiting": self.num_requests_waiting,
            "kv_cache_usage_perc": self.kv_cache_usage_perc,
            "reserved_by_mode": dict(sorted(self.reserved_by_mode.items())),
            "observed_at_ns": self.observed_at_ns,
            "source": self.source,
        }


def compile_service_contract(
    contract: ApplicationServiceContract,
    snapshot: ServiceRuntimeSnapshot,
    *,
    policy_identity: str,
) -> dict[str, Any]:
    ranked = sorted(
        contract.legal_modes,
        key=lambda mode: (-mode.utility_rank, mode.mode_id),
    )
    candidates = []
    selected: ServiceMode | None = None
    for mode in ranked:
        reserved = int(snapshot.reserved_by_mode.get(mode.mode_id, 0))
        slot_available = reserved < mode.concurrency_limit
        deadline_feasible = mode.expected_seconds <= contract.deadline_seconds
        feasible = slot_available and deadline_feasible
        candidates.append({
            "mode_id": mode.mode_id,
            "utility_rank": mode.utility_rank,
            "expected_seconds": mode.expected_seconds,
            "reserved": reserved,
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
            else "lower_utility_mode_due_live_capacity"
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
        "schema": MODE_DECISION_SCHEMA,
        "request_id": contract.request_id,
        "policy_identity": policy_identity,
        "requested_contract_digest": contract.digest,
        "runtime_snapshot": snapshot.to_dict(),
        "candidates": candidates,
        "selected_mode": selected.mode_id if selected else None,
        "disposition": disposition,
        "reason": reason,
    }
    decision["decision_digest"] = _digest(decision)
    return decision


def validate_mode_decision(
    contract: ApplicationServiceContract,
    decision: Mapping[str, Any],
) -> list[str]:
    errors = []
    if decision.get("schema") != MODE_DECISION_SCHEMA:
        errors.append("decision schema mismatch")
    if decision.get("request_id") != contract.request_id:
        errors.append("request identity mismatch")
    if decision.get("requested_contract_digest") != contract.digest:
        errors.append("requested contract digest mismatch")
    selected_mode = decision.get("selected_mode")
    legal_ids = {mode.mode_id for mode in contract.legal_modes}
    if selected_mode is not None and selected_mode not in legal_ids:
        errors.append("selected mode is not legal")
    if decision.get("disposition") not in contract.allowed_dispositions:
        errors.append("disposition is not legal")
    unsigned = dict(decision)
    observed_digest = unsigned.pop("decision_digest", None)
    if observed_digest != _digest(unsigned):
        errors.append("decision digest mismatch")
    return errors


def parse_vllm_metrics(text: str) -> dict[str, float]:
    aliases = {
        "vllm:num_requests_running": "num_requests_running",
        "vllm:num_requests_waiting": "num_requests_waiting",
        "vllm:kv_cache_usage_perc": "kv_cache_usage_perc",
    }
    values: dict[str, list[float]] = {target: [] for target in aliases.values()}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        metric = line.split(None, 1)[0].split("{", 1)[0]
        target = aliases.get(metric)
        if target is None:
            continue
        try:
            values[target].append(float(line.split()[-1]))
        except (IndexError, ValueError):
            continue
    if any(not rows for rows in values.values()):
        raise RuntimeError("vLLM metrics lack running/waiting/KV fields")
    return {
        "num_requests_running": sum(values["num_requests_running"]),
        "num_requests_waiting": sum(values["num_requests_waiting"]),
        "kv_cache_usage_perc": max(values["kv_cache_usage_perc"]),
    }


def fetch_vllm_runtime_snapshot(
    base_url: str,
    *,
    reserved_by_mode: Mapping[str, int],
    timeout_seconds: float = 5.0,
) -> ServiceRuntimeSnapshot:
    metrics_url = base_url.rstrip("/")
    if metrics_url.endswith("/v1"):
        metrics_url = metrics_url[:-3]
    request = urllib.request.Request(metrics_url + "/metrics", method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            if response.status != 200:
                raise RuntimeError(f"metrics endpoint returned HTTP {response.status}")
            metrics = parse_vllm_metrics(response.read().decode("utf-8"))
    except (OSError, UnicodeDecodeError, urllib.error.URLError) as exc:
        raise RuntimeError(f"live runtime snapshot unavailable: {exc}") from exc
    return ServiceRuntimeSnapshot(
        **metrics,
        reserved_by_mode=dict(reserved_by_mode),
        observed_at_ns=time.time_ns(),
    )
