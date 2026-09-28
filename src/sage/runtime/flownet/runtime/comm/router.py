from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from threading import Lock
from typing import Any

from sage.runtime.flownet.runtime.comm.protocol import V1Envelope, coerce_envelope

V1ProtocolHandler = Callable[[V1Envelope], Any | Awaitable[Any]]


class V1ProtocolRouter:
    """Dispatches v1 envelopes by (plane, op) to registered handlers."""

    def __init__(self) -> None:
        self._handlers: dict[tuple[str, str], V1ProtocolHandler] = {}
        self._lock = Lock()
        self._stats: dict[str, int] = {
            "dispatch_total": 0,
            "missing_handler": 0,
            "dispatch_errors": 0,
        }
        self._last_dispatch_error: dict[str, str] | None = None

    def register_handler(
        self,
        *,
        plane: str,
        op: str,
        handler: V1ProtocolHandler,
    ) -> None:
        if not callable(handler):
            raise TypeError("handler must be callable.")
        key = _normalize_handler_key(plane=plane, op=op)
        with self._lock:
            if key in self._handlers:
                raise ValueError(f"handler_already_registered:{key[0]}:{key[1]}")
            self._handlers[key] = handler

    def unregister_handler(self, *, plane: str, op: str) -> bool:
        key = _normalize_handler_key(plane=plane, op=op)
        with self._lock:
            return self._handlers.pop(key, None) is not None

    def has_handler(self, *, plane: str, op: str) -> bool:
        key = _normalize_handler_key(plane=plane, op=op)
        with self._lock:
            return key in self._handlers

    async def dispatch(self, envelope_payload: V1Envelope | dict[str, Any]) -> Any:
        envelope = coerce_envelope(envelope_payload)
        key = (envelope.plane, envelope.op)
        handler = self._resolve_handler(key)
        self._bump("dispatch_total")

        if handler is None:
            self._bump("missing_handler")
            raise RuntimeError(f"protocol_handler_not_found:{envelope.plane}:{envelope.op}")

        try:
            result = handler(envelope)
            if inspect.isawaitable(result):
                result = await result
            return result
        except Exception as exc:
            self._bump("dispatch_errors")
            with self._lock:
                self._last_dispatch_error = {
                    "plane": envelope.plane,
                    "op": envelope.op,
                    "error_type": type(exc).__name__,
                    "message": str(exc),
                }
            raise

    def stats_snapshot(self) -> dict[str, int]:
        with self._lock:
            return dict(self._stats)

    def last_dispatch_error(self) -> dict[str, str] | None:
        with self._lock:
            if self._last_dispatch_error is None:
                return None
            return dict(self._last_dispatch_error)

    def _resolve_handler(self, key: tuple[str, str]) -> V1ProtocolHandler | None:
        with self._lock:
            return self._handlers.get(key)

    def _bump(self, key: str) -> None:
        with self._lock:
            self._stats[key] = int(self._stats.get(key, 0)) + 1


def _normalize_handler_key(*, plane: str, op: str) -> tuple[str, str]:
    normalized_plane = str(plane or "").strip().lower()
    normalized_op = str(op or "").strip()
    if not normalized_plane:
        raise ValueError("plane must be non-empty.")
    if not normalized_op:
        raise ValueError("op must be non-empty.")
    return normalized_plane, normalized_op


__all__ = [
    "V1ProtocolHandler",
    "V1ProtocolRouter",
]
