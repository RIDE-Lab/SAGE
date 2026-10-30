import json

import pytest
from sage.runtime.scope_audit import scope_audit_format, scope_audit_payload, scope_failures
from sage.runtime.source_audit import InvalidSourceAudit, prepare_source_audit


def request():
    return prepare_source_audit(
        "How to evaluate?",
        "One approach is M.\nEveryone must use M.",
        {"s": "Source author rhetoric."},
    )


def test_challenge_is_bound_but_does_not_repeat_source_rhetoric():
    req = request()
    data = json.loads(scope_audit_payload(req))
    assert data["digest"] == req.digest and data["answer"] == json.loads(req.payload)["answer"]
    assert "Source author rhetoric." not in scope_audit_payload(req)
    assert scope_failures(
        req,
        json.dumps(
            {
                "digest": req.digest,
                "units": {"0": None, "1": "Another method can satisfy the same premises."},
            }
        ),
    ) == (1,)
    assert scope_audit_format(req)["json_schema"]["schema"]["properties"]["units"]["required"] == [
        "0",
        "1",
    ]


@pytest.mark.parametrize(
    "units",
    [
        {"0": None},
        {"0": None, "1": True},
        {"0": None, "1": ""},
        {"0": None, "1": "   "},
        {"0": None, "1": None, "2": None},
    ],
)
def test_missing_malformed_or_extra_decision_fails_closed(units):
    req = request()
    with pytest.raises(InvalidSourceAudit):
        scope_failures(req, json.dumps({"digest": req.digest, "units": units}))


def test_stale_and_duplicate_decisions_cannot_be_accepted():
    req = request()
    for raw in [
        json.dumps({"digest": "stale", "units": {"0": None, "1": None}}),
        '{"digest":"' + req.digest + '","units":{"0":null,"1":"veto","1":null}}',
    ]:
        with pytest.raises(InvalidSourceAudit):
            scope_failures(req, raw)
