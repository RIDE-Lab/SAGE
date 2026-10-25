from __future__ import annotations

import queue
import time

from sage.foundation import (
    BaseCoMapFunction,
    BaseJoinFunction,
    BatchFunction,
    KeyByFunction,
    MapFunction,
    SinkFunction,
    SourceFunction,
)
from sage.runtime import BaseService, LocalEnvironment
from sage.runtime.exception_hooks import (
    get_registered_exception_handler_hook,
    register_kernel_exception_handler_hook,
)
from sage.runtime.job_manager import JobManager
from sage.runtime.scheduler import FIFOScheduler, LoadAwareScheduler, resolve_scheduler
from sage.stream._runtime_kernel_types import Packet, StopSignal


class NumberBatchSource(BatchFunction):
    def __init__(self) -> None:
        super().__init__()
        self._current = 0

    def execute(self):
        if self._current >= 4:
            return StopSignal("numbers-done")
        value = self._current
        self._current += 1
        return value


class DoubleValue(MapFunction):
    def execute(self, data: int) -> int:
        return data * 2


class CollectSink(SinkFunction):
    collected: list[int] = []

    def execute(self, data: int) -> None:
        type(self).collected.append(data)


class RecordReplicaIndex(MapFunction):
    def execute(self, data: int) -> dict[str, int]:
        return {"value": data, "replica": int(getattr(self.ctx, "parallel_index", -1))}


class RecordKeyReplica(MapFunction):
    def execute(self, data: dict[str, int | str]) -> dict[str, int | str]:
        return {
            "key": str(data["key"]),
            "replica": int(getattr(self.ctx, "parallel_index", -1)),
        }


class CollectReplicaAssignments(SinkFunction):
    collected: list[dict[str, object]] = []

    def execute(self, data: dict[str, object]) -> None:
        type(self).collected.append(data)


class SlowTickSource(BatchFunction):
    def __init__(self) -> None:
        super().__init__()
        self._current = 0

    def execute(self):
        time.sleep(0.02)
        self._current += 1
        return self._current


class JoinLeftSource(BatchFunction):
    def __init__(self) -> None:
        super().__init__()
        self._items = [
            {"id": 1, "msg": "Hello-1", "type": "hello"},
            {"id": 2, "msg": "Hello-2", "type": "hello"},
        ]

    def execute(self):
        if not self._items:
            return StopSignal("left-done")
        return self._items.pop(0)


class JoinRightSource(BatchFunction):
    def __init__(self) -> None:
        super().__init__()
        self._items = [
            {"id": 1, "msg": "World-1", "type": "world"},
            {"id": 2, "msg": "World-2", "type": "world"},
        ]

    def execute(self):
        if not self._items:
            return StopSignal("right-done")
        return self._items.pop(0)


class IdKey(KeyByFunction):
    def execute(self, data):
        return data["id"]


class HelloWorldJoin(BaseJoinFunction):
    def __init__(self) -> None:
        super().__init__()
        self._left: dict[int, list[dict[str, str]]] = {}
        self._right: dict[int, list[dict[str, str]]] = {}

    def execute(self, payload, key, tag):
        results = []
        if tag == 0:
            self._left.setdefault(key, []).append(payload)
            for right in self._right.get(key, []):
                results.append({"id": key, "msg": f"{payload['msg']} + {right['msg']}"})
        else:
            self._right.setdefault(key, []).append(payload)
            for left in self._left.get(key, []):
                results.append({"id": key, "msg": f"{left['msg']} + {payload['msg']}"})
        return results


class CollectJoinSink(SinkFunction):
    collected: list[dict[str, str]] = []

    def execute(self, data):
        type(self).collected.append(data)


class CoMapLeftSource(BatchFunction):
    def __init__(self) -> None:
        super().__init__()
        self._items = [{"msg": "Hello-1"}, {"msg": "Hello-2"}]

    def execute(self):
        if not self._items:
            return StopSignal("comap-left-done")
        return self._items.pop(0)


class CoMapRightSource(BatchFunction):
    def __init__(self) -> None:
        super().__init__()
        self._items = [{"msg": "World-1"}, {"msg": "World-2"}]

    def execute(self):
        if not self._items:
            return StopSignal("comap-right-done")
        return self._items.pop(0)


class HelloWorldCoMap(BaseCoMapFunction):
    def map0(self, data):
        return f"left:{data['msg']}"

    def map1(self, data):
        return f"right:{data['msg']}"


class CollectCoMapSink(SinkFunction):
    collected: list[str] = []

    def execute(self, data):
        type(self).collected.append(data)


class ServiceBridgeSource(SourceFunction):
    def __init__(self, request_queue: queue.Queue):
        super().__init__()
        self._request_queue = request_queue

    def execute(self, data=None):
        try:
            payload = self._request_queue.get_nowait()
        except queue.Empty:
            return None

        if payload == "STOP":
            return StopSignal("service-bridge-stop")
        return payload


