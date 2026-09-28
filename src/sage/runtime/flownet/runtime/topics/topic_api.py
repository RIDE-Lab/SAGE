from __future__ import annotations

import time
from collections.abc import Callable
from os import PathLike
from threading import Lock
from typing import Any

from sage.runtime.flownet.runtime.flowengine import FlowProgramCache, FlowProgramRef
from sage.runtime.flownet.runtime.topics.control_dispatch import TopicControlDispatch
from sage.runtime.flownet.runtime.topics.coordinator_registry import (
    CoordinatorTopicState,
    EventGroupLedger,
    TopicCoordinatorRegistry,
)
from sage.runtime.flownet.runtime.topics.event_dispatch import TopicEventDispatch
from sage.runtime.flownet.runtime.topics.event_group_ledger import EventGroupLedgerManager
from sage.runtime.flownet.runtime.topics.flow_process_catalog import (
    FlowProcessCatalog,
    FlowProcessRecord,
)
from sage.runtime.flownet.runtime.topics.normalization import (
    _normalize_non_empty,
    _normalize_non_negative_int,
)
from sage.runtime.flownet.runtime.topics.routing_directory import (
    FlowProgramRouteView,
    FlowProgramRoutingDirectory,
    TopicRouteView,
    TopicRoutingDirectory,
    TopicSwitchWindowView,
)
from sage.runtime.flownet.runtime.topics.subscriber_progress import SubscriberProgressManager
from sage.runtime.flownet.runtime.topics.subscriber_registry import TopicSubscriberRegistry

IngressEventHandler = Callable[..., list[dict[str, Any]]]


