from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from os import PathLike
from typing import Any, Literal

from sage.runtime.flownet.runtime import (
    V1CommHub,
    V1RuntimeHost,
    V1TransportBackend,
    build_v1_transport_backend,
)

BootstrapMode = Literal["local", "connect_only"]

_DEFAULT_LOCAL_PORT = 9920
_DEFAULT_NODE_PORT = 8787
_DEFAULT_LOCAL_ADDRESS = "127.0.0.1:9920"


@dataclass(frozen=True)
class V1BootstrapHandle:
    """
    Minimal bootstrap artifact for B2 PoC.

    This intentionally preserves only normalized bootstrap intent and the
    runtime host object. Actual cluster attach handshake is not part of this
    slice and is left for follow-up implementation.
    """

    runtime_host: V1RuntimeHost
    node_mode: BootstrapMode
    local_address: str
    attached_node_address: str | None = None
    node_id: str | None = None
    node_threads: int | None = None
    connect_timeout: float | None = None
    status: str = "poc_stub"
    node_runtime: Any | None = None
    node_control_call: Callable[..., Any] | None = None

    def shutdown(self, *, wait: bool = True, cancel_futures: bool = False) -> None:
        node_runtime = self.node_runtime
        if node_runtime is not None:
            stop = getattr(node_runtime, "stop", None)
            if callable(stop):
                try:
                    stop()
                except Exception:
                    pass
        self.runtime_host.shutdown(wait=wait, cancel_futures=cancel_futures)


def bootstrap_runtime(
    *,
    local_address: str = _DEFAULT_LOCAL_ADDRESS,
    node_mode: BootstrapMode | str = "local",
    node: str | None = None,
    node_address: str | None = None,
    node_id: str | None = None,
    node_threads: int | None = None,
    connect_timeout: float | None = None,
    comm_hub: V1CommHub | None = None,
    comm_transport: V1TransportBackend | None = None,
    comm_transport_mode: str | None = None,
    topic_coordinator_causal_cut_path: str | PathLike[str] | None = None,
) -> V1BootstrapHandle:
    """
    v1 bootstrap entrypoint draft.

    `node_mode="local"` creates a standalone runtime host.
    `node_mode="connect_only"` creates a local io/control host plus target-node
    metadata for later node-control integration.
    """

    if node is not None and node_address is not None:
        raise ValueError("Pass either `node` or `node_address`, not both.")

    normalized_mode = _normalize_mode(node_mode)
    normalized_local_address = _normalize_address(
        local_address,
        field_name="local_address",
        default_port=_DEFAULT_LOCAL_PORT,
    )
    normalized_node_address = _normalize_optional_address(
        node if node is not None else node_address,
        field_name="node_address",
        default_port=_DEFAULT_NODE_PORT,
    )
    normalized_node_id = _normalize_optional_non_empty(node_id, field_name="node_id")
    normalized_node_threads = _normalize_optional_positive_int(
        node_threads,
        field_name="node_threads",
    )
    normalized_connect_timeout = _normalize_optional_non_negative_float(
        connect_timeout,
        field_name="connect_timeout",
    )

    if normalized_mode == "connect_only" and normalized_node_address is None:
        raise ValueError("connect_only mode requires `node` or `node_address`.")

    if comm_transport is not None and comm_transport_mode is not None:
        raise ValueError("comm_transport and comm_transport_mode cannot both be provided.")

    resolved_comm_transport = comm_transport
    if resolved_comm_transport is None and comm_transport_mode is not None:
        resolved_comm_transport = build_v1_transport_backend(
            transport_mode=comm_transport_mode,
        )

    runtime_host = V1RuntimeHost(
        local_address=normalized_local_address,
        comm_hub=comm_hub,
        comm_transport=resolved_comm_transport,
        topic_coordinator_causal_cut_path=topic_coordinator_causal_cut_path,
    )
    return V1BootstrapHandle(
        runtime_host=runtime_host,
        node_mode=normalized_mode,
        local_address=normalized_local_address,
        attached_node_address=normalized_node_address,
        node_id=normalized_node_id,
        node_threads=normalized_node_threads,
        connect_timeout=normalized_connect_timeout,
    )


