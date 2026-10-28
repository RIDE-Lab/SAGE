"""Durable at-most-once invocation for destinations without idempotency keys.

A claim is persisted BEFORE calling the external destination. An uncertain claim
is never reclaimed, including after process death. This sacrifices automatic
retry/liveness when acknowledgement is lost, rather than duplicating an effect.
It is not an exactly-once transport. Callers must authorize every replay and use
an owner-scoped identity. Reconciliation must use destination evidence, not time.
"""

import hashlib
import json
from uuid import uuid4


class EffectOutcomeUnknown(RuntimeError):
    """An invocation may have happened; do not call the destination again."""


class EffectInputConflict(ValueError):
    pass


class ExternalEffects:
    def __init__(self, store, *, namespace):
        if not isinstance(namespace, str) or not namespace.strip():
            raise ValueError("effect_namespace_required")
        self.store, self.namespace = store, namespace

    def begin(self, operation_id, inputs, *, guard=None):
        if not isinstance(operation_id, str) or not operation_id.strip():
            raise ValueError("effect_operation_id_required")
        slot = ("sage-external-effects-v1", self.namespace, operation_id)
        digest = hashlib.sha256(
            json.dumps(inputs, sort_keys=True, allow_nan=False, separators=(",", ":")).encode()
        ).hexdigest()
        token = uuid4().hex
        slots = (slot,)
        if guard is not None:
            guard.validate_store(self.store)
            if guard.slot == slot:
                raise ValueError("effect_guard_overlap")
            slots = (slot, guard.slot)

        def claim(values):
            if guard is not None:
                guard.apply(values)
            old = values[slot]
            if old is not None:
                if old["digest"] != digest:
                    raise EffectInputConflict("effect_input_conflict")
                if old["status"] != "done":
                    raise EffectOutcomeUnknown("external_effect_outcome_unknown")
                return values
            return {**values, slot: {"digest": digest, "token": token, "status": "started"}}

        saved = self.store.update_many(slots, claim)[slot]
        return {
            "slot": slot,
            "token": token,
            "replay": saved["status"] == "done",
            "result": saved.get("result"),
        }

    def complete(self, claim, result):
        result = json.loads(json.dumps(result, allow_nan=False))
        slot = claim["slot"]

        def finish(values):
            old = values[slot]
            if old is None or old["token"] != claim["token"] or old["status"] != "started":
                raise EffectOutcomeUnknown("external_effect_claim_changed")
            return {slot: {**old, "status": "done", "result": result}}

        self.store.update_many((slot,), finish)
        return result

    def execute(self, operation_id, inputs, invoke, *, guard=None):
        claim = self.begin(operation_id, inputs, guard=guard)
        if claim["replay"]:
            return claim["result"]
        try:
            return self.complete(claim, invoke())
        except Exception as error:
            # Leave the durable claim in place, even if the destination accepted
            # but the local acknowledgement transaction failed.
            raise EffectOutcomeUnknown("external_effect_outcome_unknown") from error