class TopicAPI:
    """
    v1 topic runtime facade (flat model).

    Canonical identity for convergence in v1 is `event_group_id`.
    TopicAPI itself does not execute flow processes. Runtime wiring injects an
    ingress handler that may run flowengine and emit data/control intents.
    """

    def __init__(
        self,
        *,
        local_address: str | Callable[[], str],
        flow_process_catalog: FlowProcessCatalog | None = None,
        routing_directory: TopicRoutingDirectory | None = None,
        flow_program_routing_directory: FlowProgramRoutingDirectory | None = None,
        coordinator_registry: TopicCoordinatorRegistry | None = None,
        coordinator_causal_cut_path: str | PathLike[str] | None = None,
        pull_flow_program: Callable[[FlowProgramRef], Any | None] | None = None,
        time_fn: Callable[[], float] | None = None,
        on_topic_event: Callable[[dict[str, Any]], None] | None = None,
        on_request_done: Callable[[dict[str, Any]], None] | None = None,
        on_request_done_notify_intents: Callable[[list[dict[str, Any]]], None] | None = None,
        on_subscriber_done: Callable[[dict[str, Any]], None] | None = None,
        on_ingress_event: IngressEventHandler | None = None,
    ) -> None:
        if callable(local_address):
            self._local_address_fn = local_address
        else:
            normalized_local_address = _normalize_non_empty(
                local_address,
                field_name="local_address",
            )
            self._local_address_fn = lambda: normalized_local_address

        self._time_fn = time_fn or time.time

        self.flow_process_catalog = flow_process_catalog or FlowProcessCatalog()
        self.routing_directory = routing_directory or TopicRoutingDirectory(
            time_fn=self._time_fn,
        )
        self.flow_program_routing_directory = (
            flow_program_routing_directory or FlowProgramRoutingDirectory()
        )
        if coordinator_registry is not None and coordinator_causal_cut_path is not None:
            raise ValueError(
                "coordinator_registry and coordinator_causal_cut_path cannot both be provided."
            )
        self.coordinator_registry = coordinator_registry or TopicCoordinatorRegistry(
            time_fn=self._time_fn,
            causal_cut_path=coordinator_causal_cut_path,
        )
        self.subscriber_registry = TopicSubscriberRegistry(time_fn=self._time_fn)
        self._on_topic_event = on_topic_event
        self._on_request_done = on_request_done
        self._on_request_done_notify_intents = on_request_done_notify_intents
        self._on_ingress_event = on_ingress_event
        self._topic_event_listener_lock = Lock()
        self._topic_event_listeners: dict[str, Callable[[dict[str, Any]], None]] = {}

        self._flow_program_cache = FlowProgramCache(
            pull_program=pull_flow_program,
        )

        self._ledger_manager = EventGroupLedgerManager(
            time_fn=self._time_fn,
            on_request_done=self._handle_request_done,
            on_ledger_mutation=(
                self.coordinator_registry.commit_event_group
                if self.coordinator_registry.causal_cut_enabled
                else None
            ),
        )
        self._subscriber_progress = SubscriberProgressManager(
            time_fn=self._time_fn,
            on_subscriber_done=on_subscriber_done,
        )
        self._event_dispatch = TopicEventDispatch(
            routing_directory=self.routing_directory,
            local_address=self._local_address,
        )
        self._control_dispatch = TopicControlDispatch(
            routing_directory=self.routing_directory,
            local_address=self._local_address,
        )

    def register_flow_program(
        self,
        *,
        flow_program_uri: str,
        flow_program_rev: str,
        flow_program: Any,
    ) -> None:
        flow_program_ref = self._resolve_flow_program_ref(
            flow_program_uri=flow_program_uri,
            flow_program_rev=flow_program_rev,
        )
        self._validate_flow_program_runtime(flow_program)
        self._flow_program_cache.register(
            flow_program_ref=flow_program_ref,
            flow_program=flow_program,
        )
        self.upsert_flow_program_owner(
            flow_program_uri=flow_program_ref.program_uri,
            flow_program_rev=flow_program_ref.program_rev,
            owner_address=self._local_address(),
        )

    def unregister_flow_program(
        self,
        *,
        flow_program_uri: str,
        flow_program_rev: str,
    ) -> bool:
        flow_program_ref = self._resolve_flow_program_ref(
            flow_program_uri=flow_program_uri,
            flow_program_rev=flow_program_rev,
        )
        return self._flow_program_cache.unregister(
            flow_program_ref=flow_program_ref,
        )

    def upsert_flow_program_owner(
        self,
        *,
        flow_program_uri: str,
        flow_program_rev: str,
        owner_address: str,
    ) -> FlowProgramRouteView:
        return self.flow_program_routing_directory.upsert_route(
            flow_program_uri=flow_program_uri,
            flow_program_rev=flow_program_rev,
            owner_address=owner_address,
        )

    def resolve_registered_flow_program(
        self,
        *,
        flow_program_uri: str,
        flow_program_rev: str,
    ) -> Any | None:
        flow_program_ref = self._resolve_flow_program_ref(
            flow_program_uri=flow_program_uri,
            flow_program_rev=flow_program_rev,
        )
        return self._resolve_flow_program(flow_program_ref)

    def register_flow_process(
        self,
        *,
        flow_process_uri: str,
        flow_program_uri: str,
        flow_program_rev: str,
        flow_program_owner_address: str | None = None,
        in_topic_uri: str,
        out_topic_uri: str,
        metadata: dict[str, Any] | None = None,
    ) -> FlowProcessRecord:
        record = self.flow_process_catalog.register(
            flow_process_uri=flow_process_uri,
            flow_program_uri=flow_program_uri,
            flow_program_rev=_normalize_non_empty(
                flow_program_rev,
                field_name="flow_program_rev",
            ),
            in_topic_uri=in_topic_uri,
            out_topic_uri=out_topic_uri,
            metadata=metadata,
        )
        if flow_program_owner_address is not None:
            self.upsert_flow_program_owner(
                flow_program_uri=record.flow_program_uri,
                flow_program_rev=record.flow_program_rev,
                owner_address=flow_program_owner_address,
            )
        in_route = self.routing_directory.require(record.in_topic_uri)
        out_route = self.routing_directory.require(record.out_topic_uri)
        local_address = self._local_address()

        if in_route.coordinator_address == local_address:
            in_state = self.coordinator_registry.get_or_create(record.in_topic_uri, in_route.epoch)
            in_state.consuming_flow_process_uris.add(record.flow_process_uri)
            in_state.updated_at = self._time_fn()

        if out_route.coordinator_address == local_address:
            out_state = self.coordinator_registry.get_or_create(
                record.out_topic_uri, out_route.epoch
            )
            out_state.producing_flow_process_uris.add(record.flow_process_uri)
            out_state.updated_at = self._time_fn()

        return record

    def unregister_flow_process(self, *, flow_process_uri: str) -> bool:
        normalized_flow_process_uri = _normalize_non_empty(
            flow_process_uri,
            field_name="flow_process_uri",
        )
        record = self.flow_process_catalog.get(normalized_flow_process_uri)
        if record is None:
            return False

        removed = self.flow_process_catalog.delete(normalized_flow_process_uri)
        if not removed:
            return False

        self.coordinator_registry.discard_flow_process_uri(
            topic_uri=record.in_topic_uri,
            flow_process_uri=normalized_flow_process_uri,
        )
        if record.out_topic_uri != record.in_topic_uri:
            self.coordinator_registry.discard_flow_process_uri(
                topic_uri=record.out_topic_uri,
                flow_process_uri=normalized_flow_process_uri,
            )
        return True

    def add_subscriber(
        self,
        *,
        topic_uri: str,
        subscriber_id: str,
        subscriber_address: str | None = None,
        epoch: int | None = None,
    ) -> int:
        state = self._require_local_coordinator_state(topic_uri, epoch=epoch)
        normalized_subscriber_id = _normalize_non_empty(subscriber_id, field_name="subscriber_id")
        state.subscriber_ids.add(normalized_subscriber_id)
        if subscriber_address is not None:
            state.subscriber_addresses[normalized_subscriber_id] = _normalize_non_empty(
                subscriber_address,
                field_name="subscriber_address",
            )
        state.updated_at = self._time_fn()
        return len(state.subscriber_ids)

    def remove_subscriber(
        self,
        *,
        topic_uri: str,
        subscriber_id: str,
        epoch: int | None = None,
    ) -> bool:
        state = self._require_local_coordinator_state(topic_uri, epoch=epoch)
        normalized_subscriber_id = _normalize_non_empty(subscriber_id, field_name="subscriber_id")
        removed = normalized_subscriber_id in state.subscriber_ids
        state.subscriber_ids.discard(normalized_subscriber_id)
        state.subscriber_addresses.pop(normalized_subscriber_id, None)
        state.updated_at = self._time_fn()
        return removed

    def register_local_subscriber(
        self,
        *,
        topic_uri: str,
        subscriber_id: str,
        epoch: int | None = None,
    ) -> dict[str, Any]:
        route = self._require_topic_route(topic_uri, epoch=epoch)
        normalized_subscriber_id = self._subscriber_progress.normalize_subscriber_id(subscriber_id)
        state = self.subscriber_registry.get_or_create(
            topic_uri=route.topic_uri,
            epoch=route.epoch,
            subscriber_id=normalized_subscriber_id,
        )
        return {
            "topic_uri": state.topic_uri,
            "epoch": state.epoch,
            "subscriber_id": state.subscriber_id,
        }

    def request_done_notify(
        self,
        *,
        topic_uri: str,
        event_group_id: str,
        subscriber_id: str,
        status: str,
        final_seq: int | None = None,
        expected_total_events: int | None = None,
        epoch: int | None = None,
    ) -> dict[str, Any]:
        route = self._require_topic_route(topic_uri, epoch=epoch)
        if expected_total_events is None:
            raise ValueError("expected_total_events must be provided.")
        normalized_subscriber_id = self._subscriber_progress.normalize_subscriber_id(subscriber_id)
        resolved_event_group_id = self._resolve_event_group_id(event_group_id)
        state = self.subscriber_registry.get_or_create(
            topic_uri=route.topic_uri,
            epoch=route.epoch,
            subscriber_id=normalized_subscriber_id,
        )
        result = self._subscriber_progress.apply_request_done_notify(
            state=state,
            event_group_id=resolved_event_group_id,
            status=status,
            final_seq=final_seq,
            expected_total_events=expected_total_events,
        )
        return result

    def subscriber_drain(
        self,
        *,
        topic_uri: str,
        event_group_id: str,
        subscriber_id: str,
        drained_seq: int,
        epoch: int | None = None,
    ) -> dict[str, Any]:
        route = self._require_topic_route(topic_uri, epoch=epoch)
        normalized_subscriber_id = self._subscriber_progress.normalize_subscriber_id(subscriber_id)
        resolved_event_group_id = self._resolve_event_group_id(event_group_id)
        state = self.subscriber_registry.get_or_create(
            topic_uri=route.topic_uri,
            epoch=route.epoch,
            subscriber_id=normalized_subscriber_id,
        )
        result = self._subscriber_progress.apply_drain_update(
            state=state,
            event_group_id=resolved_event_group_id,
            drained_seq=drained_seq,
        )
        return result

    def subscriber_progress(
        self,
        *,
        topic_uri: str,
        event_group_id: str,
        subscriber_id: str,
        epoch: int | None = None,
    ) -> dict[str, Any] | None:
        route = self._require_topic_route(topic_uri, epoch=epoch)
        normalized_subscriber_id = self._subscriber_progress.normalize_subscriber_id(subscriber_id)
        resolved_event_group_id = self._resolve_event_group_id(event_group_id)
        state = self.subscriber_registry.get(
            topic_uri=route.topic_uri,
            epoch=route.epoch,
            subscriber_id=normalized_subscriber_id,
        )
        if state is None:
            return None
        return self._subscriber_progress.snapshot(
            state=state,
            event_group_id=resolved_event_group_id,
        )

    def set_ingress_event_handler(
        self,
        handler: IngressEventHandler | None,
    ) -> None:
        self._on_ingress_event = handler

    def has_ingress_event_handler(self) -> bool:
        return callable(self._on_ingress_event)

    def add_topic_event_listener(
        self,
        *,
        listener_id: str,
        listener: Callable[[dict[str, Any]], None],
    ) -> None:
        normalized_listener_id = _normalize_non_empty(
            listener_id,
            field_name="listener_id",
        )
        if not callable(listener):
            raise TypeError("listener must be callable.")
        with self._topic_event_listener_lock:
            if normalized_listener_id in self._topic_event_listeners:
                raise ValueError(f"topic_event_listener_already_exists:{normalized_listener_id}")
            self._topic_event_listeners[normalized_listener_id] = listener

    def remove_topic_event_listener(
        self,
        *,
        listener_id: str,
    ) -> bool:
        normalized_listener_id = _normalize_non_empty(
            listener_id,
            field_name="listener_id",
        )
        with self._topic_event_listener_lock:
            return self._topic_event_listeners.pop(normalized_listener_id, None) is not None

    def topic_event_listener_count(self) -> int:
        with self._topic_event_listener_lock:
            return len(self._topic_event_listeners)

    def publish_or_forward_event(
        self,
        *,
        topic_uri: str,
        event_group_id: str,
        payload: Any = None,
        tags: dict[str, str] | None = None,
        seq: int | None = None,
        epoch: int | None = None,
    ) -> dict[str, Any]:
        resolved_event_group_id = self._resolve_event_group_id(event_group_id)
        return self._event_dispatch.publish_or_forward_event(
            topic_uri=topic_uri,
            event_group_id=resolved_event_group_id,
            payload=payload,
            tags=tags,
            seq=seq,
            epoch=epoch,
            publish_local=self.publish_event,
        )

    def publish_event(
        self,
        *,
        topic_uri: str,
        event_group_id: str,
        payload: Any = None,
        tags: dict[str, str] | None = None,
        seq: int | None = None,
        epoch: int | None = None,
    ) -> dict[str, Any]:
        resolved_event_group_id = self._resolve_event_group_id(event_group_id)
        published = self._publish_local_event(
            topic_uri=topic_uri,
            event_group_id=resolved_event_group_id,
            payload=payload,
            tags=tags,
            seq=seq,
            epoch=epoch,
        )
        state = self._require_local_coordinator_state(topic_uri, epoch=epoch)
        switch_window = self.routing_directory.observe_admission(
            topic_uri=state.topic_uri,
            epoch=state.epoch,
            event_group_id=resolved_event_group_id,
            observed_at_s=published.get("published_at"),
        )
        flow_executions: list[dict[str, Any]] = []
        if callable(self._on_ingress_event):
            ingress_result = self._on_ingress_event(
                state=state,
                event_group_id=resolved_event_group_id,
                payload=payload,
                tags=tags,
                seq=published.get("seq"),
            )
            if ingress_result is not None:
                flow_executions = list(ingress_result)
        result = {
            "topic_uri": state.topic_uri,
            "epoch": state.epoch,
            "event_group_id": published["event_group_id"],
            "published_at": published.get("published_at"),
            "flow_process_uris": sorted(state.consuming_flow_process_uris),
            "subscriber_ids": sorted(state.subscriber_ids),
            "emitted_event_count": published["emitted_event_count"],
            "flow_executions": flow_executions,
        }
        if switch_window is not None:
            result["switch_window"] = _switch_window_view_to_payload(switch_window)
        return result

    def event_chain_done_or_forward(
        self,
        *,
        topic_uri: str,
        event_group_id: str,
        delta: int,
        epoch: int | None = None,
    ) -> dict[str, Any]:
        resolved_event_group_id = self._resolve_event_group_id(event_group_id)
        return self._control_dispatch.event_chain_done_or_forward(
            topic_uri=topic_uri,
            event_group_id=resolved_event_group_id,
            delta=delta,
            epoch=epoch,
            apply_local=self.event_chain_done,
        )

    def event_chain_done(
        self,
        *,
        topic_uri: str,
        event_group_id: str,
        delta: int,
        epoch: int | None = None,
    ) -> dict[str, Any]:
        resolved_event_group_id = self._resolve_event_group_id(event_group_id)
        state = self._require_local_coordinator_state(topic_uri, epoch=epoch)
        result = self._ledger_manager.apply_event_chain_delta(
            state=state,
            event_group_id=resolved_event_group_id,
            delta=delta,
        )
        return self._attach_request_done_notify_intents(
            state=state,
            result=result,
        )

    def producer_done_or_forward(
        self,
        *,
        topic_uri: str,
        event_group_id: str,
        final_seq: int | None = None,
        expected_total_events: int | None = None,
        epoch: int | None = None,
    ) -> dict[str, Any]:
        resolved_event_group_id = self._resolve_event_group_id(event_group_id)
        return self._control_dispatch.producer_done_or_forward(
            topic_uri=topic_uri,
            event_group_id=resolved_event_group_id,
            final_seq=final_seq,
            expected_total_events=expected_total_events,
            epoch=epoch,
            apply_local=self.producer_done,
        )

    def request_outcome_or_forward(
        self,
        *,
        topic_uri: str,
        event_group_id: str,
        outcome_status: str,
        outcome_error_type: str | None = None,
        outcome_error_message: str | None = None,
        outcome_error_stage: str | None = None,
        outcome_metadata: dict[str, Any] | None = None,
        epoch: int | None = None,
    ) -> dict[str, Any]:
        resolved_event_group_id = self._resolve_event_group_id(event_group_id)
        return self._control_dispatch.request_outcome_or_forward(
            topic_uri=topic_uri,
            event_group_id=resolved_event_group_id,
            outcome_status=outcome_status,
            outcome_error_type=outcome_error_type,
            outcome_error_message=outcome_error_message,
            outcome_error_stage=outcome_error_stage,
            outcome_metadata=outcome_metadata,
            epoch=epoch,
            apply_local=self.request_outcome,
        )

    def producer_done(
        self,
        *,
        topic_uri: str,
        event_group_id: str,
        final_seq: int | None = None,
        expected_total_events: int | None = None,
        epoch: int | None = None,
    ) -> dict[str, Any]:
        resolved_event_group_id = self._resolve_event_group_id(event_group_id)
        state = self._require_local_coordinator_state(topic_uri, epoch=epoch)
        result = self._ledger_manager.apply_producer_done(
            state=state,
            event_group_id=resolved_event_group_id,
            final_seq=final_seq,
            expected_total_events=expected_total_events,
        )
        return self._attach_request_done_notify_intents(
            state=state,
            result=result,
        )

    def request_outcome(
        self,
        *,
        topic_uri: str,
        event_group_id: str,
        outcome_status: str,
        outcome_error_type: str | None = None,
        outcome_error_message: str | None = None,
        outcome_error_stage: str | None = None,
        outcome_metadata: dict[str, Any] | None = None,
        epoch: int | None = None,
    ) -> dict[str, Any]:
        resolved_event_group_id = self._resolve_event_group_id(event_group_id)
        state = self._require_local_coordinator_state(topic_uri, epoch=epoch)
        return self._ledger_manager.apply_outcome(
            state=state,
            event_group_id=resolved_event_group_id,
            outcome_status=outcome_status,
            error_type=outcome_error_type,
            error_message=outcome_error_message,
            error_stage=outcome_error_stage,
            metadata=outcome_metadata,
        )

    def apply_forward_intent(self, intent: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(intent, dict):
            raise TypeError("intent must be a dict.")
        mode = _normalize_non_empty(str(intent.get("mode") or ""), field_name="mode")
        topic_uri = _normalize_non_empty(
            str(intent.get("topic_uri") or ""),
            field_name="topic_uri",
        )
        epoch = _normalize_non_negative_int(intent.get("epoch"), field_name="epoch")
        local_address = self._local_address()
        if mode == "request_done_notify":
            subscriber_address = intent.get("subscriber_address")
            if subscriber_address is not None:
                normalized_subscriber_address = _normalize_non_empty(
                    str(subscriber_address),
                    field_name="subscriber_address",
                )
                if normalized_subscriber_address != local_address:
                    raise RuntimeError(
                        "forward_intent_subscriber_mismatch:"
                        f" expected_subscriber={normalized_subscriber_address},"
                        f" local_address={local_address}",
                    )
        else:
            coordinator_address = intent.get("coordinator_address")
            if coordinator_address is not None:
                normalized_owner = _normalize_non_empty(
                    str(coordinator_address),
                    field_name="coordinator_address",
                )
                if normalized_owner != local_address:
                    raise RuntimeError(
                        "forward_intent_owner_mismatch:"
                        f" expected_owner={normalized_owner},"
                        f" local_address={local_address}",
                    )
        if "request_ref_id" in intent:
            raise ValueError("request_ref_id is not allowed for v1 topic intents.")
        resolved_event_group_id = self._resolve_event_group_id(intent.get("event_group_id"))

        if mode == "forward_topic_event":
            tags = intent.get("tags")
            normalized_tags: dict[str, str] | None = None
            if isinstance(tags, dict):
                normalized_tags = {str(key): str(value) for key, value in tags.items()}
            result = self.publish_event(
                topic_uri=topic_uri,
                event_group_id=resolved_event_group_id,
                payload=intent.get("payload"),
                tags=normalized_tags,
                seq=intent.get("seq"),
                epoch=epoch,
            )
            return {"mode": "applied_topic_event", "result": result}

        if mode == "forward_event_chain_done":
            result = self.event_chain_done(
                topic_uri=topic_uri,
                event_group_id=resolved_event_group_id,
                delta=int(intent.get("delta") or 0),
                epoch=epoch,
            )
            return {"mode": "applied_event_chain_done", "result": result}

        if mode == "forward_producer_done":
            result = self.producer_done(
                topic_uri=topic_uri,
                event_group_id=resolved_event_group_id,
                final_seq=intent.get("final_seq"),
                expected_total_events=intent.get("expected_total_events"),
                epoch=epoch,
            )
            return {"mode": "applied_producer_done", "result": result}

        if mode == "forward_request_outcome":
            raw_outcome_metadata = intent.get("outcome_metadata")
            if raw_outcome_metadata is not None and not isinstance(raw_outcome_metadata, dict):
                raise TypeError("outcome_metadata must be a dict when provided.")
            result = self.request_outcome(
                topic_uri=topic_uri,
                event_group_id=resolved_event_group_id,
                outcome_status=_normalize_non_empty(
                    intent.get("outcome_status"),
                    field_name="outcome_status",
                ),
                outcome_error_type=intent.get("outcome_error_type"),
                outcome_error_message=intent.get("outcome_error_message"),
                outcome_error_stage=intent.get("outcome_error_stage"),
                outcome_metadata=dict(raw_outcome_metadata or {}),
                epoch=epoch,
            )
            return {"mode": "applied_request_outcome", "result": result}

        if mode == "request_done_notify":
            result = self.request_done_notify(
                topic_uri=topic_uri,
                event_group_id=resolved_event_group_id,
                subscriber_id=_normalize_non_empty(
                    intent.get("subscriber_id"),
                    field_name="subscriber_id",
                ),
                status=_normalize_non_empty(intent.get("status"), field_name="status"),
                final_seq=intent.get("final_seq"),
                expected_total_events=intent.get("expected_total_events"),
                epoch=epoch,
            )
            return {"mode": "applied_request_done_notify", "result": result}

        raise ValueError(f"unsupported_forward_intent_mode:{mode}")

    def event_group_ledger(
        self,
        *,
        topic_uri: str,
        event_group_id: str,
        epoch: int | None = None,
    ) -> dict[str, Any] | None:
        state = self._require_local_coordinator_state(topic_uri, epoch=epoch)
        return self._ledger_manager.snapshot(
            state=state,
            event_group_id=event_group_id,
        )

    def mixed_version_summary(
        self,
        *,
        topic_uri: str,
        epoch: int | None = None,
        include_requests: bool = False,
    ) -> dict[str, Any]:
        state = self._require_local_coordinator_state(topic_uri, epoch=epoch)
        return self._ledger_manager.mixed_version_summary(
            state=state,
            include_requests=include_requests,
        )

    def topic_switch_window(
        self,
        *,
        topic_uri: str,
    ) -> dict[str, Any] | None:
        switch_window = self.routing_directory.switch_window(topic_uri)
        if switch_window is None:
            return None
        return _switch_window_view_to_payload(switch_window)

    def gc_idle_runtime_state(
        self,
        *,
        max_idle_seconds: float,
    ) -> dict[str, int]:
        return {
            "removed_coordinator_states": self.coordinator_registry.gc_idle(
                max_idle_seconds=max_idle_seconds,
            ),
            "removed_subscriber_states": self.subscriber_registry.gc_idle(
                max_idle_seconds=max_idle_seconds,
            ),
        }

    def resolve_flow_program_by_ref(self, flow_program_ref: FlowProgramRef) -> Any | None:
        return self._resolve_flow_program_by_ref(flow_program_ref)

    def require_topic_route(
        self,
        topic_uri: str,
        epoch: int | None = None,
    ) -> TopicRouteView:
        return self.routing_directory.require(topic_uri, epoch=epoch)

    def publish_flow_output(
        self,
        out_topic_uri: str,
        event_group_id: str,
        payload: Any,
        tags: dict[str, str] | None,
        seq: int | None,
        out_topic_epoch: int | None = None,
    ) -> dict[str, Any]:
        return self._publish_flow_output(
            out_topic_uri=out_topic_uri,
            event_group_id=event_group_id,
            payload=payload,
            tags=tags,
            seq=seq,
            out_topic_epoch=out_topic_epoch,
        )

    def apply_or_forward_event_chain_delta(
        self,
        out_topic_uri: str,
        event_group_id: str,
        delta: int,
        out_topic_epoch: int | None = None,
    ) -> dict[str, Any] | None:
        return self._apply_or_forward_event_chain_delta(
            out_topic_uri=out_topic_uri,
            event_group_id=event_group_id,
            delta=delta,
            out_topic_epoch=out_topic_epoch,
        )

    def apply_or_forward_request_outcome(
        self,
        out_topic_uri: str,
        event_group_id: str,
        outcome_status: str,
        outcome_error_type: str | None,
        outcome_error_message: str | None,
        outcome_error_stage: str | None,
        outcome_metadata: dict[str, Any] | None,
        out_topic_epoch: int | None = None,
    ) -> dict[str, Any] | None:
        return self._apply_or_forward_request_outcome(
            out_topic_uri=out_topic_uri,
            event_group_id=event_group_id,
            outcome_status=outcome_status,
            outcome_error_type=outcome_error_type,
            outcome_error_message=outcome_error_message,
            outcome_error_stage=outcome_error_stage,
            outcome_metadata=outcome_metadata,
            out_topic_epoch=out_topic_epoch,
        )

    def _handle_request_done(self, request_done: dict[str, Any]) -> None:
        request_done_copy = dict(request_done)
        switch_window = self.routing_directory.observe_completion(
            topic_uri=request_done_copy.get("topic_uri"),
            epoch=request_done_copy.get("epoch"),
            event_group_id=request_done_copy.get("event_group_id"),
            observed_at_s=request_done_copy.get("completed_at"),
        )
        if switch_window is not None:
            request_done_copy["switch_window"] = _switch_window_view_to_payload(switch_window)
        if callable(self._on_request_done):
            self._on_request_done(dict(request_done_copy))
        intents = self._build_request_done_notify_intents(
            request_done=request_done_copy,
        )
        if intents and callable(self._on_request_done_notify_intents):
            self._on_request_done_notify_intents([dict(intent) for intent in intents])

    def _attach_request_done_notify_intents(
        self,
        *,
        state: CoordinatorTopicState,
        result: dict[str, Any],
    ) -> dict[str, Any]:
        request_done = result.get("request_done")
        if not isinstance(request_done, dict):
            return result
        switch_window = self.routing_directory.observe_completion(
            topic_uri=request_done.get("topic_uri"),
            epoch=request_done.get("epoch"),
            event_group_id=request_done.get("event_group_id"),
            observed_at_s=request_done.get("completed_at"),
        )
        if switch_window is not None:
            request_done["switch_window"] = _switch_window_view_to_payload(switch_window)
        intents = self._build_request_done_notify_intents_for_state(
            state=state,
            request_done=request_done,
        )
        if intents:
            result["request_done_notify_intents"] = intents
        return result

    def _build_request_done_notify_intents(
        self,
        *,
        request_done: dict[str, Any],
    ) -> list[dict[str, Any]]:
        topic_uri = _normalize_non_empty(request_done.get("topic_uri"), field_name="topic_uri")
        epoch = _normalize_non_negative_int(request_done.get("epoch"), field_name="epoch")
        state = self.coordinator_registry.get(topic_uri, epoch)
        if state is None:
            return []
        return self._build_request_done_notify_intents_for_state(
            state=state,
            request_done=request_done,
        )

    def _build_request_done_notify_intents_for_state(
        self,
        *,
        state: CoordinatorTopicState,
        request_done: dict[str, Any],
    ) -> list[dict[str, Any]]:
        event_group_id = self._resolve_event_group_id(request_done.get("event_group_id"))
        status = _normalize_non_empty(request_done.get("status"), field_name="status")
        final_seq = request_done.get("final_seq")
        expected_total_events = request_done.get("expected_total_events")
        if final_seq is not None:
            final_seq = _normalize_non_negative_int(final_seq, field_name="final_seq")
        if expected_total_events is None:
            raise ValueError("request_done expected_total_events must be present.")
        expected_total_events = _normalize_non_negative_int(
            expected_total_events,
            field_name="expected_total_events",
        )

        intents: list[dict[str, Any]] = []
        for subscriber_id in sorted(state.subscriber_ids):
            subscriber_address = state.subscriber_addresses.get(subscriber_id)
            if subscriber_address is None:
                continue
            intent: dict[str, Any] = {
                "mode": "request_done_notify",
                "coordinator_address": self._local_address(),
                "subscriber_address": _normalize_non_empty(
                    subscriber_address,
                    field_name="subscriber_address",
                ),
                "topic_uri": state.topic_uri,
                "epoch": state.epoch,
                "event_group_id": event_group_id,
                "subscriber_id": subscriber_id,
                "status": status,
            }
            if final_seq is not None:
                intent["final_seq"] = final_seq
            intent["expected_total_events"] = expected_total_events
            intents.append(intent)
        return intents

    def _local_address(self) -> str:
        return _normalize_non_empty(self._local_address_fn(), field_name="local_address")

    def _resolve_event_group_id(self, event_group_id: str) -> str:
        return self._ledger_manager.normalize_event_group_id(event_group_id)

    def _publish_local_event(
        self,
        *,
        topic_uri: str,
        event_group_id: str,
        payload: Any,
        tags: dict[str, str] | None,
        seq: int | None,
        epoch: int | None,
    ) -> dict[str, Any]:
        state = self._require_local_coordinator_state(topic_uri, epoch=epoch)
        event_record = self._ledger_manager.record_local_event_publish(
            state=state,
            event_group_id=event_group_id,
            payload=payload,
            tags=tags,
            seq=seq,
        )
        self._notify_topic_event(event_record)
        return event_record

    def _notify_topic_event(self, event_record: dict[str, Any]) -> None:
        if callable(self._on_topic_event):
            self._on_topic_event(dict(event_record))
        with self._topic_event_listener_lock:
            listeners = list(self._topic_event_listeners.values())
        for listener in listeners:
            listener(dict(event_record))

    def _publish_flow_output(
        self,
        out_topic_uri: str,
        event_group_id: str,
        payload: Any,
        tags: dict[str, str] | None,
        seq: int | None,
        out_topic_epoch: int | None,
    ) -> dict[str, Any]:
        route = self.routing_directory.require(out_topic_uri, epoch=out_topic_epoch)
        if route.coordinator_address == self._local_address():
            published = self._publish_local_event(
                topic_uri=route.topic_uri,
                event_group_id=event_group_id,
                payload=payload,
                tags=tags,
                seq=seq,
                epoch=route.epoch,
            )
            result = dict(published)
            result["mode"] = "local"
            return result
        return self._event_dispatch.build_forward_topic_event_intent(
            coordinator_address=route.coordinator_address,
            topic_uri=route.topic_uri,
            epoch=route.epoch,
            event_group_id=event_group_id,
            payload=payload,
            tags=tags,
            seq=seq,
        )

    def _apply_or_forward_event_chain_delta(
        self,
        out_topic_uri: str,
        event_group_id: str,
        delta: int,
        out_topic_epoch: int | None,
    ) -> dict[str, Any] | None:
        route = self.routing_directory.require(out_topic_uri, epoch=out_topic_epoch)
        if route.coordinator_address == self._local_address():
            self.event_chain_done(
                topic_uri=route.topic_uri,
                event_group_id=event_group_id,
                delta=delta,
                epoch=route.epoch,
            )
            return None
        return self._control_dispatch.build_forward_event_chain_done_intent(
            coordinator_address=route.coordinator_address,
            topic_uri=route.topic_uri,
            epoch=route.epoch,
            event_group_id=event_group_id,
            delta=delta,
        )

    def _apply_or_forward_request_outcome(
        self,
        *,
        out_topic_uri: str,
        event_group_id: str,
        outcome_status: str,
        outcome_error_type: str | None,
        outcome_error_message: str | None,
        outcome_error_stage: str | None,
        outcome_metadata: dict[str, Any] | None,
        out_topic_epoch: int | None,
    ) -> dict[str, Any] | None:
        route = self.routing_directory.require(out_topic_uri, epoch=out_topic_epoch)
        if route.coordinator_address == self._local_address():
            self.request_outcome(
                topic_uri=route.topic_uri,
                event_group_id=event_group_id,
                outcome_status=outcome_status,
                outcome_error_type=outcome_error_type,
                outcome_error_message=outcome_error_message,
                outcome_error_stage=outcome_error_stage,
                outcome_metadata=outcome_metadata,
                epoch=route.epoch,
            )
            return None
        return self._control_dispatch.build_forward_request_outcome_intent(
            coordinator_address=route.coordinator_address,
            topic_uri=route.topic_uri,
            epoch=route.epoch,
            event_group_id=event_group_id,
            outcome_status=outcome_status,
            outcome_error_type=outcome_error_type,
            outcome_error_message=outcome_error_message,
            outcome_error_stage=outcome_error_stage,
            outcome_metadata=outcome_metadata,
        )

    def _resolve_flow_program_by_ref(self, flow_program_ref: FlowProgramRef) -> Any | None:
        normalized_ref = self._normalize_program_ref(flow_program_ref)
        flow_program = self._flow_program_cache.resolve(
            flow_program_ref=normalized_ref,
        )
        if flow_program is None:
            return None
        self._validate_flow_program_runtime(flow_program)
        return flow_program

    def _resolve_flow_program(self, flow_program_ref: FlowProgramRef) -> Any | None:
        return self._resolve_flow_program_by_ref(flow_program_ref)

    def _resolve_flow_program_ref(
        self,
        *,
        flow_program_uri: str,
        flow_program_rev: str,
    ) -> FlowProgramRef:
        normalized_program_uri = _normalize_non_empty(
            flow_program_uri,
            field_name="flow_program_uri",
        )
        return FlowProgramRef(
            program_uri=normalized_program_uri,
            program_rev=_normalize_non_empty(
                flow_program_rev,
                field_name="flow_program_rev",
            ),
        )

    @staticmethod
    def _normalize_program_ref(flow_program_ref: FlowProgramRef) -> FlowProgramRef:
        if not isinstance(flow_program_ref, FlowProgramRef):
            raise TypeError("flow_program_ref must be FlowProgramRef.")
        program_uri = _normalize_non_empty(
            flow_program_ref.program_uri,
            field_name="flow_program_ref.program_uri",
        )
        program_rev = _normalize_non_empty(
            flow_program_ref.program_rev,
            field_name="flow_program_ref.program_rev",
        )
        return FlowProgramRef(program_uri=program_uri, program_rev=program_rev)

    @staticmethod
    def _validate_flow_program_runtime(flow_program: Any) -> None:
        if flow_program is None:
            raise TypeError("flow_program must not be None.")
        if not callable(getattr(flow_program, "lookup_transformation", None)):
            raise TypeError(
                "flow_program must provide callable lookup_transformation(trans_id).",
            )
        if not callable(getattr(flow_program, "resolve_entry_transformation", None)):
            raise TypeError(
                "flow_program must provide callable resolve_entry_transformation().",
            )

    def _require_topic_route(
        self,
        topic_uri: str,
        *,
        epoch: int | None,
    ) -> TopicRouteView:
        return self.routing_directory.require(topic_uri, epoch=epoch)

    def _require_local_coordinator_state(
        self,
        topic_uri: str,
        *,
        epoch: int | None,
    ) -> CoordinatorTopicState:
        route = self._require_topic_route(topic_uri, epoch=epoch)
        expected_epoch = int(route.epoch)
        local_address = self._local_address()
        if route.coordinator_address != local_address:
            raise RuntimeError(
                "not_local_coordinator:"
                f" topic_uri={route.topic_uri},"
                f" expected_owner={route.coordinator_address},"
                f" local_address={local_address}",
            )
        return self.coordinator_registry.get_or_create(route.topic_uri, expected_epoch)


def _switch_window_view_to_payload(view: TopicSwitchWindowView) -> dict[str, Any]:
    return {
        "topic_uri": view.topic_uri,
        "old_epoch": view.old_epoch,
        "new_epoch": view.new_epoch,
        "old_version_admission_cutoff_s": view.old_version_admission_cutoff_s,
        "new_version_first_admission_s": view.new_version_first_admission_s,
        "new_version_first_admission_event_group_id": view.new_version_first_admission_event_group_id,
        "old_version_last_completion_s": view.old_version_last_completion_s,
        "old_version_last_completion_event_group_id": view.old_version_last_completion_event_group_id,
        "w_switch_s": view.w_switch_s,
        "admission_gap_s": view.admission_gap_s,
        "overlap_s": view.overlap_s,
        "missing_markers": list(view.missing_markers),
        "anomalies": list(view.anomalies),
        "reason": view.reason,
        "complete": view.complete,
    }


__all__ = [
    "FlowProcessRecord",
    "FlowProcessCatalog",
    "TopicRouteView",
    "TopicRoutingDirectory",
    "TopicSwitchWindowView",
    "FlowProgramRouteView",
    "FlowProgramRoutingDirectory",
    "EventGroupLedger",
    "CoordinatorTopicState",
    "TopicCoordinatorRegistry",
    "TopicAPI",
]
