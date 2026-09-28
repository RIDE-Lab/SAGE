from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from sage.runtime.flownet.runtime.comm import (
    OP_CONTROL_TOPIC_EVENT_CHAIN_DONE_FORWARD,
    V1ProtocolRouter,
    make_envelope,
)
from sage.runtime.flownet.runtime.topics import TopicAPI

TOPIC = "topic:causal-recovery"
ADDRESS = "127.0.0.1:19090"
EPOCH = 7


def _topic_api(
    state_path: Path | None,
    *,
    on_request_done=None,
) -> TopicAPI:
    api = TopicAPI(
        local_address=ADDRESS,
        coordinator_causal_cut_path=state_path,
        on_request_done=on_request_done,
    )
    api.routing_directory.upsert_route(
        topic_uri=TOPIC,
        coordinator_address=ADDRESS,
        epoch=EPOCH,
    )
    return api


def test_causal_cut_restores_pending_lineage_and_commits_before_done_notify(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "coordinator-cut.json"
    first = _topic_api(state_path)

    first.publish_event(
        topic_uri=TOPIC,
        event_group_id="request-1",
        payload={"token_ids": [1, 2, 3]},
        seq=1,
        epoch=EPOCH,
    )
    first.event_chain_done(
        topic_uri=TOPIC,
        event_group_id="request-1",
        delta=1,
        epoch=EPOCH,
    )
    first.producer_done(
        topic_uri=TOPIC,
        event_group_id="request-1",
        final_seq=1,
        expected_total_events=1,
        epoch=EPOCH,
    )
    before_restart = first.event_group_ledger(
        topic_uri=TOPIC,
        event_group_id="request-1",
        epoch=EPOCH,
    )
    assert before_restart is not None
    assert before_restart["event_chain_pending"] == 1
    assert before_restart["producer_done"] is True
    assert before_restart["request_done_emitted"] is False
    assert before_restart["commit_index"] == 3
    assert len(before_restart["payload_digest"]) == 64
    assert len(before_restart["lineage_digest"]) == 64

    notified: list[dict[str, object]] = []

    def _on_request_done(payload: dict[str, object]) -> None:
        envelope = json.loads(state_path.read_text())
        persisted_ledger = envelope["payload"]["states"][0]["event_group_ledgers"][0]
        assert persisted_ledger["request_done_emitted"] is True
        assert persisted_ledger["commit_index"] == payload["commit_index"]
        notified.append(payload)

    restarted = _topic_api(state_path, on_request_done=_on_request_done)
    restored = restarted.event_group_ledger(
        topic_uri=TOPIC,
        event_group_id="request-1",
        epoch=EPOCH,
    )
    assert restored == before_restart

    result = restarted.event_chain_done(
        topic_uri=TOPIC,
        event_group_id="request-1",
        delta=-1,
        epoch=EPOCH,
    )
    assert result["request_done"]["commit_index"] == 4
    assert len(notified) == 1
    final = restarted.event_group_ledger(
        topic_uri=TOPIC,
        event_group_id="request-1",
        epoch=EPOCH,
    )
    assert final is not None
    assert final["event_chain_pending"] == 0
    assert final["request_done_emitted"] is True
    assert final["commit_index"] == 4


def test_causal_cut_corruption_fails_closed(tmp_path: Path) -> None:
    state_path = tmp_path / "coordinator-cut.json"
    api = _topic_api(state_path)
    api.publish_event(
        topic_uri=TOPIC,
        event_group_id="request-1",
        payload="payload",
        seq=1,
        epoch=EPOCH,
    )
    envelope = json.loads(state_path.read_text())
    envelope["payload"]["commit_index"] += 1
    state_path.write_text(json.dumps(envelope))

    with pytest.raises(RuntimeError, match="causal_cut_restore_failed:digest_mismatch"):
        _topic_api(state_path)


def test_default_registry_does_not_create_durable_state(tmp_path: Path) -> None:
    api = _topic_api(None)
    api.publish_event(
        topic_uri=TOPIC,
        event_group_id="request-1",
        payload="payload",
        seq=1,
        epoch=EPOCH,
    )
    ledger = api.event_group_ledger(
        topic_uri=TOPIC,
        event_group_id="request-1",
        epoch=EPOCH,
    )
    assert ledger is not None
    assert "commit_index" not in ledger
    assert "lineage_digest" not in ledger
    assert list(tmp_path.iterdir()) == []


def test_protocol_router_retains_last_dispatch_failure_for_crash_audit() -> None:
    router = V1ProtocolRouter()

    def _fail(_envelope) -> None:
        raise RuntimeError("event_chain_pending_underflow")

    router.register_handler(
        plane="control",
        op=OP_CONTROL_TOPIC_EVENT_CHAIN_DONE_FORWARD,
        handler=_fail,
    )
    envelope = make_envelope(
        plane="control",
        op=OP_CONTROL_TOPIC_EVENT_CHAIN_DONE_FORWARD,
        source_address="127.0.0.1:19091",
        target_address=ADDRESS,
        request_ref_id="request-1",
        body={
            "mode": "forward_event_chain_done",
            "coordinator_address": ADDRESS,
            "topic_uri": TOPIC,
            "epoch": EPOCH,
            "event_group_id": "request-1",
            "delta": -1,
        },
    )
    with pytest.raises(RuntimeError, match="event_chain_pending_underflow"):
        asyncio.run(router.dispatch(envelope))

    assert router.last_dispatch_error() == {
        "plane": "control",
        "op": OP_CONTROL_TOPIC_EVENT_CHAIN_DONE_FORWARD,
        "error_type": "RuntimeError",
        "message": "event_chain_pending_underflow",
    }