class EchoPipelineService(BaseService):
    def __init__(self, request_queue: queue.Queue, request_timeout: float = 2.0):
        super().__init__()
        self._request_queue = request_queue
        self._request_timeout = request_timeout

    def process(self, message: dict[str, int | str]):
        if message.get("command") == "shutdown":
            self._request_queue.put("STOP")
            return {"status": "shutdown_requested"}

        response_queue: queue.Queue[dict[str, int]] = queue.Queue(maxsize=1)
        self._request_queue.put({"value": int(message["value"]), "response_queue": response_queue})
        return response_queue.get(timeout=self._request_timeout)


class EchoWorker(MapFunction):
    def execute(self, payload: dict[str, object]):
        value = int(payload["value"])
        return {"value": value * 2, "response_queue": payload["response_queue"]}


class EchoPublishSink(SinkFunction):
    def execute(self, payload: dict[str, object]):
        response_queue = payload["response_queue"]
        assert isinstance(response_queue, queue.Queue)
        response_queue.put({"value": int(payload["value"])})


class InvokeEchoPipeline(MapFunction):
    def execute(self, payload: dict[str, int | str]):
        response = self.call_service("echo_pipeline", payload, timeout=2.0)
        if payload.get("command") == "shutdown":
            return {"type": "control", "response": response}
        return {"type": "decision", "value": response["value"]}


class CollectServiceSink(SinkFunction):
    collected: list[int] = []
    control_messages: list[dict[str, str]] = []

    def execute(self, payload: dict[str, object]):
        if payload["type"] == "decision":
            type(self).collected.append(int(payload["value"]))
        else:
            response = payload["response"]
            assert isinstance(response, dict)
            type(self).control_messages.append(response)


def test_packet_and_stop_signal_are_main_repo_owned() -> None:
    signal = StopSignal("done", source="source-1")
    packet = Packet(payload={"x": 1}, input_index=2, partition_key="k", partition_strategy="hash")

    copied = packet.copy()
    rekeyed = packet.update_key("k2")

    assert signal.name == "done"
    assert signal.source == "source-1"
    assert copied == packet
    assert rekeyed.partition_key == "k2"
    assert packet.inherit_partition_info("payload").partition_strategy == "hash"


def test_scheduler_resolution_uses_in_tree_implementations() -> None:
    fifo = resolve_scheduler(scheduler="fifo", platform="local")
    load_aware = resolve_scheduler(scheduler="load_aware", platform="local")
    configured_load_aware = resolve_scheduler(
        scheduler={
            "type": "load-aware",
            "strategy": "spread",
            "max_concurrent": 4,
        },
        platform="local",
    )

    assert isinstance(fifo, FIFOScheduler)
    assert isinstance(load_aware, LoadAwareScheduler)
    assert isinstance(configured_load_aware, LoadAwareScheduler)
    assert fifo.get_metrics()["scheduler_type"] == "FIFO"
    assert load_aware.get_metrics()["scheduler_type"] == "LoadAware"
    assert configured_load_aware.strategy == "spread"
    assert configured_load_aware.max_concurrent == 4


def test_scheduler_resolution_accepts_scheduler_mapping_aliases() -> None:
    scheduler = resolve_scheduler(
        scheduler={
            "name": "load aware",
            "strategy": "pack",
            "max_concurrent": 2,
        },
        platform="local",
    )

    assert isinstance(scheduler, LoadAwareScheduler)
    assert scheduler.strategy == "pack"
    assert scheduler.max_concurrent == 2


def test_scheduler_resolution_rejects_scheduler_mapping_without_type() -> None:
    try:
        resolve_scheduler(scheduler={"strategy": "spread"}, platform="local")
    except ValueError as exc:
        assert "scheduler mapping must include one of" in str(exc)
    else:  # pragma: no cover - defensive guard
        raise AssertionError("resolve_scheduler() should reject mapping configs without type")


def test_local_environment_batch_submit_runs_without_kernel_dependency() -> None:
    CollectSink.collected = []
    JobManager().cleanup_all_jobs()

    env = LocalEnvironment(name="batch-test")
    env.from_batch(NumberBatchSource).map(DoubleValue).sink(CollectSink)

    env_uuid = env.submit(autostop=True)
    status = env.jobmanager.get_job_status(env_uuid)

    assert CollectSink.collected == [0, 2, 4, 6]
    assert status["status"] == "stopped"
    assert status["pipeline_size"] == 3


def test_local_environment_round_robins_unkeyed_parallel_map_locally() -> None:
    CollectReplicaAssignments.collected = []
    JobManager().cleanup_all_jobs()

    env = LocalEnvironment(name="local-parallel-map-round-robin")
    env.from_batch([0, 1, 2, 3]).map(RecordReplicaIndex, parallelism=2).sink(
        CollectReplicaAssignments
    )

    env_uuid = env.submit(autostop=True)
    status = env.jobmanager.get_job_status(env_uuid)

    assert CollectReplicaAssignments.collected == [
        {"value": 0, "replica": 0},
        {"value": 1, "replica": 1},
        {"value": 2, "replica": 0},
        {"value": 3, "replica": 1},
    ]
    assert status["status"] == "stopped"


