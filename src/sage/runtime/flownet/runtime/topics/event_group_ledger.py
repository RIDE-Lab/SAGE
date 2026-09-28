from __future__ import annotations

import hashlib
from collections.abc import Callable
from typing import Any

import cloudpickle

from sage.runtime.flownet.runtime.topics.coordinator_registry import (
    CoordinatorTopicState,
    EventGroupLedger,
)
from sage.runtime.flownet.runtime.topics.normalization import (
    _normalize_non_empty,
    _normalize_non_negative_int,
)


class EventGroupLedgerManager:
    """Coordinator-side convergence ledger manager keyed by event_group_id."""

    def __init__(
        self,
        *,
        time_fn: Callable[[], float],
        on_request_done: Callable[[dict[str, Any]], None] | None = None,
        on_ledger_mutation: Callable[..., int] | None = None,
    ) -> None:
        self._time_fn = time_fn
        self._on_request_done = on_request_done
        self._on_ledger_mutation = on_ledger_mutation

    def normalize_event_group_id(self, event_group_id: str) -> str:
        return _normalize_non_empty(event_group_id, field_name="event_group_id")

    def ledger(
        self,
        *,
        state: CoordinatorTopicState,
        event_group_id: str,
    ) -> EventGroupLedger:
        normalized_event_group_id = self.normalize_event_group_id(event_group_id)
        ledger = state.event_group_ledgers.get(normalized_event_group_id)
        if ledger is None:
            ledger = EventGroupLedger(
                event_group_id=normalized_event_group_id,
                admission_epoch=state.epoch,
            )
            state.event_group_ledgers[normalized_event_group_id] = ledger
        return ledger

    def snapshot(
        self,
        *,
        state: CoordinatorTopicState,
        event_group_id: str,
    ) -> dict[str, Any] | None:
        normalized_event_group_id = self.normalize_event_group_id(event_group_id)
        ledger = state.event_group_ledgers.get(normalized_event_group_id)
        if ledger is None:
            return None
        version_info = _version_snapshot(ledger.observed_flow_program_revs)
        snapshot = {
            "event_group_id": ledger.event_group_id,
            "admission_epoch": ledger.admission_epoch,
            "first_admitted_at": ledger.first_admitted_at,
            "completed_at": ledger.completed_at,
            "event_chain_pending": ledger.event_chain_pending,
            "producer_done": ledger.producer_done,
            "emitted_event_count": ledger.emitted_event_count,
            "final_seq": ledger.final_seq,
            "expected_total_events_hint": ledger.expected_total_events_hint,
            "request_done_emitted": ledger.request_done_emitted,
            "outcome_status": ledger.outcome_status,
            "outcome_error_type": ledger.outcome_error_type,
            "outcome_error_message": ledger.outcome_error_message,
            "outcome_error_stage": ledger.outcome_error_stage,
            "outcome_metadata": dict(ledger.outcome_metadata),
            "observed_versions": version_info["observed_versions"],
            "version_count": version_info["version_count"],
            "mixed_version_violation": version_info["mixed_version_violation"],
        }
        if ledger.commit_index > 0:
            snapshot.update(
                {
                    "payload_digest": ledger.payload_digest,
                    "lineage_digest": ledger.lineage_digest,
                    "commit_index": ledger.commit_index,
                }
            )
        return snapshot

    def apply_outcome(
        self,
        *,
        state: CoordinatorTopicState,
        event_group_id: str,
        outcome_status: str,
        error_type: str | None = None,
        error_message: str | None = None,
        error_stage: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        ledger = self.ledger(state=state, event_group_id=event_group_id)
        normalized_metadata = {str(key): value for key, value in dict(metadata or {}).items()}
        observed_version = _extract_observed_flow_program_rev(normalized_metadata)
        if observed_version is not None:
            ledger.observed_flow_program_revs.add(observed_version)
        normalized_outcome_status = _normalize_outcome_status(outcome_status)
        if (
            _OUTCOME_STATUS_PRIORITY[normalized_outcome_status]
            >= _OUTCOME_STATUS_PRIORITY[ledger.outcome_status]
        ):
            ledger.outcome_status = normalized_outcome_status
            if normalized_outcome_status in _OUTCOME_FAILURE_STATUSES:
                ledger.outcome_error_type = _normalize_optional_non_empty(error_type)
                ledger.outcome_error_message = _normalize_optional_non_empty(error_message)
                ledger.outcome_error_stage = _normalize_optional_non_empty(error_stage)
                ledger.outcome_metadata = normalized_metadata
            else:
                ledger.outcome_error_type = None
                ledger.outcome_error_message = None
                ledger.outcome_error_stage = None
                ledger.outcome_metadata = {}
        ledger.updated_at = self._time_fn()
        state.updated_at = ledger.updated_at
        self._commit_ledger(state=state, ledger=ledger)
        version_info = _version_snapshot(ledger.observed_flow_program_revs)
        return {
            "topic_uri": state.topic_uri,
            "epoch": state.epoch,
            "event_group_id": ledger.event_group_id,
            "outcome_status": ledger.outcome_status,
            "outcome_error_type": ledger.outcome_error_type,
            "outcome_error_message": ledger.outcome_error_message,
            "outcome_error_stage": ledger.outcome_error_stage,
            "outcome_metadata": dict(ledger.outcome_metadata),
            "observed_versions": version_info["observed_versions"],
            "version_count": version_info["version_count"],
            "mixed_version_violation": version_info["mixed_version_violation"],
        }

    def mixed_version_summary(
        self,
        *,
        state: CoordinatorTopicState,
        include_requests: bool = False,
    ) -> dict[str, Any]:
        mixed_requests: list[dict[str, Any]] = []
        for event_group_id, ledger in sorted(state.event_group_ledgers.items()):
            version_info = _version_snapshot(ledger.observed_flow_program_revs)
            if not version_info["mixed_version_violation"]:
                continue
            mixed_requests.append(
                {
                    "event_group_id": event_group_id,
                    "observed_versions": version_info["observed_versions"],
                    "version_count": version_info["version_count"],
                }
            )
        summary: dict[str, Any] = {
            "topic_uri": state.topic_uri,
            "epoch": state.epoch,
            "v_mix_count": len(mixed_requests),
            "v_mix_passed": len(mixed_requests) == 0,
        }
        if include_requests:
            summary["v_mix_requests"] = mixed_requests
        return summary

    def record_local_event_publish(
        self,
        *,
        state: CoordinatorTopicState,
        event_group_id: str,
        payload: Any,
        tags: dict[str, str] | None,
        seq: int | None,
    ) -> dict[str, Any]:
        ledger = self.ledger(state=state, event_group_id=event_group_id)
        normalized_seq = self._resolve_event_seq(tags=tags, seq=seq, ledger=ledger)
        if ledger.admission_epoch is None:
            ledger.admission_epoch = state.epoch
        ledger.emitted_event_count += 1
        if callable(self._on_ledger_mutation):
            payload_digest = hashlib.sha256(cloudpickle.dumps(payload)).hexdigest()
            previous_lineage = ledger.lineage_digest or ("0" * 64)
            lineage_record = f"{previous_lineage}:{normalized_seq}:{payload_digest}".encode()
            ledger.payload_digest = payload_digest
            ledger.lineage_digest = hashlib.sha256(lineage_record).hexdigest()
        if normalized_seq is not None:
            if ledger.final_seq is None or normalized_seq > ledger.final_seq:
                ledger.final_seq = normalized_seq
        ledger.updated_at = self._time_fn()
        if ledger.first_admitted_at is None:
            ledger.first_admitted_at = ledger.updated_at
        state.updated_at = ledger.updated_at
        self._commit_ledger(state=state, ledger=ledger)
        return {
            "topic_uri": state.topic_uri,
            "epoch": state.epoch,
            "event_group_id": ledger.event_group_id,
            "payload": payload,
            "tags": dict(tags or {}),
            "seq": normalized_seq,
            "emitted_event_count": ledger.emitted_event_count,
            "admission_epoch": ledger.admission_epoch,
            "first_admitted_at": ledger.first_admitted_at,
            "published_at": ledger.updated_at,
        }

    def apply_event_chain_delta(
        self,
        *,
        state: CoordinatorTopicState,
        event_group_id: str,
        delta: int,
    ) -> dict[str, Any]:
        ledger = self.ledger(state=state, event_group_id=event_group_id)
        next_pending = ledger.event_chain_pending + int(delta)
        if next_pending < 0:
            raise ValueError("event_chain_pending_underflow")
        ledger.event_chain_pending = next_pending
        ledger.updated_at = self._time_fn()
        state.updated_at = ledger.updated_at
        request_done = self._maybe_request_done(state=state, ledger=ledger)
        if request_done is None:
            self._commit_ledger(state=state, ledger=ledger)
        return {
            "topic_uri": state.topic_uri,
            "epoch": state.epoch,
            "event_group_id": ledger.event_group_id,
            "event_chain_pending": ledger.event_chain_pending,
            "producer_done": ledger.producer_done,
            "request_done": request_done,
        }

    def apply_producer_done(
        self,
        *,
        state: CoordinatorTopicState,
        event_group_id: str,
        final_seq: int | None,
        expected_total_events: int | None,
    ) -> dict[str, Any]:
        ledger = self.ledger(state=state, event_group_id=event_group_id)
        ledger.producer_done = True
        if final_seq is not None:
            normalized_final_seq = _normalize_non_negative_int(final_seq, field_name="final_seq")
            if ledger.final_seq is None or normalized_final_seq > ledger.final_seq:
                ledger.final_seq = normalized_final_seq
        if expected_total_events is not None:
            ledger.expected_total_events_hint = _normalize_non_negative_int(
                expected_total_events,
                field_name="expected_total_events",
            )
        ledger.updated_at = self._time_fn()
        state.updated_at = ledger.updated_at
        request_done = self._maybe_request_done(state=state, ledger=ledger)
        if request_done is None:
            self._commit_ledger(state=state, ledger=ledger)
        return {
            "topic_uri": state.topic_uri,
            "epoch": state.epoch,
            "event_group_id": ledger.event_group_id,
            "event_chain_pending": ledger.event_chain_pending,
            "producer_done": ledger.producer_done,
            "request_done": request_done,
        }

    def _resolve_event_seq(
        self,
        *,
        tags: dict[str, str] | None,
        seq: int | None,
        ledger: EventGroupLedger,
    ) -> int | None:
        if seq is None and isinstance(tags, dict):
            raw_seq = tags.get("__seq__")
            if raw_seq is not None:
                seq = int(raw_seq)
        if seq is not None:
            return _normalize_non_negative_int(seq, field_name="seq")
        return ledger.emitted_event_count + 1

    def _maybe_request_done(
        self,
        *,
        state: CoordinatorTopicState,
        ledger: EventGroupLedger,
    ) -> dict[str, Any] | None:
        if ledger.request_done_emitted:
            return None
        if not ledger.producer_done:
            return None
        if ledger.event_chain_pending != 0:
            return None
        ledger.request_done_emitted = True
        if ledger.admission_epoch is None:
            ledger.admission_epoch = state.epoch
        if ledger.first_admitted_at is None:
            ledger.first_admitted_at = ledger.updated_at
        ledger.completed_at = ledger.updated_at
        expected_total_events = ledger.expected_total_events_hint
        if expected_total_events is None:
            if ledger.final_seq is not None:
                expected_total_events = ledger.final_seq
            else:
                expected_total_events = ledger.emitted_event_count
        version_info = _version_snapshot(ledger.observed_flow_program_revs)
        request_done = {
            "topic_uri": state.topic_uri,
            "epoch": state.epoch,
            "event_group_id": ledger.event_group_id,
            "admission_epoch": ledger.admission_epoch,
            "first_admitted_at": ledger.first_admitted_at,
            "completed_at": ledger.completed_at,
            "final_seq": ledger.final_seq,
            "expected_total_events": expected_total_events,
            "status": _derive_request_done_status(ledger.outcome_status),
            "outcome_status": ledger.outcome_status,
            "outcome_error_type": ledger.outcome_error_type,
            "outcome_error_message": ledger.outcome_error_message,
            "outcome_error_stage": ledger.outcome_error_stage,
            "outcome_metadata": dict(ledger.outcome_metadata),
            "observed_versions": version_info["observed_versions"],
            "version_count": version_info["version_count"],
            "mixed_version_violation": version_info["mixed_version_violation"],
        }
        self._commit_ledger(state=state, ledger=ledger)
        if ledger.commit_index > 0:
            request_done.update(
                {
                    "payload_digest": ledger.payload_digest,
                    "lineage_digest": ledger.lineage_digest,
                    "commit_index": ledger.commit_index,
                }
            )
        if callable(self._on_request_done):
            self._on_request_done(dict(request_done))
        return request_done

    def _commit_ledger(
        self,
        *,
        state: CoordinatorTopicState,
        ledger: EventGroupLedger,
    ) -> None:
        if callable(self._on_ledger_mutation):
            self._on_ledger_mutation(state=state, ledger=ledger)


_OUTCOME_FAILURE_STATUSES = frozenset({"failed", "aborted", "dropped"})
_OUTCOME_STATUS_PRIORITY = {
    "pending": 0,
    "succeeded": 1,
    "dropped": 2,
    "failed": 3,
    "aborted": 4,
}


def _normalize_outcome_status(outcome_status: str) -> str:
    normalized = _normalize_non_empty(outcome_status, field_name="outcome_status").strip().lower()
    if normalized not in _OUTCOME_STATUS_PRIORITY:
        raise ValueError(f"unsupported_outcome_status:{normalized}")
    return normalized


def _normalize_optional_non_empty(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    if not normalized:
        return None
    return normalized


def _derive_request_done_status(outcome_status: str) -> str:
    normalized = _normalize_outcome_status(outcome_status)
    if normalized in _OUTCOME_FAILURE_STATUSES:
        return normalized
    return "completed"


def _extract_observed_flow_program_rev(metadata: dict[str, Any]) -> str | None:
    for field_name in ("flow_program_rev", "flow_program_version", "version"):
        value = _normalize_optional_non_empty(metadata.get(field_name))
        if value is not None:
            return value
    return None


def _version_snapshot(observed_revs: set[str]) -> dict[str, Any]:
    observed_versions = sorted(observed_revs)
    version_count = len(observed_versions)
    return {
        "observed_versions": observed_versions,
        "version_count": version_count,
        "mixed_version_violation": version_count > 1,
    }


__all__ = ["EventGroupLedgerManager"]