def bootstrap_node_runtime(
    *,
    node_id: str,
    bind_address: str,
    seeds: Iterable[str] | str | None = None,
    metadata: Mapping[str, Any] | None = None,
    join_timeout: float = 3.0,
    gossip_interval: float = 1.0,
    offline_timeout: float = 8.0,
    http_timeout: float = 2.0,
    state_backend: str = "memory",
    state_sqlite_path: str | None = None,
    local_address: str | None = None,
    comm_hub: V1CommHub | None = None,
    comm_transport: V1TransportBackend | None = None,
    comm_transport_mode: str | None = None,
) -> V1BootstrapHandle:
    """
    Bootstrap a node runtime with seed join + membership gossip.

    This is the minimal 15B entrypoint for process/daemon mode.
    """

    from sage.runtime.flownet.client.node_runtime import (
        V1NodeRuntimeService,
    )

    normalized_node_id = _normalize_non_empty(node_id, field_name="node_id")
    normalized_bind_address = _normalize_address(
        bind_address,
        field_name="bind_address",
        default_port=_DEFAULT_NODE_PORT,
    )
    resolved_local_address = _normalize_address(
        local_address if local_address is not None else normalized_bind_address,
        field_name="local_address",
        default_port=_DEFAULT_NODE_PORT,
    )
    normalized_join_timeout = _normalize_positive_float(join_timeout, field_name="join_timeout")
    normalized_gossip_interval = _normalize_positive_float(
        gossip_interval,
        field_name="gossip_interval",
    )
    normalized_offline_timeout = _normalize_positive_float(
        offline_timeout,
        field_name="offline_timeout",
    )
    normalized_http_timeout = _normalize_positive_float(http_timeout, field_name="http_timeout")
    normalized_state_backend = str(state_backend or "memory").strip().lower() or "memory"
    normalized_state_sqlite_path = _normalize_optional_non_empty(
        state_sqlite_path,
        field_name="state_sqlite_path",
    )

    raw_seed_values: list[str] = []
    if seeds is None:
        raw_seed_values = []
    elif isinstance(seeds, str):
        raw_seed_values = [seeds]
    else:
        raw_seed_values = [str(item) for item in seeds]

    base_handle = bootstrap_runtime(
        local_address=resolved_local_address,
        node_mode="local",
        node_id=normalized_node_id,
        connect_timeout=normalized_join_timeout,
        comm_hub=comm_hub,
        comm_transport=comm_transport,
        comm_transport_mode=comm_transport_mode,
    )

    node_runtime = V1NodeRuntimeService(
        runtime_host=base_handle.runtime_host,
        node_id=normalized_node_id,
        node_address=normalized_bind_address,
        seed_addresses=raw_seed_values,
        metadata=metadata,
        join_timeout_s=normalized_join_timeout,
        gossip_interval_s=normalized_gossip_interval,
        offline_timeout_s=normalized_offline_timeout,
        http_timeout_s=normalized_http_timeout,
        state_backend=normalized_state_backend,
        state_sqlite_path=normalized_state_sqlite_path,
    )
    try:
        node_runtime.start()
    except Exception:
        base_handle.shutdown()
        raise

    status = "node_seed"
    if node_runtime.seed_addresses:
        status = "node_joined"
    attached_node_address = node_runtime.seed_addresses[0] if node_runtime.seed_addresses else None
    return replace(
        base_handle,
        local_address=normalized_bind_address,
        attached_node_address=attached_node_address,
        node_id=normalized_node_id,
        connect_timeout=normalized_join_timeout,
        status=status,
        node_runtime=node_runtime,
        node_control_call=node_runtime.node_control_call,
    )


def _normalize_mode(raw_mode: BootstrapMode | str) -> BootstrapMode:
    normalized = str(raw_mode or "").strip().lower()
    if normalized == "local":
        return "local"
    if normalized == "connect_only":
        return "connect_only"
    raise ValueError("node_mode must be one of: local, connect_only.")


def _normalize_address(
    raw_address: str,
    *,
    field_name: str,
    default_port: int,
) -> str:
    normalized = str(raw_address or "").strip()
    if not normalized:
        raise ValueError(f"{field_name} must not be empty.")
    if ":" in normalized:
        return normalized
    return f"{normalized}:{default_port}"


def _normalize_optional_address(
    raw_address: str | None,
    *,
    field_name: str,
    default_port: int,
) -> str | None:
    if raw_address is None:
        return None
    return _normalize_address(raw_address, field_name=field_name, default_port=default_port)


def _normalize_optional_non_empty(raw_value: str | None, *, field_name: str) -> str | None:
    if raw_value is None:
        return None
    normalized = str(raw_value).strip()
    if not normalized:
        raise ValueError(f"{field_name} must not be empty when provided.")
    return normalized


def _normalize_optional_positive_int(raw_value: int | None, *, field_name: str) -> int | None:
    if raw_value is None:
        return None
    normalized = int(raw_value)
    if normalized <= 0:
        raise ValueError(f"{field_name} must be positive when provided.")
    return normalized


def _normalize_optional_non_negative_float(
    raw_value: float | None,
    *,
    field_name: str,
) -> float | None:
    if raw_value is None:
        return None
    normalized = float(raw_value)
    if normalized < 0:
        raise ValueError(f"{field_name} must be >= 0 when provided.")
    return normalized


def _normalize_non_empty(raw_value: Any, *, field_name: str) -> str:
    normalized = str(raw_value or "").strip()
    if not normalized:
        raise ValueError(f"{field_name} must not be empty.")
    return normalized


def _normalize_positive_float(raw_value: float, *, field_name: str) -> float:
    normalized = float(raw_value)
    if normalized <= 0.0:
        raise ValueError(f"{field_name} must be > 0.")
    return normalized


__all__ = [
    "BootstrapMode",
    "V1BootstrapHandle",
    "bootstrap_runtime",
    "bootstrap_node_runtime",
]
