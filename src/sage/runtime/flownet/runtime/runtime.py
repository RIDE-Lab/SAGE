from __future__ import annotations

import asyncio
from collections.abc import Coroutine, Mapping
from concurrent.futures import CancelledError as FutureCancelledError
from concurrent.futures import Future
from concurrent.futures import TimeoutError as FutureTimeoutError
from os import PathLike
from threading import Lock
from typing import Any, Protocol

from sage.runtime.flownet.runtime.actors import (
    ActorAPI,
    ExecutorLanes,
    call_actor_via_comm,
    register_actor_call_rpc_handler,
)
from sage.runtime.flownet.runtime.actors.callback_registry import (
    CALLBACK_REGISTRY_SERVICE_ACTOR_ID,
    ActorCallbackRuntime,
    CallbackRegistry,
    CallbackRegistryServiceActor,
)
from sage.runtime.flownet.runtime.backend_container import (
    BackendContainer,
    BackendContainerPlugin,
    BackendContainerRegistry,
)
from sage.runtime.flownet.runtime.collective import (
    CollectiveExecutor,
    CollectiveExecutorRegistry,
    ensure_default_collective_executors,
)
from sage.runtime.flownet.runtime.comm import (
    OP_CONTROL_TOPIC_EVENT_CHAIN_DONE_FORWARD,
    OP_CONTROL_TOPIC_PRODUCER_DONE_FORWARD,
    OP_CONTROL_TOPIC_REQUEST_DONE_NOTIFY,
    OP_CONTROL_TOPIC_REQUEST_OUTCOME_FORWARD,
    OP_DATA_TOPIC_EVENT_FORWARD,
    OP_RPC_ACTOR_CALL,
    OP_RPC_FLOW_PROGRAM_PULL,
    PLANE_CONTROL,
    PLANE_DATA,
    PLANE_RPC,
    V1CommHub,
    V1TransportBackend,
)
from sage.runtime.flownet.runtime.endpoint_registry import FlowEndpointRegistry
from sage.runtime.flownet.runtime.flowengine import (
    CollectiveRuntime,
    FlowEngineV1,
    FlowProcessExecution,
    build_actor_payload_target_invoker,
    build_flow_program_pull_resolver,
    register_flow_program_pull_handler,
)
from sage.runtime.flownet.runtime.governance import RuntimeGovernanceManager
from sage.runtime.flownet.runtime.loops import LoopThread
from sage.runtime.flownet.runtime.shared_state_registry import SharedStateServiceRegistry
from sage.runtime.flownet.runtime.topics import (
    FlowProgramRoutingDirectory,
    TopicAPI,
    build_actor_exception_handler_invoker,
    register_topic_forward_handlers,
    send_topic_forward_intent_via_comm,
)


class ClusterBackendRouter(Protocol):
    def list_cluster_backends(
        self,
        *,
        required_tags: Mapping[str, str] | None = None,
        required_capabilities: Mapping[str, Any] | None = None,
        include_metrics: bool = True,
    ) -> tuple[dict[str, Any], ...]: ...

    def submit_remote_backend_job(
        self,
        *,
        node_address: str,
        request: Mapping[str, Any],
        backend_id: str | None = None,
        required_tags: Mapping[str, str] | None = None,
        required_capabilities: Mapping[str, Any] | None = None,
        preferred_backend_id: str | None = None,
        request_epoch: int | None = None,
    ) -> Mapping[str, Any]: ...

    def poll_remote_backend_job(
        self,
        *,
        node_address: str,
        job_token: str,
        auto_ack: bool = False,
    ) -> Mapping[str, Any] | None: ...

    def cancel_remote_backend_job(
        self,
        *,
        node_address: str,
        job_token: str,
        reason: str = "",
    ) -> bool: ...

    def ack_remote_backend_job(
        self,
        *,
        node_address: str,
        job_token: str,
    ) -> bool: ...


