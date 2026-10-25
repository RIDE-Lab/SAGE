from __future__ import annotations

import os
import time
from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class PlacementDecision:
    target_node: str | None = None
    delay: float = 0.0
    immediate: bool = True
    reason: str = ""
    resource: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def immediate_default(cls, *, reason: str) -> PlacementDecision:
        return cls(target_node=None, delay=0.0, immediate=True, reason=reason)


@dataclass(slots=True)
class NodeResources:
    node_id: str
    hostname: str
    available_cpu: float
    available_gpu: float
    available_memory: int
    task_count: int = 0
    alive: bool = True

    def can_fit(
        self,
        cpu_required: float = 0.0,
        gpu_required: float = 0.0,
        memory_required: int = 0,
    ) -> bool:
        return (
            self.available_cpu >= cpu_required
            and self.available_gpu >= gpu_required
            and self.available_memory >= memory_required
        )


class NodeSelector:
    """Small in-tree node selector for local or optional distributed scheduling demos."""

    def __init__(self, enable_tracking: bool = True) -> None:
        self.enable_tracking = enable_tracking
        self.node_task_count: dict[str, int] = {}

    def get_all_nodes(self) -> list[NodeResources]:
        hostname = os.environ.get("SAGE_NODE_NAME") or os.uname().nodename
        cpu_count = float(os.cpu_count() or 1)
        memory_bytes = int(os.environ.get("SAGE_AVAILABLE_MEMORY", 8 * 1024**3))
        node = NodeResources(
            node_id=hostname,
            hostname=hostname,
            available_cpu=cpu_count,
            available_gpu=float(os.environ.get("SAGE_AVAILABLE_GPU", 0)),
            available_memory=memory_bytes,
            task_count=self.node_task_count.get(hostname, 0),
        )
        return [node]

    def get_node(self, node_id: str) -> NodeResources | None:
        for node in self.get_all_nodes():
            if node.node_id == node_id:
                return node
        return None

    def select_best_node(
        self,
        cpu_required: float = 0.0,
        gpu_required: float = 0.0,
        memory_required: int = 0,
        custom_resources: dict[str, float] | None = None,
        strategy: str = "balanced",
        exclude_nodes: list[str] | None = None,
    ) -> str | None:
        del custom_resources
        exclude = set(exclude_nodes or [])
        nodes = [n for n in self.get_all_nodes() if n.node_id not in exclude]
        candidates = [
            n for n in nodes if n.can_fit(cpu_required, gpu_required, memory_required) and n.alive
        ]
        if not candidates:
            return None
        if strategy == "spread":
            candidates.sort(key=lambda n: (n.task_count, n.hostname))
        else:
            candidates.sort(key=lambda n: (n.task_count, -n.available_cpu, n.hostname))
        selected = candidates[0]
        if self.enable_tracking:
            self.node_task_count[selected.node_id] = (
                self.node_task_count.get(selected.node_id, 0) + 1
            )
        return selected.node_id


class BaseScheduler(ABC):
    def __init__(self) -> None:
        self.scheduled_count = 0
        self.decision_history: list[PlacementDecision] = []

    @abstractmethod
    def make_decision(self, task_node: Any) -> PlacementDecision:
        raise NotImplementedError

    def make_service_decision(self, service_node: Any) -> PlacementDecision:
        return PlacementDecision.immediate_default(
            reason=f"{self.__class__.__name__} service scheduling"
        )

    def schedule_task(self, task_node: Any, runtime_ctx: Any = None) -> Any:
        decision = self.make_decision(task_node)
        if decision.delay > 0:
            time.sleep(decision.delay)
        ctx = runtime_ctx if runtime_ctx is not None else getattr(task_node, "ctx", None)
        return task_node.task_factory.create_task(task_node.name, ctx)

    def schedule_service(self, service_node: Any, runtime_ctx: Any = None) -> Any:
        decision = self.make_service_decision(service_node)
        if decision.delay > 0:
            time.sleep(decision.delay)
        ctx = runtime_ctx if runtime_ctx is not None else getattr(service_node, "ctx", None)
        return service_node.service_task_factory.create_service_task(ctx)

    def task_completed(self, task_name: str) -> None:
        return None

    def get_metrics(self) -> dict[str, Any]:
        return {
            "scheduler_type": self.__class__.__name__,
            "total_scheduled": self.scheduled_count,
            "decisions": len(self.decision_history),
        }

    def shutdown(self) -> None:
        self.decision_history.clear()


class FIFOScheduler(BaseScheduler):
    def __init__(self, platform: str = "local") -> None:
        super().__init__()
        self.platform = platform
        self.total_latency = 0.0

    def make_decision(self, task_node: Any) -> PlacementDecision:
        start_time = time.time()
        self.scheduled_count += 1
        decision = PlacementDecision.immediate_default(
            reason=f"FIFO order: #{self.scheduled_count}"
        )
        self.decision_history.append(decision)
        self.total_latency += time.time() - start_time
        return decision

    def get_metrics(self) -> dict[str, Any]:
        avg_latency = self.total_latency / self.scheduled_count if self.scheduled_count else 0.0
        return {
            "scheduler_type": "FIFO",
            "total_scheduled": self.scheduled_count,
            "avg_latency_ms": avg_latency * 1000,
            "decisions": len(self.decision_history),
            "platform": self.platform,
        }


