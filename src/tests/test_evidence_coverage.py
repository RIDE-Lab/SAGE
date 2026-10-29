import json
from dataclasses import replace

import pytest

from sage.runtime.evidence_coverage import (
    EvidenceRequirement,
    InvalidEvidenceCoverage,
    coverage_response_format,
    prepare_coverage,
    question_requirements,
    repair_queries,
    validate_coverage,
)
from sage.runtime.source_answer_plan import PlanSource


def request():
    return prepare_coverage("A? B?", (EvidenceRequirement("a", 0, 3), EvidenceRequirement("b", 3, 5)),
        (PlanSource("public", "A", "https://example.org/a", "A uses state ownership. Measurements are not displayed here."),))


def response(req, **changes):
    data = {"digest": req.digest, "items": [
        {"requirement_id": "a", "witnesses": [{"source_id": "public", "start_line": 1, "end_line": 1}]},
        {"requirement_id": "b", "witnesses": []}]}
    data.update(changes)
    return json.dumps(data)


def test_complete_question_and_exact_witnesses_bind_repair():
    req = request()
    checked = validate_coverage(req, response(req))
    assert checked.missing_ids == ("b",)
    assert repair_queries(req, checked) == ("B?",)
    assert "Measurements are not displayed here." in req.payload
    assert coverage_response_format(req)["json_schema"]["strict"] is True


@pytest.mark.parametrize("start,end", [(0, 1), (1, 2), (2, 1), (True, 1), (1, 1.0), ("1", 1)])
def test_witness_ranges_fail_closed(start, end):
    req = request()
    with pytest.raises(InvalidEvidenceCoverage):
        validate_coverage(req, response(req, items=[{"requirement_id": "a", "witnesses": [
            {"source_id": "public", "start_line": start, "end_line": end}]},
            {"requirement_id": "b", "witnesses": []}]))


def test_source_lines_are_lossless_and_receipts_cannot_replace_source_text():
    req = request()
    content = "标题\r\n\r\n测量数据未在本文展示。\n完整链接：https://example.org/full?q=one&n=two"
    req = prepare_coverage(req.question, req.requirements, (replace(req.sources[0], content=content),))
    payload = json.loads(req.payload)
    assert "".join(line["text"] for line in payload["sources"][0]["lines"]) == content
    checked = validate_coverage(req, response(req, items=[{"requirement_id": "a", "witnesses": [
        {"source_id": "public", "start_line": 3, "end_line": 4}]},
        {"requirement_id": "b", "witnesses": []}]))
    assert checked.items[0].witnesses[0].quote == "".join(content.splitlines(keepends=True)[2:])
    tampered = replace(checked, items=(replace(checked.items[0], witnesses=(
        replace(checked.items[0].witnesses[0], quote="作者从未测量"),)), checked.items[1]))
    with pytest.raises(InvalidEvidenceCoverage, match="receipt content changed"):
        repair_queries(req, tampered)


def test_repair_anchors_original_identifiers_without_losing_missing_spans():
    question = "请评审 ExampleState 和 ABC：比较正确性与性能；不要推断结果。"
    req = prepare_coverage(question, question_requirements(question), ())
    assessment = validate_coverage(req, json.dumps({"digest": req.digest, "items": [
        {"requirement_id": r.requirement_id, "witnesses": []} for r in req.requirements]}))
    queries = repair_queries(req, assessment)
    assert queries[0] == "ExampleState ABC"
    assert "".join(queries[1:]) == question
    assert len(queries) == len(req.requirements)+1


@pytest.mark.parametrize("requirements", [(), (EvidenceRequirement("a", 1, 5),),
    (EvidenceRequirement("a", 0, 4),), (EvidenceRequirement("a", 0, 3), EvidenceRequirement("a", 3, 5)),
    (EvidenceRequirement("a", 0, 3), EvidenceRequirement("b", 2, 5)),
    (EvidenceRequirement("a", False, 5),)])
def test_partition_cannot_silently_drop_rewrite_or_duplicate_question(requirements):
    with pytest.raises(InvalidEvidenceCoverage):
        prepare_coverage("A? B?", requirements, request().sources)