def test_local_environment_keeps_keyed_parallel_map_on_stable_replica() -> None:
    CollectReplicaAssignments.collected = []
    JobManager().cleanup_all_jobs()

    env = LocalEnvironment(name="local-parallel-map-keyed")
    (
        env.from_batch(
            [
                {"key": "alpha", "value": 1},
                {"key": "beta", "value": 2},
                {"key": "alpha", "value": 3},
                {"key": "beta", "value": 4},
            ]
        )
        .keyby(lambda item: item["key"])
        .map(RecordKeyReplica, parallelism=2)
        .sink(CollectReplicaAssignments)
    )

    env_uuid = env.submit(autostop=True)
    status = env.jobmanager.get_job_status(env_uuid)

    replica_by_key: dict[str, int] = {}
    for item in CollectReplicaAssignments.collected:
        key = str(item["key"])
        replica_index = int(item["replica"])
        assert isinstance(key, str)
        previous = replica_by_key.setdefault(key, replica_index)
        assert previous == replica_index

    assert set(replica_by_key) == {"alpha", "beta"}
    assert status["status"] == "stopped"


def test_local_environment_streaming_job_can_be_stopped() -> None:
    JobManager().cleanup_all_jobs()

    env = LocalEnvironment(name="stream-test")
    env.from_batch(SlowTickSource).map(DoubleValue).sink(CollectSink)

    env_uuid = env.submit(autostop=False)
    status = env.jobmanager.get_job_status(env_uuid)
    assert status["status"] == "running"

    env.stop()
    stopped_status = env.jobmanager.get_job_status(env_uuid)
    assert stopped_status["status"] == "stopped"


def test_local_environment_batch_join_runs_with_two_sources() -> None:
    CollectJoinSink.collected = []
    JobManager().cleanup_all_jobs()

    env = LocalEnvironment(name="join-test")
    left = env.from_batch(JoinLeftSource)
    right = env.from_batch(JoinRightSource)

    left.keyby(IdKey).connect(right.keyby(IdKey)).join(HelloWorldJoin).sink(CollectJoinSink)

    env_uuid = env.submit(autostop=True)
    status = env.jobmanager.get_job_status(env_uuid)

    assert CollectJoinSink.collected == [
        {"id": 1, "msg": "Hello-1 + World-1"},
        {"id": 2, "msg": "Hello-2 + World-2"},
    ]
    assert status["status"] == "stopped"
    assert status["pipeline_size"] == 6


def test_local_environment_batch_comap_runs_with_two_sources() -> None:
    CollectCoMapSink.collected = []
    JobManager().cleanup_all_jobs()

    env = LocalEnvironment(name="comap-test")
    left = env.from_batch(CoMapLeftSource)
    right = env.from_batch(CoMapRightSource)

    left.connect(right).comap(HelloWorldCoMap).sink(CollectCoMapSink)

    env_uuid = env.submit(autostop=True)
    status = env.jobmanager.get_job_status(env_uuid)

    assert CollectCoMapSink.collected == [
        "left:Hello-1",
        "right:World-1",
        "left:Hello-2",
        "right:World-2",
    ]
    assert status["status"] == "stopped"
    assert status["pipeline_size"] == 4


def test_local_environment_pipeline_service_self_call_completes() -> None:
    CollectServiceSink.collected = []
    CollectServiceSink.control_messages = []
    JobManager().cleanup_all_jobs()

    env = LocalEnvironment(name="service-self-call-test")
    request_queue: queue.Queue[object] = queue.Queue()

    env.register_service("echo_pipeline", EchoPipelineService, request_queue)

    (env.from_source(ServiceBridgeSource, request_queue).map(EchoWorker).sink(EchoPublishSink))

    (
        env.from_batch(
            [
                {"value": 1},
                {"value": 2},
                {"command": "shutdown"},
            ]
        )
        .map(InvokeEchoPipeline)
        .sink(CollectServiceSink)
    )

    env_uuid = env.submit(autostop=True)
    status = env.jobmanager.get_job_status(env_uuid)

    assert CollectServiceSink.collected == [2, 4]
    assert CollectServiceSink.control_messages == [{"status": "shutdown_requested"}]
    assert status["status"] == "stopped"


def test_exception_hook_registration_is_owned_in_tree() -> None:
    pushed: list[str] = []

    def push(handler):
        pushed.append("push")

    def pop():
        pushed.append("pop")

    register_kernel_exception_handler_hook(push, pop)
    registered_push, registered_pop = get_registered_exception_handler_hook()

    assert registered_push is push
    assert registered_pop is pop
