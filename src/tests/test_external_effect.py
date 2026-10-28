from concurrent.futures import ThreadPoolExecutor

import pytest

from sage.runtime.external_effect import EffectInputConflict, EffectOutcomeUnknown, ExternalEffects
from sage.runtime.flownet.runtime.operator_runtime.stateful import StatefulProcessRuntime


def test_concurrent_replay_and_restart(tmp_path):
    path = str(tmp_path / "effects.db")
    states = [StatefulProcessRuntime(backend="sqlite", sqlite_path=path) for _ in range(2)]
    effects = [ExternalEffects(s, namespace="mail") for s in states]
    calls = []

    def invoke():
        calls.append(1)
        return {"sent": True}

    def execute(i):
        try:
            return effects[i % 2].execute("owner:operation", {"to": "fixture"}, invoke)
        except EffectOutcomeUnknown:
            return "pending"

    try:
        with ThreadPoolExecutor(8) as pool:
            results = list(pool.map(execute, range(16)))
        assert calls == [1]
        assert {"sent": True} in results
        with pytest.raises(EffectInputConflict):
            effects[0].execute("owner:operation", {"to": "changed"}, invoke)
    finally:
        for state in states:
            state.close()
    state = StatefulProcessRuntime(backend="sqlite", sqlite_path=path)
    try:
        assert ExternalEffects(state, namespace="mail").execute(
            "owner:operation", {"to": "fixture"}, invoke
        ) == {"sent": True}
        assert calls == [1]
    finally:
        state.close()


@pytest.mark.parametrize("lost_at", ["claim", "external", "completion"])
def test_lost_ack_never_reinvokes(tmp_path, lost_at):
    path = str(tmp_path / "effects.db")
    state = StatefulProcessRuntime(backend="sqlite", sqlite_path=path)
    effects = ExternalEffects(state, namespace="mail")
    calls = []
    original = state.update_many
    writes = []

    def lossy(*args, **kwargs):
        result = original(*args, **kwargs)
        writes.append(1)
        if len(writes) == (1 if lost_at == "claim" else 2):
            raise OSError("commit accepted, acknowledgement lost")
        return result

    if lost_at != "external":
        state.update_many = lossy

    def invoke():
        calls.append(1)
        if lost_at == "external":
            raise OSError("destination accepted, acknowledgement lost")
        return "accepted"

    try:
        with pytest.raises((OSError, EffectOutcomeUnknown)):
            effects.execute("owner:operation", {"value": 1}, invoke)
    finally:
        state.close()
    state = StatefulProcessRuntime(backend="sqlite", sqlite_path=path)
    try:
        replay = ExternalEffects(state, namespace="mail")
        if lost_at == "completion":
            assert replay.execute("owner:operation", {"value": 1}, invoke) == "accepted"
        else:
            with pytest.raises(EffectOutcomeUnknown):
                replay.execute("owner:operation", {"value": 1}, invoke)
        assert len(calls) == (0 if lost_at == "claim" else 1)
    finally:
        state.close()
