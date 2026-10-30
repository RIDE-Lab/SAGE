import json

import pytest
from sage.runtime.coverage_relation import (
    apply_relation_review,
    prepare_relation_review,
    relation_review_format,
)
from sage.runtime.evidence_coverage import (
    InvalidEvidenceCoverage,
    prepare_coverage,
    question_requirements,
    validate_coverage,
)
from sage.runtime.source_answer_plan import PlanSource


def fixture():
    q = "How to distinguish an intervention from experimental noise?"
    request = prepare_coverage(
        q,
        question_requirements(q),
        (
            PlanSource(
                "adjacent", "System architecture", "", "A feedback loop provides engineering value."
            ),
            PlanSource("method", "Evaluation", "", "Replication estimates variability."),
            PlanSource("background", "Other", "", "Unrelated background."),
        ),
        requested_relation="Identify experimental controls separating intervention effects from noise.",
    )
    paragraphs = json.loads(request.payload)["sources"]
    wire = json.dumps(
        {
            "digest": request.digest,
            "requested_relation": request.requested_relation,
            "source_assessments": {
                s["source_id"]: {
                    "studied_subject": s["title"],
                    "established_relation": s["lines"][0]["text"],
                    "selection": {"role": "background"}
                    if s["source_id"] == "background"
                    else {
                        "role": "direct",
                        "requirements": {"question": {s["paragraphs"][0]["id"]: True}},
                    },
                }
                for s in paragraphs
            },
        }
    )
    review = prepare_relation_review(request, wire)
    raw = {
        "digest": review.digest,
        "decisions": {
            "adjacent": {
                "reason": "Engineering value is not an experimental control.",
                "direct": False,
            },
            "method": {"reason": "Repetition estimates noise.", "direct": True},
        },
    }
    return request, wire, review, raw


def test_veto_retains_exact_accepted_witness_without_promoting_background():
    request, wire, review, raw = fixture()
    body = json.loads(review.payload)
    assert set(body) == {"digest", "question", "requested_relation", "source_relations"}
    assert set(body["source_relations"]) == {"adjacent", "method"}
    assert all(
        set(r) == {"studied_subject", "established_relation"}
        for r in body["source_relations"].values()
    )
    receipt = validate_coverage(request, apply_relation_review(request, wire, json.dumps(raw)))
    assert [w.source_id for w in receipt.items[0].witnesses] == ["method"]
    assert receipt.items[0].witnesses[0].quote == "Replication estimates variability."
    assert relation_review_format(review)["json_schema"]["strict"]


@pytest.mark.parametrize(
    "mutation", ["stale", "missing", "extra", "nonbool", "empty_reason", "changed_mapping"]
)
def test_incomplete_stale_or_promoted_review_is_rejected(mutation):
    request, wire, review, raw = fixture()
    if mutation == "stale":
        raw["digest"] = "old"
    if mutation == "missing":
        del raw["decisions"]["adjacent"]
    if mutation == "extra":
        raw["decisions"]["background"] = {"direct": True, "reason": "promote"}
    if mutation == "nonbool":
        raw["decisions"]["method"]["direct"] = 1
    if mutation == "empty_reason":
        raw["decisions"]["method"]["reason"] = " "
    if mutation == "changed_mapping":
        value = json.loads(wire)
        value["source_assessments"]["method"]["established_relation"] = "changed"
        wire = json.dumps(value)
    with pytest.raises(InvalidEvidenceCoverage):
        apply_relation_review(request, wire, json.dumps(raw))


def test_all_vetoes_create_a_real_gap_not_fabricated_support():
    request, wire, review, raw = fixture()
    raw["decisions"]["method"]["direct"] = False
    assert validate_coverage(
        request, apply_relation_review(request, wire, json.dumps(raw))
    ).missing_ids == ("question",)