@pytest.mark.parametrize("source,quote", [("private-not-authorized", "A uses state ownership."),
    ("public", "No measurements were ever performed."), ("public", ""), ("public", " "),
    ("public", None), ([], "A")])
def test_unwitnessed_claim_cannot_become_a_coverage_receipt(source, quote):
    req = request()
    with pytest.raises(InvalidEvidenceCoverage):
        validate_coverage(req, response(req, items=[{"requirement_id": "a", "witnesses": [
            {"source_id": source, "quote": quote}]}, {"requirement_id": "b", "witnesses": []}]))


@pytest.mark.parametrize("items", [[], [{"requirement_id": "a", "witnesses": []}],
    [{"requirement_id": "a", "witnesses": []}]*2,
    [{"requirement_id": "unknown", "witnesses": []}],
    [{"requirement_id": "a", "witnesses": [], "passed": True}]])
def test_no_missing_duplicate_unknown_or_model_passed_flags(items):
    with pytest.raises(InvalidEvidenceCoverage):
        validate_coverage(request(), response(request(), items=items))


def test_changed_question_sources_or_requirements_require_a_fresh_assessment():
    req = request()
    checked = validate_coverage(req, response(req))
    for changed in [prepare_coverage("C? B?", req.requirements, req.sources),
                    prepare_coverage(req.question, req.requirements,
                        (replace(req.sources[0], content=req.sources[0].content+" More context."),))]:
        with pytest.raises(InvalidEvidenceCoverage):
            repair_queries(changed, checked)
    with pytest.raises(InvalidEvidenceCoverage):
        validate_coverage(req, response(req).replace('"digest":', '"digest":"duplicate", "digest":'))


def test_empty_retrieval_maps_explicit_gaps_and_preserves_the_whole_question():
    req = prepare_coverage("No source?", (EvidenceRequirement("all", 0, 10),), ())
    result = validate_coverage(req, json.dumps({"digest": req.digest,
        "items": [{"requirement_id": "all", "witnesses": []}]}))
    assert repair_queries(req, result) == ("No source?",)


def test_syntactic_partition_retains_every_character_and_qualifier():
    question = "A 和 B 分别是什么？\n请给出处；不要推断没有实验。  "
    parts = question_requirements(question)
    assert len(parts) > 1
    assert "".join(question[r.start:r.end] for r in parts) == question
    prepare_coverage(question, parts, ())


@pytest.mark.parametrize("field", ["question", "sources", "requirements", "payload"])
def test_request_cannot_be_rebound_while_reusing_the_digest(field):
    req = request()
    replacements = {"question": "C? B?", "sources": (replace(req.sources[0], content="changed"),),
        "requirements": (EvidenceRequirement("all", 0, 5),), "payload": req.payload+" "}
    with pytest.raises(InvalidEvidenceCoverage):
        validate_coverage(replace(req, **{field: replacements[field]}), response(req))


def test_decoder_schema_binds_selections_to_original_source_paragraphs():
    from jsonschema import Draft202012Validator
    from sage.runtime.evidence_coverage import decode_coverage_selection
    req = prepare_coverage('Explain the pipeline?', question_requirements('Explain the pipeline?'), (
        PlanSource('wiki', 'Pipeline', 'https://example.org/wiki', 'prefill\ndecode\nverification'),
        PlanSource('web', 'Snippet', 'https://example.org/web', 'One line only.'),
    ))
    validator = Draft202012Validator(coverage_response_format(req)['json_schema']['schema'])
    def mapping(value):
        return {'digest': req.digest, 'items': [{'requirement_id': 'question', 'witnesses': [value]}]}
    for paragraph, source, end in [('p0', 'wiki', 3), ('p1', 'web', 1)]:
        assert validator.is_valid(mapping(paragraph))
        receipt = validate_coverage(req, decode_coverage_selection(req, json.dumps(mapping(paragraph))))
        assert receipt.items[0].witnesses[0].source_id == source
        assert receipt.items[0].witnesses[0].end_line == end
    for invalid in ['p2', {'source_id': 'web', 'start_line': 1, 'end_line': 3}]:
        assert not validator.is_valid(mapping(invalid))
        with pytest.raises(InvalidEvidenceCoverage):
            validate_coverage(req, decode_coverage_selection(req, json.dumps(mapping(invalid))))


