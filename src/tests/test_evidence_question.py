import json
from dataclasses import replace

import pytest
from sage.runtime.evidence_coverage import (
    InvalidEvidenceCoverage,
    coverage_response_format,
    decode_coverage_selection,
    prepare_coverage,
    question_requirements,
    validate_coverage,
)
from sage.runtime.evidence_question import (
    prepare_question_relation,
    question_relation_format,
    validate_question_relation,
)
from sage.runtime.source_answer_plan import PlanSource


def test_interpretation_input_has_no_retrieved_evidence():
    request = prepare_question_relation(
        "How distinguish benefits from confounding?", "Computer systems"
    )
    assert set(json.loads(request.payload)) == {"question", "public_context", "digest"}
    relation = "Distinguish optimization effects from experimental confounding."
    assert (
        validate_question_relation(
            request, json.dumps({"digest": request.digest, "requested_relation": relation})
        )
        == relation
    )
    assert question_relation_format(request)["json_schema"]["strict"]
    with pytest.raises(InvalidEvidenceCoverage):
        validate_question_relation(
            replace(request, payload=request.payload.replace("Computer systems", "Medicine")),
            json.dumps({"digest": request.digest, "requested_relation": relation}),
        )
    for data in (
        {"digest": "stale", "requested_relation": relation},
        {"digest": request.digest, "requested_relation": " "},
    ):
        with pytest.raises(InvalidEvidenceCoverage):
            validate_question_relation(request, json.dumps(data))


def test_sources_cannot_redefine_frozen_question_relation():
    q = "How distinguish benefits from confounding?"
    req = prepare_coverage(
        q,
        question_requirements(q),
        (PlanSource("a", "Compression feedback", "", "Quantization engineering loop."),),
        requested_relation="Distinguish optimization effects from experimental confounding.",
    )
    raw = {
        "digest": req.digest,
        "requested_relation": req.requested_relation,
        "source_assessments": {
            "a": {
                "studied_subject": "compression",
                "established_relation": "engineering closure",
                "selection": {"role": "background"},
            }
        },
    }
    assert validate_coverage(req, decode_coverage_selection(req, json.dumps(raw))).missing_ids == (
        "question",
    )
    assert coverage_response_format(req)["json_schema"]["schema"]["properties"][
        "requested_relation"
    ]["enum"] == [req.requested_relation]
    raw["requested_relation"] = "Does compression form an engineering feedback loop?"
    with pytest.raises(InvalidEvidenceCoverage):
        validate_coverage(req, decode_coverage_selection(req, json.dumps(raw)))
    with pytest.raises(InvalidEvidenceCoverage):
        validate_coverage(replace(req, requested_relation="changed"), json.dumps(raw))
