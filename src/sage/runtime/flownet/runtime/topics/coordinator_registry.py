from __future__ import annotations

import hashlib
import json
import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock
from typing import Any

from sage.runtime.flownet.runtime.topics.normalization import (
    _normalize_non_empty,
    _normalize_non_negative_int,
    _normalize_topic_uri,
)


@dataclass
class EventGroupLedger:
    event_group_id: str
    admission_epoch: int | None = None
    first_admitted_at: float | None = None
    completed_at: float | None = None
    event_chain_pending: int = 0
    producer_done: bool = False
    emitted_event_count: int = 0
    final_seq: int | None = None
    expected_total_events_hint: int | None = None
    request_done_emitted: bool = False
    outcome_status: str = "pending"
    outcome_error_type: str | None = None
    outcome_error_message: str | None = None
    outcome_error_stage: str | None = None
    outcome_metadata: dict[str, object] = field(default_factory=dict)
    observed_flow_program_revs: set[str] = field(default_factory=set)
    payload_digest: str | None = None
    lineage_digest: str | None = None
    commit_index: int = 0
    updated_at: float = field(default_factory=time.time)


@dataclass
class CoordinatorTopicState:
    topic_uri: str
    epoch: int
    consuming_flow_process_uris: set[str] = field(default_factory=set)
    producing_flow_process_uris: set[str] = field(default_factory=set)
    subscriber_ids: set[str] = field(default_factory=set)
    subscriber_addresses: dict[str, str] = field(default_factory=dict)
    event_group_ledgers: dict[str, EventGroupLedger] = field(default_factory=dict)
    updated_at: float = field(default_factory=time.time)