def test_diagnostics_never_forward_unrecognized_model_or_exception_text():
    assert InvalidEvidenceCoverage('witness range not in authorized source').code == 'witness_out_of_range'
    assert InvalidEvidenceCoverage('secret-or-source-body').code == 'invalid_request_or_receipt'


def test_coverage_purpose_is_bound_and_cannot_reuse_source_receipt():
    req = request()
    complete = prepare_coverage(req.question, req.requirements, req.sources, purpose="answer_completeness")
    assert req.digest != complete.digest
    assert json.loads(complete.payload)["purpose"] == "answer_completeness"
    with pytest.raises(InvalidEvidenceCoverage):
        validate_coverage(replace(complete, purpose="source_support"), '{}')
    with pytest.raises(InvalidEvidenceCoverage):
        prepare_coverage(req.question, req.requirements, req.sources, purpose="ignore-gaps")


def test_decoder_pairs_endpoints_and_covers_every_nonblank_line_without_truncation():
    from jsonschema import Draft202012Validator
    from sage.runtime.evidence_coverage import decode_coverage_selection
    source = PlanSource('s', 'Source', '', 'heading\r\n\r\n' + 'body ' * 5000 + '\r\ncontinued\r\n\r\nlast')
    req = prepare_coverage('What is reported?', question_requirements('What is reported?'), (source,))
    schema = coverage_response_format(req)['json_schema']['schema']
    witnesses = schema['properties']['items']['items']['properties']['witnesses']
    covered = set()
    for paragraph in witnesses['items']['enum']:
        raw = {'digest': req.digest, 'items': [{'requirement_id': 'question', 'witnesses': [paragraph]}]}
        assert Draft202012Validator(schema).is_valid(raw)
        receipt = validate_coverage(req, decode_coverage_selection(req, json.dumps(raw)))
        value = receipt.items[0].witnesses[0]
        assert value.quote == ''.join(source.content.splitlines(keepends=True)[value.start_line-1:value.end_line])
        covered.update(range(value.start_line, value.end_line + 1))
    assert covered == {i for i, line in enumerate(source.content.splitlines(), 1) if line.strip()}
    assert witnesses['maxItems'] == 3
    assert ''.join(line['text'] for line in json.loads(req.payload)['sources'][0]['lines']) == source.content
    raw['items'][0]['witnesses'] *= 4
    assert not Draft202012Validator(schema).is_valid(raw)
    with pytest.raises(InvalidEvidenceCoverage):
        validate_coverage(req, decode_coverage_selection(req, json.dumps(raw)))
    # A reversed range is impossible in the model protocol and remains rejected
    # by the independent canonical validator (including historical receipts).
    raw['items'][0]['witnesses'] = [{'source_id': 's', 'start_line': 3, 'end_line': 1}]
    assert not Draft202012Validator(schema).is_valid(raw)
    with pytest.raises(InvalidEvidenceCoverage):
        validate_coverage(req, json.dumps(raw))


def test_compact_paragraph_decoder_schema_grows_with_ids_not_object_branches():
    source = PlanSource('s', 'Long source', '', '\n\n'.join('Paragraph ' + str(i) for i in range(1000)))
    req = prepare_coverage('Question?', question_requirements('Question?'), (source,))
    assert len(json.dumps(coverage_response_format(req))) < 10000


@pytest.mark.parametrize('raw', ['not JSON', '{"digest":"stale","items":[]}', '{"unexpected":true}'])
def test_model_selection_decode_never_turns_bad_output_into_empty_valid_coverage(raw):
    from sage.runtime.evidence_coverage import decode_coverage_selection
    req = request()
    with pytest.raises(InvalidEvidenceCoverage):
        validate_coverage(req, decode_coverage_selection(req, raw))