class LoadAwareScheduler(BaseScheduler):
    def __init__(
        self,
        platform: str = "local",
        max_concurrent: int = 10,
        strategy: str = "balanced",
    ) -> None:
        super().__init__()
        self.platform = platform
        self.max_concurrent = max_concurrent
        self.strategy = strategy
        self.total_latency = 0.0
        self.active_tasks = 0
        self.resource_utilization: list[float] = []

    def _extract_resource_request(self, node: Any) -> dict[str, Any]:
        transformation = getattr(node, "transformation", None)
        memory_required = getattr(transformation, "memory_required", None)
        return {
            "cpu": float(getattr(transformation, "cpu_required", 1.0) or 1.0),
            "gpu": float(getattr(transformation, "gpu_required", 0.0) or 0.0),
            "memory": memory_required,
            "custom_resources": dict(getattr(transformation, "custom_resources", {}) or {}),
        }

    def make_decision(self, task_node: Any) -> PlacementDecision:
        start_time = time.time()
        delay = 0.0
        if self.active_tasks >= self.max_concurrent:
            overflow = self.active_tasks - self.max_concurrent + 1
            delay = min(0.01 * overflow, 1.0)

        resource = self._extract_resource_request(task_node)
        self.active_tasks += 1
        self.scheduled_count += 1
        utilization = self.active_tasks / self.max_concurrent if self.max_concurrent else 0.0
        self.resource_utilization.append(utilization)

        decision = PlacementDecision(
            target_node=None,
            delay=delay,
            immediate=(delay == 0.0),
            reason=(
                f"LoadAware: task={getattr(task_node, 'name', 'unknown')}, "
                f"active={self.active_tasks}, strategy={self.strategy}"
            ),
            resource=resource,
        )
        self.decision_history.append(decision)
        self.total_latency += time.time() - start_time
        return decision

    def task_completed(self, task_name: str) -> None:
        self.active_tasks = max(0, self.active_tasks - 1)

    def get_metrics(self) -> dict[str, Any]:
        avg_latency = self.total_latency / self.scheduled_count if self.scheduled_count else 0.0
        avg_utilization = (
            sum(self.resource_utilization) / len(self.resource_utilization)
            if self.resource_utilization
            else 0.0
        )
        return {
            "scheduler_type": "LoadAware",
            "total_scheduled": self.scheduled_count,
            "avg_latency_ms": avg_latency * 1000,
            "active_tasks": self.active_tasks,
            "max_concurrent": self.max_concurrent,
            "avg_resource_utilization": avg_utilization,
            "decisions": len(self.decision_history),
            "platform": self.platform,
            "strategy": self.strategy,
        }

    def shutdown(self) -> None:
        super().shutdown()
        self.resource_utilization.clear()


def _has_scheduler_interface(value: Any) -> bool:
    return callable(getattr(value, "make_decision", None))


def _normalize_scheduler_name(raw_value: Any) -> str:
    normalized = str(raw_value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if not normalized:
        raise ValueError("scheduler name must be a non-empty string.")
    return normalized


def _coerce_positive_int(raw_value: Any, *, field_name: str) -> int:
    try:
        value = int(raw_value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be a positive integer.") from exc
    if value <= 0:
        raise ValueError(f"{field_name} must be a positive integer.")
    return value


def _resolve_scheduler_from_mapping(*, scheduler: Mapping[str, Any], platform: str):
    scheduler_type = (
        scheduler.get("type")
        or scheduler.get("name")
        or scheduler.get("kind")
    )
    if scheduler_type is None:
        raise ValueError(
            "scheduler mapping must include one of: 'type', 'name', or 'kind'."
        )

    scheduler_name = _normalize_scheduler_name(scheduler_type)
    if scheduler_name == "fifo":
        return FIFOScheduler(platform=platform)
    if scheduler_name in {"load_aware", "loadaware"}:
        strategy = str(scheduler.get("strategy") or "balanced").strip() or "balanced"
        max_concurrent = _coerce_positive_int(
            scheduler.get("max_concurrent", 10),
            field_name="scheduler.max_concurrent",
        )
        return LoadAwareScheduler(
            platform=platform,
            max_concurrent=max_concurrent,
            strategy=strategy,
        )

    raise ValueError(
        "Unknown scheduler type: "
        f"{scheduler_type}. Available options: 'fifo', 'load_aware'"
    )


def create_default_scheduler(*, platform: str):
    return FIFOScheduler(platform=platform)


def resolve_scheduler(*, scheduler: Any, platform: str):
    if scheduler is None:
        return create_default_scheduler(platform=platform)

    if isinstance(scheduler, str):
        scheduler_lower = _normalize_scheduler_name(scheduler)
        if scheduler_lower == "fifo":
            return FIFOScheduler(platform=platform)
        if scheduler_lower in {"load_aware", "loadaware"}:
            return LoadAwareScheduler(platform=platform)
        raise ValueError(
            f"Unknown scheduler type: {scheduler}. Available options: 'fifo', 'load_aware'"
        )

    if isinstance(scheduler, Mapping):
        return _resolve_scheduler_from_mapping(scheduler=scheduler, platform=platform)

    if _has_scheduler_interface(scheduler):
        return scheduler

    raise TypeError(
        "scheduler must be None, str, mapping, or an object implementing make_decision(), "
        f"got {type(scheduler)}"
    )


__all__ = [
    "BaseScheduler",
    "FIFOScheduler",
    "LoadAwareScheduler",
    "NodeResources",
    "NodeSelector",
    "PlacementDecision",
    "create_default_scheduler",
    "resolve_scheduler",
]