class V1RuntimeHost:
    """
    Minimal v1 runtime host.

    Owns loop threads and composes v1 ActorAPI + TopicAPI into one runtime entry.
    """

    def __init__(
        self,
        *,
        local_address: str,
        actor_api: ActorAPI | None = None,
        comm_hub: V1CommHub | None = None,
        comm_transport: V1TransportBackend | None = None,
        topic_api: TopicAPI | None = None,
        executor_lanes: ExecutorLanes | None = None,
        runtime_loop: LoopThread | None = None,
        user_loop: LoopThread | None = None,
        default_cpu_max_workers: int | None = None,
        exception_handler_timeout: float | None = None,
        gc_idle_interval_seconds: float | None = 30.0,
        gc_idle_ttl_seconds: float = 300.0,
        backend_container_registry: BackendContainerRegistry | None = None,
        collective_executor_registry: CollectiveExecutorRegistry | None = None,
        endpoint_registry: FlowEndpointRegistry | None = None,
        shared_state_registry: SharedStateServiceRegistry | None = None,
        topic_coordinator_causal_cut_path: str | PathLike[str] | None = None,
    ) -> None:
        if comm_hub is not None and comm_transport is not None:
            raise ValueError("comm_hub and comm_transport cannot both be provided.")

        normalized_gc_interval_seconds = self._normalize_optional_interval_seconds(
            gc_idle_interval_seconds,
            field_name="gc_idle_interval_seconds",
        )
        normalized_gc_ttl_seconds = self._normalize_non_negative_seconds(
            gc_idle_ttl_seconds,
            field_name="gc_idle_ttl_seconds",
        )

        self._runtime_loop = runtime_loop or LoopThread("flownet-v1-runtime-loop")
        self._user_loop = user_loop or LoopThread("flownet-v1-user-loop")
        self._owns_runtime_loop = runtime_loop is None
        self._owns_user_loop = user_loop is None
        self._closed = False
        self._gc_idle_interval_seconds = normalized_gc_interval_seconds
        self._gc_idle_ttl_seconds = normalized_gc_ttl_seconds
        self._gc_loop_future: Future | None = None
        self._callback_runtime: ActorCallbackRuntime | None = None
        self._backend_container_registry = backend_container_registry or BackendContainerRegistry()
        self._cluster_backend_router: ClusterBackendRouter | None = None
        self._collective_executor_registry = (
            collective_executor_registry or CollectiveExecutorRegistry()
        )
        ensure_default_collective_executors(self._collective_executor_registry)
        self.governance = RuntimeGovernanceManager()
        self.endpoint_registry = endpoint_registry or FlowEndpointRegistry()
        self.shared_state_registry = shared_state_registry or SharedStateServiceRegistry()
        set_governance_manager = getattr(self.shared_state_registry, "set_governance_manager", None)
        if callable(set_governance_manager):
            set_governance_manager(self.governance)
        self._scheduler_observability_lock = Lock()
        self._scheduler_observability = {
            "selection_total": 0,
            "fallback_count": 0,
            "spillover_decision_count": 0,
            "spillover_remote_used_count": 0,
            "spillover_remote_blocked_count": 0,
            "spillover_active_claims": 0,
        }

        if actor_api is not None and executor_lanes is not None:
            raise ValueError("actor_api and executor_lanes cannot both be provided.")

        self._owns_executor_lanes = False
        self.executor_lanes: ExecutorLanes | None = executor_lanes
        if actor_api is None:
            if self.executor_lanes is None:
                self.executor_lanes = ExecutorLanes(
                    default_cpu_max_workers=default_cpu_max_workers,
                )
                self._owns_executor_lanes = True
            self.actor_api = ActorAPI(
                local_address=local_address,
                executor_lanes=self.executor_lanes,
                runtime_host=self,
            )
            self._owns_actor_api = True
        else:
            self.actor_api = actor_api
            self._owns_actor_api = False
            self.executor_lanes = None
        set_runtime_host = getattr(self.actor_api, "set_runtime_host", None)
        if callable(set_runtime_host):
            set_runtime_host(self)

        self.comm_hub = comm_hub or V1CommHub(
            local_address=local_address,
            transport=comm_transport,
        )
        self._owns_comm_hub = comm_hub is None
        self._ensure_actor_call_rpc_handler()
        invoke_exception_handler = build_actor_exception_handler_invoker(
            actor_api=self.actor_api,
            comm_hub=self.comm_hub,
            timeout=exception_handler_timeout,
            run_coroutine=self._run_user_coro_blocking,
        )

        if topic_api is None:
            flow_program_routing_directory = FlowProgramRoutingDirectory()
            pull_flow_program = build_flow_program_pull_resolver(
                comm_hub=self.comm_hub,
                resolve_owner_address=(
                    lambda flow_program_ref: (
                        flow_program_routing_directory.require(flow_program_ref).owner_address
                    )
                ),
                run_coroutine=self._run_user_coro_blocking,
            )
            self.topic_api = TopicAPI(
                local_address=local_address,
                flow_program_routing_directory=flow_program_routing_directory,
                pull_flow_program=pull_flow_program,
                coordinator_causal_cut_path=topic_coordinator_causal_cut_path,
            )
            self._owns_topic_api = True
        else:
            self.topic_api = topic_api
            self._owns_topic_api = False
        self._ensure_topic_forward_handlers()
        self._ensure_callback_runtime()
        self.actor_api.set_event_dispatcher(self._dispatch_actor_event)
        if not self.topic_api.has_ingress_event_handler():
            invoke_actor_target = build_actor_payload_target_invoker(
                actor_api=self.actor_api,
                comm_hub=self.comm_hub,
                run_coroutine=self._run_user_coro_blocking,
            )
            flow_process_execution = FlowProcessExecution(
                flow_process_catalog=self.topic_api.flow_process_catalog,
                flow_engine=FlowEngineV1(
                    collective_runtime=CollectiveRuntime(
                        executor_registry=self._collective_executor_registry,
                    ),
                    run_coroutine=self._run_user_coro_blocking,
                ),
                resolve_flow_program=self.topic_api.resolve_flow_program_by_ref,
                resolve_topic_route=self.topic_api.require_topic_route,
                publish_flow_output=self.topic_api.publish_flow_output,
                apply_or_forward_event_chain_delta=self.topic_api.apply_or_forward_event_chain_delta,
                apply_or_forward_request_outcome=self.topic_api.apply_or_forward_request_outcome,
                invoke_actor_target=invoke_actor_target,
                invoke_exception_handler=invoke_exception_handler,
            )
            self.topic_api.set_ingress_event_handler(flow_process_execution.dispatch)
            self._flow_process_execution = flow_process_execution
        else:
            self._flow_process_execution = None
        self._ensure_flow_program_pull_rpc_handler()
        self._start_idle_gc_loop_if_enabled()

    @property
    def runtime_loop(self) -> LoopThread:
        return self._runtime_loop

    @property
    def user_loop(self) -> LoopThread:
        return self._user_loop

    def submit_runtime_coro(self, coro: Coroutine) -> Future:
        self._ensure_open()
        return self._runtime_loop.submit(coro)

    def submit_user_coro(self, coro: Coroutine) -> Future:
        self._ensure_open()
        return self._user_loop.submit(coro)

    def call_local_actor(
        self,
        actor_id: str,
        method: str,
        *args: Any,
        timeout: float | None = None,
        **kwargs: Any,
    ) -> Any:
        self._ensure_open()
        fut = self.submit_user_coro(self.actor_api.call_local(actor_id, method, *args, **kwargs))
        return fut.result(timeout=timeout)

    def call_or_prepare_actor_call(
        self,
        target: Any,
        *args: Any,
        timeout: float | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        self._ensure_open()
        fut = self.submit_user_coro(self.actor_api.call_or_prepare_call(target, *args, **kwargs))
        return fut.result(timeout=timeout)

    def gc_idle_runtime_state(
        self,
        *,
        max_idle_seconds: float,
    ) -> dict[str, int]:
        self._ensure_open()
        return self.topic_api.gc_idle_runtime_state(
            max_idle_seconds=max_idle_seconds,
        )

    def register_backend_plugin(self, plugin: BackendContainerPlugin) -> None:
        self._ensure_open()
        self._backend_container_registry.register_plugin(plugin)

    def unregister_backend_plugin(self, plugin_id: str) -> bool:
        self._ensure_open()
        return self._backend_container_registry.unregister_plugin(plugin_id)

    def register_backend_container(
        self,
        container: BackendContainer,
        *,
        tags: Mapping[str, str] | None = None,
        capabilities: Mapping[str, Any] | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        self._ensure_open()
        self._backend_container_registry.register_container(
            container,
            tags=tags,
            capabilities=capabilities,
            metadata=metadata,
        )

    def unregister_backend_container(self, backend_id: str) -> bool:
        self._ensure_open()
        return self._backend_container_registry.unregister_container(backend_id)

    def get_backend_container(self, backend_id: str) -> BackendContainer | None:
        self._ensure_open()
        return self._backend_container_registry.get_container(backend_id)

    def list_backend_containers(
        self,
        *,
        include_metrics: bool = True,
    ) -> tuple[dict[str, Any], ...]:
        self._ensure_open()
        return self._backend_container_registry.list_containers(include_metrics=include_metrics)

    def find_backend_containers(
        self,
        *,
        required_tags: Mapping[str, str] | None = None,
        required_capabilities: Mapping[str, Any] | None = None,
        include_metrics: bool = True,
    ) -> tuple[dict[str, Any], ...]:
        self._ensure_open()
        return self._backend_container_registry.find_containers(
            required_tags=required_tags,
            required_capabilities=required_capabilities,
            include_metrics=include_metrics,
        )

    def configure_cluster_backend_router(self, router: ClusterBackendRouter | None) -> None:
        self._ensure_open()
        if router is None:
            self._cluster_backend_router = None
            return
        required_methods = (
            "list_cluster_backends",
            "submit_remote_backend_job",
            "poll_remote_backend_job",
            "cancel_remote_backend_job",
            "ack_remote_backend_job",
        )
        for method_name in required_methods:
            if not callable(getattr(router, method_name, None)):
                raise TypeError(f"cluster_backend_router_missing_method:{method_name}")
        self._cluster_backend_router = router

    def clear_cluster_backend_router(self) -> None:
        self._ensure_open()
        self._cluster_backend_router = None

    def select_backend_container(
        self,
        *,
        required_tags: Mapping[str, str] | None = None,
        required_capabilities: Mapping[str, Any] | None = None,
        preferred_backend_id: str | None = None,
        request_epoch: int | None = None,
    ) -> dict[str, Any]:
        self._ensure_open()
        candidates = list(
            self._backend_container_registry.find_containers(
                required_tags=required_tags,
                required_capabilities=required_capabilities,
                include_metrics=True,
            )
        )
        if not candidates:
            raise RuntimeError(
                "backend_container_no_candidate:"
                f"required_tags={_format_required_tags(required_tags)},"
                f"required_capabilities={_format_required_capabilities(required_capabilities)}",
            )
        try:
            return _select_backend_record_from_candidates(
                candidates=candidates,
                preferred_backend_id=preferred_backend_id,
                request_epoch=request_epoch,
                local_node_address=None,
            )
        except RuntimeError as exc:
            message = str(exc)
            if message == "backend_container_no_schedulable_candidate":
                raise RuntimeError(
                    "backend_container_no_schedulable_candidate:"
                    f"required_tags={_format_required_tags(required_tags)},"
                    f"required_capabilities={_format_required_capabilities(required_capabilities)}",
                ) from exc
            raise

    def submit_backend_job(
        self,
        *,
        request: Mapping[str, Any],
        backend_id: str | None = None,
        required_tags: Mapping[str, str] | None = None,
        required_capabilities: Mapping[str, Any] | None = None,
        preferred_backend_id: str | None = None,
        request_epoch: int | None = None,
        timeout_seconds: float | None = None,
        poll_interval_seconds: float = 0.01,
        auto_ack: bool = True,
    ) -> Future:
        self._ensure_open()
        normalized_backend_id = _normalize_optional_non_empty(backend_id)
        normalized_request_epoch = (
            _normalize_optional_epoch(request_epoch)
            if request_epoch is not None
            else _resolve_request_epoch_from_request(request)
        )
        selection_trace: dict[str, Any] | None = None
        cluster_router = self._cluster_backend_router
        remote_node_address: str | None = None
        remote_submit_attempts: list[dict[str, Any]] = []
        local_selection_error: RuntimeError | None = None
        if normalized_backend_id is None:
            try:
                selection = self.select_backend_container(
                    required_tags=required_tags,
                    required_capabilities=required_capabilities,
                    preferred_backend_id=preferred_backend_id,
                    request_epoch=normalized_request_epoch,
                )
                normalized_backend_id = _normalize_optional_non_empty(selection.get("backend_id"))
                selection_trace = _build_backend_selection_trace(
                    selection=selection,
                    required_tags=required_tags,
                    required_capabilities=required_capabilities,
                )
            except RuntimeError as exc:
                local_selection_error = exc

            if normalized_backend_id is None and cluster_router is not None:
                cluster_candidates = list(
                    cluster_router.list_cluster_backends(
                        required_tags=required_tags,
                        required_capabilities=required_capabilities,
                        include_metrics=True,
                    )
                )
                if cluster_candidates:
                    cluster_selection = _select_backend_record_from_candidates(
                        candidates=cluster_candidates,
                        preferred_backend_id=preferred_backend_id,
                        request_epoch=normalized_request_epoch,
                        local_node_address=self.comm_hub.local_address,
                    )
                    normalized_backend_id = _normalize_optional_non_empty(
                        cluster_selection.get("backend_id")
                    )
                    remote_node_address = _normalize_optional_non_empty(
                        cluster_selection.get("node_address")
                    )
                    selection_trace = {
                        "backend_id": cluster_selection.get("backend_id"),
                        "node_id": cluster_selection.get("node_id"),
                        "node_address": cluster_selection.get("node_address"),
                        "request_epoch": cluster_selection.get("request_epoch"),
                        "matched_request_epoch": cluster_selection.get("matched_request_epoch"),
                        "epoch_policy": cluster_selection.get("epoch_policy"),
                        "epoch_fallback": cluster_selection.get("epoch_fallback"),
                        "selection_reason": cluster_selection.get("selection_reason"),
                        "required_tags": dict(required_tags or {}),
                        "required_capabilities": _normalize_backend_capability_mapping(
                            required_capabilities
                        ),
                    }
                    if remote_node_address == self.comm_hub.local_address:
                        remote_node_address = None
                    elif remote_node_address is not None and normalized_backend_id is not None:
                        remote_submit_attempts.append(
                            {
                                "node_address": remote_node_address,
                                "backend_id": normalized_backend_id,
                                "selection_trace": dict(selection_trace),
                            }
                        )
                        failover_selection = _select_cluster_failover_record(
                            candidates=cluster_candidates,
                            failed_node_address=remote_node_address,
                            preferred_backend_id=preferred_backend_id,
                            request_epoch=normalized_request_epoch,
                            local_node_address=self.comm_hub.local_address,
                        )
                        if failover_selection is not None:
                            failover_backend_id = _normalize_optional_non_empty(
                                failover_selection.get("backend_id")
                            )
                            failover_node_address = _normalize_optional_non_empty(
                                failover_selection.get("node_address")
                            )
                            if (
                                failover_backend_id is not None
                                and failover_node_address is not None
                                and failover_node_address != self.comm_hub.local_address
                            ):
                                failover_trace = _build_backend_selection_trace(
                                    selection=failover_selection,
                                    required_tags=required_tags,
                                    required_capabilities=required_capabilities,
                                )
                                failover_trace["fallback_used"] = True
                                failover_trace["fallback_reason"] = "remote_transport_retry_once"
                                failover_trace["failed_node_address"] = remote_node_address
                                remote_submit_attempts.append(
                                    {
                                        "node_address": failover_node_address,
                                        "backend_id": failover_backend_id,
                                        "selection_trace": failover_trace,
                                    }
                                )
                elif local_selection_error is not None:
                    raise local_selection_error
        elif cluster_router is not None:
            local_backend = self._backend_container_registry.get_container(normalized_backend_id)
            if local_backend is None:
                cluster_candidates = list(
                    cluster_router.list_cluster_backends(
                        required_tags=None,
                        required_capabilities=None,
                        include_metrics=True,
                    )
                )
                explicit_cluster_record = _find_backend_record_in_candidates(
                    candidates=cluster_candidates,
                    backend_id=normalized_backend_id,
                )
                if explicit_cluster_record is not None:
                    if not _record_matches_backend_requirements(
                        explicit_cluster_record,
                        required_tags=required_tags,
                        required_capabilities=required_capabilities,
                    ):
                        raise RuntimeError(
                            "backend_container_requirement_constraint_mismatch:"
                            "backend_id="
                            f"{normalized_backend_id},required_tags={_format_required_tags(required_tags)},"
                            f"required_capabilities={_format_required_capabilities(required_capabilities)}",
                        )
                    remote_node_address = _normalize_optional_non_empty(
                        explicit_cluster_record.get("node_address")
                    )
                    if remote_node_address == self.comm_hub.local_address:
                        remote_node_address = None
                    elif remote_node_address is not None:
                        raw_metrics = explicit_cluster_record.get("metrics")
                        metrics = dict(raw_metrics) if isinstance(raw_metrics, Mapping) else {}
                        backend_epoch = _normalize_optional_epoch(metrics.get("epoch"))
                        epoch_policy = _resolve_epoch_policy(
                            backend_epoch=backend_epoch,
                            request_epoch=normalized_request_epoch,
                        )
                        selection_trace = {
                            "backend_id": explicit_cluster_record.get("backend_id"),
                            "node_id": explicit_cluster_record.get("node_id"),
                            "node_address": explicit_cluster_record.get("node_address"),
                            "request_epoch": normalized_request_epoch,
                            "matched_request_epoch": epoch_policy == "match",
                            "epoch_policy": epoch_policy,
                            "epoch_fallback": epoch_policy != "match",
                            "selection_reason": "explicit_backend_id",
                            "required_tags": dict(required_tags or {}),
                            "required_capabilities": _normalize_backend_capability_mapping(
                                required_capabilities
                            ),
                            "preferred_hit": False,
                        }
                        remote_submit_attempts.append(
                            {
                                "node_address": remote_node_address,
                                "backend_id": normalized_backend_id,
                                "selection_trace": dict(selection_trace),
                            }
                        )

        if selection_trace is not None:
            self._record_scheduler_selection(
                selection_trace,
                remote_used=remote_node_address is not None,
            )

        if normalized_backend_id is None:
            if local_selection_error is not None:
                raise local_selection_error
            raise RuntimeError("backend_container_selection_failed")

        if remote_node_address is None and (required_tags or required_capabilities):
            tag_matched_records = self._backend_container_registry.find_containers(
                required_tags=required_tags,
                required_capabilities=required_capabilities,
                include_metrics=False,
            )
            allowed_backend_ids = {
                _normalize_optional_non_empty(record.get("backend_id"))
                for record in tag_matched_records
            }
            if normalized_backend_id not in allowed_backend_ids:
                raise RuntimeError(
                    "backend_container_requirement_constraint_mismatch:"
                    f"backend_id={normalized_backend_id},required_tags={_format_required_tags(required_tags)},"
                    f"required_capabilities={_format_required_capabilities(required_capabilities)}",
                )

        backend: BackendContainer | None = None
        if remote_node_address is None:
            backend = self._backend_container_registry.get_container(normalized_backend_id)
            if backend is None:
                raise RuntimeError(f"backend_container_not_found:{normalized_backend_id}")
        resolved_poll_interval = max(0.001, float(poll_interval_seconds))
        resolved_timeout_seconds = (
            None if timeout_seconds is None else max(0.001, float(timeout_seconds))
        )
        base_request_payload = dict(request)
        if normalized_request_epoch is not None:
            base_request_payload.setdefault("request_epoch", normalized_request_epoch)

        async def _run_backend_job() -> Mapping[str, Any]:
            active_backend_id = normalized_backend_id
            active_remote_node_address = remote_node_address
            if active_remote_node_address is not None:
                router = self._cluster_backend_router
                if router is None:
                    raise RuntimeError("cluster_backend_router_not_configured")
                attempt_plan = list(remote_submit_attempts)
                if not attempt_plan:
                    attempt_plan = [
                        {
                            "node_address": active_remote_node_address,
                            "backend_id": active_backend_id,
                            "selection_trace": dict(selection_trace or {}),
                        }
                    ]
                submit_result: Any = None
                last_remote_error: Exception | None = None
                for attempt_index, attempt in enumerate(attempt_plan):
                    attempt_node_address = _normalize_optional_non_empty(
                        attempt.get("node_address")
                    )
                    attempt_backend_id = _normalize_optional_non_empty(attempt.get("backend_id"))
                    if attempt_node_address is None or attempt_backend_id is None:
                        continue
                    request_payload = dict(base_request_payload)
                    raw_attempt_trace = attempt.get("selection_trace")
                    if isinstance(raw_attempt_trace, Mapping):
                        request_payload.setdefault(
                            "backend_selection",
                            dict(raw_attempt_trace),
                        )
                    elif selection_trace is not None:
                        request_payload.setdefault("backend_selection", dict(selection_trace))
                    try:
                        submit_result = router.submit_remote_backend_job(
                            node_address=attempt_node_address,
                            request=request_payload,
                            backend_id=attempt_backend_id,
                            required_tags=required_tags,
                            required_capabilities=required_capabilities,
                            preferred_backend_id=preferred_backend_id,
                            request_epoch=normalized_request_epoch,
                        )
                    except Exception as exc:
                        last_remote_error = exc
                        if _is_retryable_cluster_transport_error(exc) and attempt_index + 1 < len(
                            attempt_plan
                        ):
                            continue
                        self._record_scheduler_remote_blocked()
                        raise
                    active_backend_id = attempt_backend_id
                    active_remote_node_address = attempt_node_address
                    self._record_scheduler_remote_used()
                    break
                if submit_result is None:
                    if last_remote_error is not None:
                        raise last_remote_error
                    raise RuntimeError("cluster_backend_submit_attempts_exhausted")
            else:
                assert backend is not None
                request_payload = dict(base_request_payload)
                if selection_trace is not None:
                    request_payload.setdefault("backend_selection", dict(selection_trace))
                submit_result = backend.submit(request_payload)
            normalized_submit_result = (
                dict(submit_result)
                if isinstance(submit_result, Mapping)
                else {"submit_result": submit_result}
            )
            if active_remote_node_address is not None:
                raw_job_token = normalized_submit_result.get("job_token")
                job_token = str(raw_job_token or "").strip()
                if not job_token:
                    return normalized_submit_result
            else:
                raw_job_id = normalized_submit_result.get("job_id")
                job_id = str(raw_job_id or "").strip()
                if not job_id:
                    return normalized_submit_result

            started_at = asyncio.get_running_loop().time()
            remote_claim_started = active_remote_node_address is not None
            try:
                while True:
                    if active_remote_node_address is not None:
                        router = self._cluster_backend_router
                        if router is None:
                            raise RuntimeError("cluster_backend_router_not_configured")
                        poll_result = router.poll_remote_backend_job(
                            node_address=active_remote_node_address,
                            job_token=job_token,
                            auto_ack=False,
                        )
                    else:
                        assert backend is not None
                        poll_result = backend.poll(job_id)
                    if poll_result is not None:
                        normalized_poll_result = (
                            dict(poll_result)
                            if isinstance(poll_result, Mapping)
                            else {"result": poll_result}
                        )
                        if auto_ack:
                            if active_remote_node_address is not None:
                                router = self._cluster_backend_router
                                if router is None:
                                    raise RuntimeError("cluster_backend_router_not_configured")
                                router.ack_remote_backend_job(
                                    node_address=active_remote_node_address,
                                    job_token=job_token,
                                )
                            else:
                                assert backend is not None
                                backend.ack(job_id)
                        return normalized_poll_result

                    if resolved_timeout_seconds is not None:
                        elapsed = asyncio.get_running_loop().time() - started_at
                        if elapsed >= resolved_timeout_seconds:
                            if active_remote_node_address is not None:
                                router = self._cluster_backend_router
                                if router is not None:
                                    router.cancel_remote_backend_job(
                                        node_address=active_remote_node_address,
                                        job_token=job_token,
                                        reason="runtime_backend_job_timeout",
                                    )
                            else:
                                assert backend is not None
                                backend.cancel(job_id, reason="runtime_backend_job_timeout")
                            raise TimeoutError(
                                "backend_job_timeout:"
                                "backend_id="
                                f"{active_backend_id},timeout_seconds={resolved_timeout_seconds}",
                            )
                    await asyncio.sleep(resolved_poll_interval)
            finally:
                if remote_claim_started:
                    self._record_scheduler_remote_claim_end()

        return self.submit_user_coro(_run_backend_job())

    def register_collective_executor(
        self,
        *,
        mode: str,
        executor: CollectiveExecutor,
        path_tags: tuple[str, ...] = (),
    ) -> None:
        self._ensure_open()
        self._collective_executor_registry.register_executor(
            mode=mode,
            executor=executor,
            path_tags=path_tags,
        )

    def unregister_collective_executor(
        self,
        *,
        mode: str | None = None,
        path_tag: str | None = None,
    ) -> bool:
        self._ensure_open()
        return self._collective_executor_registry.unregister_executor(
            mode=mode,
            path_tag=path_tag,
        )

    def list_collective_executors(self) -> dict[str, Any]:
        self._ensure_open()
        return self._collective_executor_registry.snapshot()

    def scheduler_observability_snapshot(self) -> dict[str, Any]:
        with self._scheduler_observability_lock:
            snapshot = dict(self._scheduler_observability)
        governance_snapshot = getattr(self.governance, "snapshot", None)
        return {
            "selection_total": int(snapshot.get("selection_total", 0)),
            "fallback_count": int(snapshot.get("fallback_count", 0)),
            "scheduler_spillover": {
                "decision_count": int(snapshot.get("spillover_decision_count", 0)),
                "remote_used_count": int(snapshot.get("spillover_remote_used_count", 0)),
                "remote_blocked_count": int(snapshot.get("spillover_remote_blocked_count", 0)),
                "active_claims": int(snapshot.get("spillover_active_claims", 0)),
            },
            "governance": (governance_snapshot() if callable(governance_snapshot) else {}),
        }

    def list_shared_state_services(self) -> list[dict[str, Any]]:
        return self.shared_state_registry.observability_snapshot()

    def list_published_endpoints(self) -> list[dict[str, Any]]:
        return self.endpoint_registry.observability_snapshot()

    def resolve_shared_state_service(self, contract_id: str) -> Any:
        record = self.shared_state_registry.resolve_contract(contract_id)
        if record is None:
            raise ValueError(f"shared_state_descriptor_not_registered:{contract_id}")
        return record.service_object

    def release_flow_instance_endpoints(self, flow_instance_id: str) -> int:
        return self.endpoint_registry.release_flow_instance_endpoints(flow_instance_id)

    def release_shared_state_flow_claims(self, flow_instance_id: str) -> None:
        self.shared_state_registry.release_flow_instance_claims(flow_instance_id)

    def shutdown(self, *, wait: bool = True, cancel_futures: bool = False) -> None:
        if self._closed:
            return
        self._closed = True
        self._stop_idle_gc_loop()
        flow_process_execution = self._flow_process_execution
        flow_engine = getattr(flow_process_execution, "_flow_engine", None)
        close_flow_engine = getattr(flow_engine, "close", None)
        if callable(close_flow_engine):
            try:
                close_flow_engine()
            except Exception:
                pass
        callback_runtime = self._callback_runtime
        self._callback_runtime = None
        self.actor_api.set_callback_runtime(None)
        if callback_runtime is not None:
            callback_runtime.shutdown(
                wait=wait,
                cancel_futures=cancel_futures,
            )

        if self._owns_comm_hub:
            self.comm_hub.close()

        if self._owns_actor_api:
            self.actor_api.shutdown(wait=wait, cancel_futures=cancel_futures)
        elif self._owns_executor_lanes and self.executor_lanes is not None:
            self.executor_lanes.shutdown(wait=wait, cancel_futures=cancel_futures)

        if self._owns_runtime_loop:
            self._runtime_loop.stop(join=wait)
        if self._owns_user_loop:
            self._user_loop.stop(join=wait)

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("v1_runtime_host_closed")

    def _record_scheduler_selection(
        self,
        selection_trace: Mapping[str, Any] | None,
        *,
        remote_used: bool,
    ) -> None:
        with self._scheduler_observability_lock:
            self._scheduler_observability["selection_total"] = (
                int(self._scheduler_observability.get("selection_total", 0)) + 1
            )
            if remote_used:
                self._scheduler_observability["spillover_decision_count"] = (
                    int(self._scheduler_observability.get("spillover_decision_count", 0)) + 1
                )
            if not isinstance(selection_trace, Mapping):
                return
            if bool(selection_trace.get("epoch_fallback")) or bool(
                selection_trace.get("fallback_used")
            ):
                self._scheduler_observability["fallback_count"] = (
                    int(self._scheduler_observability.get("fallback_count", 0)) + 1
                )

    def _record_scheduler_remote_used(self) -> None:
        with self._scheduler_observability_lock:
            self._scheduler_observability["spillover_remote_used_count"] = (
                int(self._scheduler_observability.get("spillover_remote_used_count", 0)) + 1
            )
            self._scheduler_observability["spillover_active_claims"] = (
                int(self._scheduler_observability.get("spillover_active_claims", 0)) + 1
            )

    def _record_scheduler_remote_blocked(self) -> None:
        with self._scheduler_observability_lock:
            self._scheduler_observability["spillover_remote_blocked_count"] = (
                int(self._scheduler_observability.get("spillover_remote_blocked_count", 0)) + 1
            )

    def _record_scheduler_remote_claim_end(self) -> None:
        with self._scheduler_observability_lock:
            current = int(self._scheduler_observability.get("spillover_active_claims", 0))
            self._scheduler_observability["spillover_active_claims"] = max(0, current - 1)

    def _run_user_coro_blocking(self, coro: Coroutine[Any, Any, Any]) -> Any:
        fut = self.submit_user_coro(coro)
        return fut.result()

    def _ensure_actor_call_rpc_handler(self) -> None:
        if self.comm_hub.protocol_router.has_handler(
            plane=PLANE_RPC,
            op=OP_RPC_ACTOR_CALL,
        ):
            return
        register_actor_call_rpc_handler(
            actor_api=self.actor_api,
            comm_hub=self.comm_hub,
        )

    def _ensure_flow_program_pull_rpc_handler(self) -> None:
        if self.comm_hub.protocol_router.has_handler(
            plane=PLANE_RPC,
            op=OP_RPC_FLOW_PROGRAM_PULL,
        ):
            return
        resolve_flow_program = getattr(self.topic_api, "resolve_registered_flow_program", None)
        if not callable(resolve_flow_program):
            return
        register_flow_program_pull_handler(
            comm_hub=self.comm_hub,
            resolve_flow_program=(
                lambda flow_program_ref: resolve_flow_program(
                    flow_program_uri=flow_program_ref.program_uri,
                    flow_program_rev=flow_program_ref.program_rev,
                )
            ),
        )

    def _ensure_topic_forward_handlers(self) -> None:
        required_handlers = (
            (PLANE_DATA, OP_DATA_TOPIC_EVENT_FORWARD),
            (PLANE_CONTROL, OP_CONTROL_TOPIC_EVENT_CHAIN_DONE_FORWARD),
            (PLANE_CONTROL, OP_CONTROL_TOPIC_PRODUCER_DONE_FORWARD),
            (PLANE_CONTROL, OP_CONTROL_TOPIC_REQUEST_OUTCOME_FORWARD),
            (PLANE_CONTROL, OP_CONTROL_TOPIC_REQUEST_DONE_NOTIFY),
        )
        has_snapshot = [
            self.comm_hub.protocol_router.has_handler(plane=plane, op=op)
            for plane, op in required_handlers
        ]
        if all(has_snapshot):
            return
        if any(has_snapshot):
            raise RuntimeError("topic_forward_handler_registration_partial_state")
        register_topic_forward_handlers(
            topic_api=self.topic_api,
            comm_hub=self.comm_hub,
        )

    def _ensure_callback_runtime(self) -> None:
        if self._callback_runtime is not None:
            return
        callback_registry = CallbackRegistry(
            local_address=self.comm_hub.local_address,
            topic_api=self.topic_api,
            submit_user_coro=self.submit_user_coro,
            invoke_callback=self._invoke_actor_callback_target,
        )
        try:
            self.actor_api.resolve_local_actor(CALLBACK_REGISTRY_SERVICE_ACTOR_ID)
        except ValueError:
            self.actor_api.register_local_actor(
                CallbackRegistryServiceActor(callback_registry),
                actor_id=CALLBACK_REGISTRY_SERVICE_ACTOR_ID,
            )
        self._callback_runtime = ActorCallbackRuntime(
            local_address=self.comm_hub.local_address,
            actor_api=self.actor_api,
            comm_hub=self.comm_hub,
            callback_registry=callback_registry,
        )
        self.actor_api.set_callback_runtime(self._callback_runtime)

    async def _invoke_actor_callback_target(
        self, callback_target, event_record: dict[str, Any]
    ) -> None:
        request_ref_id = str(
            event_record.get("event_group_id") or event_record.get("topic_uri") or "callback",
        )
        await call_actor_via_comm(
            self.actor_api,
            self.comm_hub,
            callback_target,
            dict(event_record),
            request_ref_id=request_ref_id,
        )

    async def _dispatch_actor_event(self, event) -> dict[str, Any]:
        publish_result = self.topic_api.publish_or_forward_event(
            topic_uri=event.topic_uri,
            event_group_id=event.event_group_id,
            payload=event.payload,
            tags=event.tags,
            seq=event.seq,
            epoch=None,
        )
        mode = str(publish_result.get("mode") or "").strip()
        if mode == "local":
            return {
                "delivery_mode": "local",
                "topic_uri": event.topic_uri,
                "event_group_id": event.event_group_id,
                "seq": event.seq,
            }
        await send_topic_forward_intent_via_comm(
            comm_hub=self.comm_hub,
            intent=publish_result,
        )
        return {
            "delivery_mode": "forwarded",
            "forward_mode": mode,
            "topic_uri": event.topic_uri,
            "event_group_id": event.event_group_id,
            "seq": event.seq,
        }

    def _start_idle_gc_loop_if_enabled(self) -> None:
        interval_seconds = self._gc_idle_interval_seconds
        if interval_seconds is None:
            return
        self._gc_loop_future = self.submit_runtime_coro(
            self._run_idle_gc_loop(
                interval_seconds=interval_seconds,
                ttl_seconds=self._gc_idle_ttl_seconds,
            )
        )

    def _stop_idle_gc_loop(self) -> None:
        gc_future = self._gc_loop_future
        self._gc_loop_future = None
        if gc_future is None:
            return
        gc_future.cancel()
        try:
            gc_future.result(timeout=0.5)
        except (FutureCancelledError, FutureTimeoutError):
            return
        except Exception:
            return

    async def _run_idle_gc_loop(
        self,
        *,
        interval_seconds: float,
        ttl_seconds: float,
    ) -> None:
        while not self._closed:
            await asyncio.sleep(interval_seconds)
            if self._closed:
                return
            try:
                self.topic_api.gc_idle_runtime_state(
                    max_idle_seconds=ttl_seconds,
                )
            except Exception:
                # GC failures must not break runtime service loops.
                continue

    @staticmethod
    def _normalize_optional_interval_seconds(
        raw_value: float | None,
        *,
        field_name: str,
    ) -> float | None:
        if raw_value is None:
            return None
        value = float(raw_value)
        if value <= 0.0:
            raise ValueError(f"{field_name} must be > 0 when provided.")
        return value

    @staticmethod
    def _normalize_non_negative_seconds(
        raw_value: float,
        *,
        field_name: str,
    ) -> float:
        value = float(raw_value)
        if value < 0.0:
            raise ValueError(f"{field_name} must be >= 0.")
        return value


def _normalize_optional_non_empty(raw_value: Any) -> str | None:
    if raw_value is None:
        return None
    normalized = str(raw_value).strip()
    if not normalized:
        return None
    return normalized


def _normalize_optional_epoch(raw_value: Any) -> int | None:
    if raw_value is None:
        return None
    if isinstance(raw_value, bool):
        return int(raw_value)
    if isinstance(raw_value, int):
        return raw_value
    normalized = str(raw_value).strip()
    if not normalized:
        return None
    try:
        return int(normalized)
    except Exception:
        return None


def _resolve_request_epoch_from_request(request: Mapping[str, Any]) -> int | None:
    request_candidates = (
        request.get("request_epoch"),
        request.get("epoch"),
        request.get("route_epoch"),
        request.get("topic_epoch"),
    )
    for candidate in request_candidates:
        normalized = _normalize_optional_epoch(candidate)
        if normalized is not None:
            return normalized

    metadata = request.get("metadata")
    if isinstance(metadata, Mapping):
        metadata_candidates = (
            metadata.get("request_epoch"),
            metadata.get("epoch"),
            metadata.get("route_epoch"),
            metadata.get("topic_epoch"),
        )
        for candidate in metadata_candidates:
            normalized = _normalize_optional_epoch(candidate)
            if normalized is not None:
                return normalized
    return None


def _select_backend_record_from_candidates(
    *,
    candidates: list[Mapping[str, Any]],
    preferred_backend_id: str | None,
    request_epoch: int | None,
    local_node_address: str | None,
) -> dict[str, Any]:
    normalized_preferred_backend_id = _normalize_optional_non_empty(preferred_backend_id)
    normalized_request_epoch = _normalize_optional_epoch(request_epoch)
    normalized_local_node_address = _normalize_optional_non_empty(local_node_address)

    scored: list[dict[str, Any]] = []
    for record in candidates:
        backend_id = _normalize_optional_non_empty(record.get("backend_id"))
        if backend_id is None:
            continue
        raw_metrics = record.get("metrics")
        metrics = dict(raw_metrics) if isinstance(raw_metrics, Mapping) else {}
        backend_epoch = _normalize_optional_epoch(metrics.get("epoch"))
        epoch_policy = _resolve_epoch_policy(
            backend_epoch=backend_epoch,
            request_epoch=normalized_request_epoch,
        )
        queue_depth = _coerce_float(metrics.get("queue_depth"), default=0.0)
        queue_capacity = max(1.0, _coerce_float(metrics.get("queue_capacity"), default=1.0))
        inflight = _coerce_float(metrics.get("inflight"), default=0.0)
        node_address = _normalize_optional_non_empty(record.get("node_address"))
        scored.append(
            {
                "record": dict(record),
                "backend_id": backend_id,
                "node_address": node_address,
                "healthy": _coerce_bool(metrics.get("healthy"), default=True),
                "schedulable": _coerce_bool(metrics.get("schedulable"), default=True),
                "epoch_policy": epoch_policy,
                "queue_pressure": queue_depth / queue_capacity,
                "inflight": inflight,
                "preferred_hit": (
                    normalized_preferred_backend_id is not None
                    and backend_id == normalized_preferred_backend_id
                ),
                "local_hit": (
                    normalized_local_node_address is not None
                    and node_address == normalized_local_node_address
                ),
            }
        )
    if not scored:
        raise RuntimeError("backend_container_snapshot_invalid:missing_backend_id")

    schedulable = [item for item in scored if bool(item["healthy"]) and bool(item["schedulable"])]
    if not schedulable:
        raise RuntimeError("backend_container_no_schedulable_candidate")

    if normalized_request_epoch is None:
        epoch_pool = schedulable
        selection_reason = "healthy_schedulable"
    else:
        exact = [item for item in schedulable if item["epoch_policy"] == "match"]
        unknown = [item for item in schedulable if item["epoch_policy"] == "unknown"]
        mismatch = [item for item in schedulable if item["epoch_policy"] == "mismatch"]
        if exact:
            epoch_pool = exact
            selection_reason = "request_epoch_match"
        elif unknown:
            epoch_pool = unknown
            selection_reason = "request_epoch_unknown_fallback"
        else:
            epoch_pool = mismatch
            selection_reason = "request_epoch_mismatch_fallback"

    epoch_pool.sort(
        key=lambda item: (
            0 if bool(item["preferred_hit"]) else 1,
            0 if bool(item["local_hit"]) else 1,
            float(item["queue_pressure"]),
            float(item["inflight"]),
            str(item["backend_id"]),
            str(item.get("node_address") or ""),
        )
    )
    selected = epoch_pool[0]
    selected_record = dict(selected["record"])
    selected_record["request_epoch"] = normalized_request_epoch
    selected_record["matched_request_epoch"] = selected["epoch_policy"] == "match"
    selected_record["epoch_policy"] = selected["epoch_policy"]
    selected_record["epoch_fallback"] = selected["epoch_policy"] != "match"
    selected_record["preferred_hit"] = bool(selected["preferred_hit"])
    selected_record["selection_reason"] = selection_reason
    selected_record["selection_reason_codes"] = _build_selection_reason_codes(
        selected=selected,
        selection_reason=selection_reason,
    )
    selected_record["selected_backend_state"] = _summarize_backend_selection_item(
        selected,
        selected=True,
    )
    selected_record["candidate_backend_states"] = [
        _summarize_backend_selection_item(item, selected=(item is selected)) for item in epoch_pool
    ]
    selected_record["excluded_backend_states"] = [
        _summarize_backend_selection_item(item, exclusion_reason=exclusion_reason)
        for item in scored
        if (
            exclusion_reason := _determine_backend_selection_exclusion_reason(
                item=item,
                selected=selected,
                epoch_pool=epoch_pool,
            )
        )
        is not None
    ]
    return selected_record


def _build_backend_selection_trace(
    *,
    selection: Mapping[str, Any],
    required_tags: Mapping[str, str] | None,
    required_capabilities: Mapping[str, Any] | None,
) -> dict[str, Any]:
    trace = {
        "backend_id": selection.get("backend_id"),
        "node_id": selection.get("node_id"),
        "node_address": selection.get("node_address"),
        "request_epoch": selection.get("request_epoch"),
        "matched_request_epoch": selection.get("matched_request_epoch"),
        "epoch_policy": selection.get("epoch_policy"),
        "epoch_fallback": selection.get("epoch_fallback"),
        "selection_reason": selection.get("selection_reason"),
        "required_tags": dict(required_tags or {}),
        "required_capabilities": _normalize_backend_capability_mapping(required_capabilities),
    }
    reason_codes = selection.get("selection_reason_codes")
    if isinstance(reason_codes, (list, tuple)):
        trace["selection_reason_codes"] = [
            code for code in (_normalize_optional_non_empty(item) for item in reason_codes) if code
        ]
    selected_backend_state = selection.get("selected_backend_state")
    if isinstance(selected_backend_state, Mapping):
        trace["selected_backend_state"] = _copy_selection_trace_value(selected_backend_state)
    for field_name in ("candidate_backend_states", "excluded_backend_states"):
        raw_value = selection.get(field_name)
        if isinstance(raw_value, (list, tuple)):
            trace[field_name] = [
                _copy_selection_trace_value(item) for item in raw_value if isinstance(item, Mapping)
            ]
    return trace


def _build_selection_reason_codes(
    *, selected: Mapping[str, Any], selection_reason: str
) -> list[str]:
    codes = [selection_reason]
    epoch_policy = _normalize_optional_non_empty(selected.get("epoch_policy"))
    if epoch_policy is not None:
        codes.append(f"epoch_policy_{epoch_policy}")
    if bool(selected.get("preferred_hit")):
        codes.append("preferred_backend")
    if bool(selected.get("local_hit")):
        codes.append("local_node")
    return codes


def _determine_backend_selection_exclusion_reason(
    *,
    item: Mapping[str, Any],
    selected: Mapping[str, Any],
    epoch_pool: list[Mapping[str, Any]],
) -> str | None:
    if not bool(item.get("healthy")):
        return "unhealthy"
    if not bool(item.get("schedulable")):
        return "unschedulable"
    if item not in epoch_pool:
        epoch_policy = _normalize_optional_non_empty(item.get("epoch_policy")) or "unknown"
        return f"epoch_policy_{epoch_policy}_filtered"
    if item is selected:
        return None
    return "ranked_lower"


def _summarize_backend_selection_item(
    item: Mapping[str, Any],
    *,
    selected: bool = False,
    exclusion_reason: str | None = None,
) -> dict[str, Any]:
    raw_record = item.get("record")
    record = dict(raw_record) if isinstance(raw_record, Mapping) else {}
    raw_metrics = record.get("metrics")
    metrics = dict(raw_metrics) if isinstance(raw_metrics, Mapping) else {}
    raw_metadata = record.get("metadata")
    metadata = dict(raw_metadata) if isinstance(raw_metadata, Mapping) else {}
    runtime_metrics = metadata.get("runtime_metrics")

    summary: dict[str, Any] = {
        "backend_id": _normalize_optional_non_empty(item.get("backend_id")),
        "node_id": _normalize_optional_non_empty(record.get("node_id")),
        "node_address": _normalize_optional_non_empty(
            item.get("node_address") or record.get("node_address")
        ),
        "healthy": bool(item.get("healthy")),
        "schedulable": bool(item.get("schedulable")),
        "selected": bool(selected),
        "epoch": _normalize_optional_epoch(metrics.get("epoch")),
        "epoch_policy": _normalize_optional_non_empty(item.get("epoch_policy")),
        "queue_depth": _coerce_float(metrics.get("queue_depth"), default=0.0),
        "queue_capacity": _coerce_float(metrics.get("queue_capacity"), default=1.0),
        "inflight": _coerce_float(metrics.get("inflight"), default=0.0),
        "queue_pressure": round(_coerce_float(item.get("queue_pressure"), default=0.0), 4),
        "preferred_hit": bool(item.get("preferred_hit")),
        "local_hit": bool(item.get("local_hit")),
        "tags": _normalize_string_mapping(
            record.get("tags") if isinstance(record.get("tags"), Mapping) else None
        ),
        "capabilities": _normalize_backend_capability_mapping(
            record.get("capabilities") if isinstance(record.get("capabilities"), Mapping) else None
        ),
    }

    for field_name in ("accelerator_type", "model_family", "precision"):
        field_value = _normalize_optional_non_empty(metadata.get(field_name))
        if field_value is not None:
            summary[field_name] = field_value

    resident_models = metadata.get("resident_models")
    if isinstance(resident_models, (list, tuple)):
        summary["resident_models"] = [
            model
            for model in (_normalize_optional_non_empty(item) for item in resident_models)
            if model
        ]

    parallelism = metadata.get("parallelism")
    if isinstance(parallelism, Mapping):
        summary["parallelism"] = _copy_selection_trace_value(parallelism)

    if isinstance(runtime_metrics, Mapping):
        summary["runtime_metrics"] = _copy_selection_trace_value(runtime_metrics)

    if exclusion_reason is not None:
        summary["exclusion_reason"] = exclusion_reason
    return summary


def _copy_selection_trace_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _copy_selection_trace_value(nested) for key, nested in value.items()}
    if isinstance(value, (list, tuple)):
        return [_copy_selection_trace_value(item) for item in value]
    return value


def _select_cluster_failover_record(
    *,
    candidates: list[Mapping[str, Any]],
    failed_node_address: str,
    preferred_backend_id: str | None,
    request_epoch: int | None,
    local_node_address: str | None,
) -> dict[str, Any] | None:
    normalized_failed_node_address = _normalize_optional_non_empty(failed_node_address)
    if normalized_failed_node_address is None:
        return None
    filtered_candidates: list[Mapping[str, Any]] = []
    for record in candidates:
        node_address = _normalize_optional_non_empty(record.get("node_address"))
        if node_address == normalized_failed_node_address:
            continue
        filtered_candidates.append(record)
    if not filtered_candidates:
        return None
    try:
        return _select_backend_record_from_candidates(
            candidates=filtered_candidates,
            preferred_backend_id=preferred_backend_id,
            request_epoch=request_epoch,
            local_node_address=local_node_address,
        )
    except RuntimeError:
        return None


def _find_backend_record_in_candidates(
    *,
    candidates: list[Mapping[str, Any]],
    backend_id: str,
) -> dict[str, Any] | None:
    normalized_backend_id = _normalize_optional_non_empty(backend_id)
    if normalized_backend_id is None:
        return None
    for record in candidates:
        candidate_backend_id = _normalize_optional_non_empty(record.get("backend_id"))
        if candidate_backend_id != normalized_backend_id:
            continue
        return dict(record)
    return None


def _record_matches_backend_requirements(
    record: Mapping[str, Any],
    *,
    required_tags: Mapping[str, str] | None,
    required_capabilities: Mapping[str, Any] | None,
) -> bool:
    normalized_required_tags = _normalize_string_mapping(required_tags)
    if normalized_required_tags:
        raw_tags = record.get("tags")
        if not isinstance(raw_tags, Mapping):
            return False
        for key, required_value in normalized_required_tags.items():
            actual = _normalize_optional_non_empty(raw_tags.get(key))
            if actual != required_value:
                return False
    normalized_required_capabilities = _normalize_backend_capability_mapping(required_capabilities)
    if not normalized_required_capabilities:
        return True
    raw_capabilities = record.get("capabilities")
    if not isinstance(raw_capabilities, Mapping):
        return False
    return _backend_capabilities_match(
        candidate_capabilities=raw_capabilities,
        required_capabilities=normalized_required_capabilities,
    )


def _is_retryable_cluster_transport_error(exc: Exception) -> bool:
    return "http_post_failed:" in str(exc)


def _resolve_epoch_policy(*, backend_epoch: int | None, request_epoch: int | None) -> str:
    if request_epoch is None:
        return "not_requested"
    if backend_epoch is None:
        return "unknown"
    if backend_epoch == request_epoch:
        return "match"
    return "mismatch"


def _coerce_bool(raw_value: Any, *, default: bool) -> bool:
    if raw_value is None:
        return bool(default)
    if isinstance(raw_value, bool):
        return raw_value
    if isinstance(raw_value, (int, float)):
        return bool(raw_value)
    normalized = str(raw_value).strip().lower()
    if not normalized:
        return bool(default)
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    return bool(default)


def _coerce_float(raw_value: Any, *, default: float) -> float:
    if raw_value is None:
        return float(default)
    try:
        return float(raw_value)
    except Exception:
        return float(default)


def _format_required_tags(required_tags: Mapping[str, str] | None) -> str:
    normalized = _normalize_string_mapping(required_tags)
    if not normalized:
        return "{}"
    parts = [f"{key}={normalized[key]}" for key in sorted(normalized.keys())]
    return "{" + ",".join(parts) + "}"


def _format_required_capabilities(required_capabilities: Mapping[str, Any] | None) -> str:
    normalized = _normalize_backend_capability_mapping(required_capabilities)
    if not normalized:
        return "{}"
    parts = [f"{key}={normalized[key]!r}" for key in sorted(normalized.keys())]
    return "{" + ",".join(parts) + "}"


def _normalize_string_mapping(raw_value: Mapping[str, str] | None) -> dict[str, str]:
    if not isinstance(raw_value, Mapping):
        return {}
    normalized: dict[str, str] = {}
    for raw_key, raw_item in raw_value.items():
        key = _normalize_optional_non_empty(raw_key)
        value = _normalize_optional_non_empty(raw_item)
        if key is None or value is None:
            continue
        normalized[key] = value
    return normalized


def _normalize_backend_capability_mapping(raw_value: Mapping[str, Any] | None) -> dict[str, Any]:
    if not isinstance(raw_value, Mapping):
        return {}
    normalized: dict[str, Any] = {}
    for raw_key, raw_item in raw_value.items():
        key = _normalize_optional_non_empty(raw_key)
        if key is None:
            continue
        normalized[key] = _normalize_backend_capability_value(raw_item)
    return normalized


def _normalize_backend_capability_value(raw_value: Any) -> Any:
    if isinstance(raw_value, Mapping):
        return {
            str(key): _normalize_backend_capability_value(value)
            for key, value in raw_value.items()
            if _normalize_optional_non_empty(key) is not None
        }
    if isinstance(raw_value, (list, tuple, set, frozenset)):
        return [_normalize_backend_capability_value(item) for item in raw_value]
    return raw_value


def _backend_capabilities_match(
    *,
    candidate_capabilities: Mapping[str, Any],
    required_capabilities: Mapping[str, Any],
) -> bool:
    for key, required_value in required_capabilities.items():
        if key not in candidate_capabilities:
            return False
        if not _backend_capability_value_matches(candidate_capabilities[key], required_value):
            return False
    return True


def _backend_capability_value_matches(candidate_value: Any, required_value: Any) -> bool:
    if isinstance(required_value, Mapping):
        if not isinstance(candidate_value, Mapping):
            return False
        for key, nested_required in required_value.items():
            if key not in candidate_value:
                return False
            if not _backend_capability_value_matches(candidate_value[key], nested_required):
                return False
        return True
    if isinstance(required_value, (list, tuple, set, frozenset)):
        required_items = list(required_value)
        if not required_items:
            return True
        if isinstance(candidate_value, (list, tuple, set, frozenset)):
            candidate_items = list(candidate_value)
            return all(
                any(
                    _backend_capability_value_matches(candidate_item, required_item)
                    for candidate_item in candidate_items
                )
                for required_item in required_items
            )
        if len(required_items) != 1:
            return False
        return _backend_capability_value_matches(candidate_value, required_items[0])
    if isinstance(candidate_value, (list, tuple, set, frozenset)):
        return any(
            _backend_capability_value_matches(candidate_item, required_value)
            for candidate_item in candidate_value
        )
    candidate_text = _normalize_optional_scalar_text(candidate_value)
    required_text = _normalize_optional_scalar_text(required_value)
    if candidate_text is not None or required_text is not None:
        return candidate_text == required_text
    return candidate_value == required_value


def _normalize_optional_scalar_text(raw_value: Any) -> str | None:
    if raw_value is None or isinstance(raw_value, bool):
        return None
    normalized = str(raw_value).strip()
    if not normalized:
        return None
    return normalized


__all__ = ["V1RuntimeHost"]