class TopicCoordinatorRegistry:
    """
    Local coordinator-state holder.

    State is lazy and keyed by (topic_uri, epoch).
    """

    def __init__(
        self,
        *,
        time_fn: Callable[[], float] | None = None,
        causal_cut_path: str | os.PathLike[str] | None = None,
    ):
        self._states: dict[tuple[str, int], CoordinatorTopicState] = {}
        self._lock = Lock()
        self._persistence_lock = Lock()
        self._time_fn = time_fn or time.time
        self._causal_cut_path = (
            Path(causal_cut_path).expanduser().resolve() if causal_cut_path is not None else None
        )
        self._commit_index = 0
        self._durable_commit_index = 0
        if self._causal_cut_path is not None and self._causal_cut_path.exists():
            self._restore_causal_cut()

    @property
    def causal_cut_enabled(self) -> bool:
        return self._causal_cut_path is not None

    def commit_event_group(
        self,
        *,
        state: CoordinatorTopicState,
        ledger: EventGroupLedger,
    ) -> int:
        """Durably publish a monotonic coordinator causal cut when enabled."""
        if self._causal_cut_path is None:
            return int(ledger.commit_index)
        with self._persistence_lock:
            self._commit_index += 1
            ledger.commit_index = self._commit_index
            payload = self._causal_cut_payload()
            self._write_causal_cut(payload)
            self._durable_commit_index = self._commit_index
            return int(ledger.commit_index)

    def get_or_create(self, topic_uri: str, epoch: int) -> CoordinatorTopicState:
        normalized_topic_uri = _normalize_topic_uri(topic_uri)
        normalized_epoch = _normalize_non_negative_int(epoch, field_name="epoch")
        key = (normalized_topic_uri, normalized_epoch)
        now = self._time_fn()
        with self._lock:
            state = self._states.get(key)
            if state is None:
                state = CoordinatorTopicState(
                    topic_uri=normalized_topic_uri,
                    epoch=normalized_epoch,
                    updated_at=now,
                )
                self._states[key] = state
            else:
                state.updated_at = now
            return state

    def get(self, topic_uri: str, epoch: int) -> CoordinatorTopicState | None:
        normalized_topic_uri = _normalize_topic_uri(topic_uri)
        normalized_epoch = _normalize_non_negative_int(epoch, field_name="epoch")
        with self._lock:
            return self._states.get((normalized_topic_uri, normalized_epoch))

    def discard_flow_process_uri(
        self,
        *,
        topic_uri: str,
        flow_process_uri: str,
    ) -> bool:
        normalized_topic_uri = _normalize_topic_uri(topic_uri)
        normalized_flow_process_uri = _normalize_non_empty(
            flow_process_uri,
            field_name="flow_process_uri",
        )
        now = self._time_fn()
        removed = False
        with self._lock:
            for state in self._states.values():
                if state.topic_uri != normalized_topic_uri:
                    continue
                prev_consuming_count = len(state.consuming_flow_process_uris)
                prev_producing_count = len(state.producing_flow_process_uris)
                state.consuming_flow_process_uris.discard(normalized_flow_process_uri)
                state.producing_flow_process_uris.discard(normalized_flow_process_uri)
                if (
                    len(state.consuming_flow_process_uris) != prev_consuming_count
                    or len(state.producing_flow_process_uris) != prev_producing_count
                ):
                    state.updated_at = now
                    removed = True
        return removed

    def gc_idle(self, *, max_idle_seconds: float) -> int:
        threshold = max(0.0, float(max_idle_seconds))
        now = self._time_fn()
        removed = 0
        with self._lock:
            for key, state in list(self._states.items()):
                self._prune_finished_event_group_ledgers(
                    state=state,
                    now=now,
                    threshold=threshold,
                )
                if self._has_unfinished_event_group(state):
                    continue
                if state.event_group_ledgers:
                    # keep recently-finished ledgers for observability until TTL.
                    continue
                if (now - state.updated_at) <= threshold:
                    continue
                if state.consuming_flow_process_uris:
                    continue
                if state.producing_flow_process_uris:
                    continue
                if state.subscriber_ids:
                    continue
                if state.subscriber_addresses:
                    continue
                self._states.pop(key, None)
                removed += 1
        return removed

    def observability_snapshot(self) -> dict[str, object]:
        now = self._time_fn()
        with self._lock:
            states = list(self._states.values())

        queue_rows: list[dict[str, object]] = []
        tracked_requests = 0
        completed_requests = 0
        active_requests = 0
        pending_event_chains = 0
        active_delay_samples_ms: list[float] = []

        for state in states:
            state_tracked = 0
            state_completed = 0
            state_active = 0
            state_pending_event_chains = 0
            state_delay_samples_ms: list[float] = []

            for ledger in state.event_group_ledgers.values():
                state_tracked += 1
                tracked_requests += 1
                state_pending_event_chains += max(0, int(ledger.event_chain_pending))
                pending_event_chains += max(0, int(ledger.event_chain_pending))

                if self._is_finished_event_group_ledger(ledger):
                    state_completed += 1
                    completed_requests += 1
                    continue

                state_active += 1
                active_requests += 1
                admitted_at = ledger.first_admitted_at or ledger.updated_at
                delay_ms = max(0.0, float(now - admitted_at) * 1000.0)
                state_delay_samples_ms.append(delay_ms)
                active_delay_samples_ms.append(delay_ms)

            if state_tracked <= 0:
                continue

            queue_rows.append(
                {
                    "queue_id": f"{state.topic_uri}@{state.epoch}",
                    "topic_uri": state.topic_uri,
                    "epoch": int(state.epoch),
                    "tracked_requests": state_tracked,
                    "completed_requests": state_completed,
                    "active_requests": state_active,
                    "pending_event_chains": state_pending_event_chains,
                    "queue_delay_ms": (
                        round(max(state_delay_samples_ms), 3) if state_delay_samples_ms else 0.0
                    ),
                }
            )

        queue_rows.sort(key=lambda item: (str(item["topic_uri"]), int(item["epoch"])))
        avg_delay_ms = (
            sum(active_delay_samples_ms) / float(len(active_delay_samples_ms))
            if active_delay_samples_ms
            else 0.0
        )
        max_delay_ms = max(active_delay_samples_ms) if active_delay_samples_ms else 0.0

        return {
            "generated_at_ms": int(now * 1000.0),
            "tracked_requests": tracked_requests,
            "completed_requests": completed_requests,
            "active_requests": active_requests,
            "pending_event_chains": pending_event_chains,
            "queue_delay_ms": {
                "avg": round(avg_delay_ms, 3),
                "max": round(max_delay_ms, 3),
            },
            "queues": queue_rows,
            "causal_cut_enabled": self.causal_cut_enabled,
            "causal_cut_commit_index": self._commit_index,
            "causal_cut_durable_commit_index": self._durable_commit_index,
        }

    def _causal_cut_payload(self) -> dict[str, Any]:
        with self._lock:
            states = sorted(self._states.values(), key=lambda item: (item.topic_uri, item.epoch))
            state_rows = []
            for state in states:
                ledgers = []
                for ledger in sorted(
                    state.event_group_ledgers.values(), key=lambda item: item.event_group_id
                ):
                    ledgers.append(
                        {
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
                            "outcome_metadata": ledger.outcome_metadata,
                            "observed_flow_program_revs": sorted(ledger.observed_flow_program_revs),
                            "payload_digest": ledger.payload_digest,
                            "lineage_digest": ledger.lineage_digest,
                            "commit_index": ledger.commit_index,
                            "updated_at": ledger.updated_at,
                        }
                    )
                state_rows.append(
                    {
                        "topic_uri": state.topic_uri,
                        "epoch": state.epoch,
                        "event_group_ledgers": ledgers,
                        "updated_at": state.updated_at,
                    }
                )
        return {
            "schema_version": 1,
            "commit_index": self._commit_index,
            "states": state_rows,
        }

    def _write_causal_cut(self, payload: dict[str, Any]) -> None:
        assert self._causal_cut_path is not None
        encoded_payload = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        envelope = {
            "schema_version": 1,
            "payload_sha256": hashlib.sha256(encoded_payload).hexdigest(),
            "payload": payload,
        }
        encoded_envelope = (
            json.dumps(envelope, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
        ).encode("utf-8")
        target = self._causal_cut_path
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.tmp-{os.getpid()}")
        with temporary.open("wb") as stream:
            stream.write(encoded_envelope)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
        directory_fd = os.open(target.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)

    def _restore_causal_cut(self) -> None:
        assert self._causal_cut_path is not None
        try:
            envelope = json.loads(self._causal_cut_path.read_text(encoding="utf-8"))
            if envelope.get("schema_version") != 1:
                raise ValueError("unsupported_schema")
            payload = envelope["payload"]
            encoded_payload = json.dumps(
                payload,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
            actual_digest = hashlib.sha256(encoded_payload).hexdigest()
            if actual_digest != envelope.get("payload_sha256"):
                raise ValueError("digest_mismatch")
            if payload.get("schema_version") != 1:
                raise ValueError("unsupported_payload_schema")
            restored_states = self._decode_states(payload.get("states"))
            commit_index = _normalize_non_negative_int(
                payload.get("commit_index"), field_name="commit_index"
            )
        except Exception as exc:
            raise RuntimeError(f"causal_cut_restore_failed:{exc}") from exc
        self._states = restored_states
        self._commit_index = commit_index
        self._durable_commit_index = commit_index

    @staticmethod
    def _decode_states(raw_states: Any) -> dict[tuple[str, int], CoordinatorTopicState]:
        if not isinstance(raw_states, list):
            raise TypeError("states must be a list")
        restored: dict[tuple[str, int], CoordinatorTopicState] = {}
        for raw_state in raw_states:
            if not isinstance(raw_state, dict):
                raise TypeError("state must be an object")
            topic_uri = _normalize_topic_uri(raw_state.get("topic_uri"))
            epoch = _normalize_non_negative_int(raw_state.get("epoch"), field_name="epoch")
            state = CoordinatorTopicState(
                topic_uri=topic_uri,
                epoch=epoch,
                updated_at=float(raw_state.get("updated_at") or time.time()),
            )
            raw_ledgers = raw_state.get("event_group_ledgers")
            if not isinstance(raw_ledgers, list):
                raise TypeError("event_group_ledgers must be a list")
            for raw_ledger in raw_ledgers:
                if not isinstance(raw_ledger, dict):
                    raise TypeError("ledger must be an object")
                event_group_id = _normalize_non_empty(
                    raw_ledger.get("event_group_id"), field_name="event_group_id"
                )
                ledger = EventGroupLedger(
                    event_group_id=event_group_id,
                    admission_epoch=raw_ledger.get("admission_epoch"),
                    first_admitted_at=raw_ledger.get("first_admitted_at"),
                    completed_at=raw_ledger.get("completed_at"),
                    event_chain_pending=_normalize_non_negative_int(
                        raw_ledger.get("event_chain_pending"),
                        field_name="event_chain_pending",
                    ),
                    producer_done=bool(raw_ledger.get("producer_done")),
                    emitted_event_count=_normalize_non_negative_int(
                        raw_ledger.get("emitted_event_count"),
                        field_name="emitted_event_count",
                    ),
                    final_seq=raw_ledger.get("final_seq"),
                    expected_total_events_hint=raw_ledger.get("expected_total_events_hint"),
                    request_done_emitted=bool(raw_ledger.get("request_done_emitted")),
                    outcome_status=str(raw_ledger.get("outcome_status") or "pending"),
                    outcome_error_type=raw_ledger.get("outcome_error_type"),
                    outcome_error_message=raw_ledger.get("outcome_error_message"),
                    outcome_error_stage=raw_ledger.get("outcome_error_stage"),
                    outcome_metadata=dict(raw_ledger.get("outcome_metadata") or {}),
                    observed_flow_program_revs=set(
                        raw_ledger.get("observed_flow_program_revs") or []
                    ),
                    payload_digest=raw_ledger.get("payload_digest"),
                    lineage_digest=raw_ledger.get("lineage_digest"),
                    commit_index=_normalize_non_negative_int(
                        raw_ledger.get("commit_index"), field_name="commit_index"
                    ),
                    updated_at=float(raw_ledger.get("updated_at") or time.time()),
                )
                if ledger.admission_epoch != epoch:
                    raise ValueError("admission_epoch_mismatch")
                if ledger.commit_index < 1:
                    raise ValueError("invalid_commit_index")
                state.event_group_ledgers[event_group_id] = ledger
            restored[(topic_uri, epoch)] = state
        return restored

    @staticmethod
    def _has_unfinished_event_group(state: CoordinatorTopicState) -> bool:
        for ledger in state.event_group_ledgers.values():
            if not TopicCoordinatorRegistry._is_finished_event_group_ledger(ledger):
                return True
        return False

    @staticmethod
    def _is_finished_event_group_ledger(ledger: EventGroupLedger) -> bool:
        if not ledger.request_done_emitted:
            return False
        if not ledger.producer_done:
            return False
        if int(ledger.event_chain_pending) != 0:
            return False
        return True

    @staticmethod
    def _prune_finished_event_group_ledgers(
        *,
        state: CoordinatorTopicState,
        now: float,
        threshold: float,
    ) -> None:
        for event_group_id, ledger in list(state.event_group_ledgers.items()):
            if not TopicCoordinatorRegistry._is_finished_event_group_ledger(ledger):
                continue
            if (now - ledger.updated_at) <= threshold:
                continue
            state.event_group_ledgers.pop(event_group_id, None)


__all__ = [
    "EventGroupLedger",
    "CoordinatorTopicState",
    "TopicCoordinatorRegistry",
]
