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



def selection(req, selected):
    payload = json.loads(req.payload)
    return {'digest': req.digest, 'requested_relation': 'fixture relation', 'source_assessments': {source['source_id']: {
        'studied_subject':'fixture', 'established_relation':'fixture relation', 'selection': {'role':'direct', 'requirements': {
            r.requirement_id: {p['id']: True for p in source['paragraphs'] if p['id'] in selected}
            for r in req.requirements}}} for source in payload['sources']}}


def test_decoder_binds_source_local_sets_to_original_ranges_without_truncation():
    from jsonschema import Draft202012Validator
    from sage.runtime.evidence_coverage import decode_coverage_selection
    text = 'heading\r\n\r\n' + 'body '*5000 + '\r\ncontinued\r\n\r\nlast'
    sources = (PlanSource('s','Source','',text), PlanSource('other','Other','','Other source.'))
    req = prepare_coverage('Question?',question_requirements('Question?'),sources)
    validator = Draft202012Validator(coverage_response_format(req)['json_schema']['schema'])
    covered = {source.source_id:set() for source in sources}
    for source in json.loads(req.payload)['sources']:
        for paragraph in source['paragraphs']:
            raw = selection(req,[paragraph['id']])
            assert validator.is_valid(raw)
            receipt = validate_coverage(req,decode_coverage_selection(req,json.dumps(raw)))
            witness = receipt.items[0].witnesses[0]
            original = next(s for s in sources if s.source_id == witness.source_id)
            assert witness.quote == ''.join(original.content.splitlines(keepends=True)[witness.start_line-1:witness.end_line])
            covered[witness.source_id].update(range(witness.start_line,witness.end_line+1))
    for source in sources:
        assert covered[source.source_id] == {i for i,line in enumerate(source.content.splitlines(),1) if line.strip()}
    assert json.loads(req.payload)['version'] == 7


@pytest.mark.parametrize('mutation', ['background','irrelevant','unknown','other_source','nonbool','false','array','omitted','stale','missing_subject','extra'])
def test_invalid_selection_cannot_be_silently_accepted_or_promote_a_role(mutation):
    from jsonschema import Draft202012Validator
    from sage.runtime.evidence_coverage import decode_coverage_selection
    req = prepare_coverage('Question?',question_requirements('Question?'),(
        PlanSource('s','Source','','A.'),PlanSource('other','Other','','B.')))
    data = selection(req,['p0'])
    item = data['source_assessments']['s']
    if mutation in ('background','irrelevant'):item['selection']['role'] = mutation
    if mutation == 'unknown':item['selection']['requirements']['question'] = {'p99':True}
    if mutation == 'other_source':item['selection']['requirements']['question'] = {'p1':True}
    if mutation == 'nonbool':item['selection']['requirements']['question'] = {'p0':1}
    if mutation == 'false':item['selection']['requirements']['question'] = {'p0':False}
    if mutation == 'array':item['selection']['requirements']['question'] = ['p0','p0']
    if mutation == 'omitted':data['source_assessments'].pop('other')
    if mutation == 'stale':data['digest'] = 'stale'
    if mutation == 'missing_subject':item.pop('studied_subject')
    if mutation == 'extra':data['extra'] = True
    assert not Draft202012Validator(coverage_response_format(req)['json_schema']['schema']).is_valid(data)
    with pytest.raises(InvalidEvidenceCoverage):
        validate_coverage(req,decode_coverage_selection(req,json.dumps(data)))


def test_background_role_without_witnesses_is_an_explicit_gap_not_a_failure():
    from sage.runtime.evidence_coverage import decode_coverage_selection
    req = request();data = selection(req,[])
    data['source_assessments']['public']['selection'] = {'role':'background'}
    checked = validate_coverage(req,decode_coverage_selection(req,json.dumps(data)))
    assert checked.missing_ids == ('a','b')
    complete = prepare_coverage(req.question,req.requirements,req.sources,purpose='answer_completeness')
    data['digest'] = complete.digest
    with pytest.raises(InvalidEvidenceCoverage):
        validate_coverage(complete,decode_coverage_selection(complete,json.dumps(data)))


def test_source_local_grammar_cannot_emit_cross_source_or_role_contradictions():
    pytest.importorskip('xgrammar')
    from xgrammar.testing import _is_grammar_accept_string
    from sage.runtime.structured_json import compact_json_grammar
    from sage.runtime.evidence_coverage import decode_coverage_selection
    req = request();data = selection(req,['p0'])
    raw = json.dumps(data,separators=(',',':'))
    grammar = compact_json_grammar(coverage_response_format(req)['json_schema']['schema'])
    assert _is_grammar_accept_string(grammar,raw)
    repeated = raw.replace('"p0":true','"p0":true,"p0":true')
    assert not _is_grammar_accept_string(grammar,repeated)
    with pytest.raises(InvalidEvidenceCoverage):
        validate_coverage(req,decode_coverage_selection(req,repeated))
    data['source_assessments']['public']['selection']['role'] = 'background'
    assert not _is_grammar_accept_string(grammar,json.dumps(data,separators=(',',':')))


def test_source_local_schema_is_linear_in_paragraph_count():
    sizes = []
    for count in (100,1000):
        source = PlanSource('s','Long','','\n\n'.join('Paragraph '+str(i) for i in range(count)))
        req = prepare_coverage('Question?',question_requirements('Question?'),(source,))
        sizes.append(len(json.dumps(coverage_response_format(req))))
    assert sizes[1] < sizes[0]*10 and sizes[1] < 50000


@pytest.mark.parametrize('raw',['not JSON','{"digest":"stale","items":[]}','{"unexpected":true}'])
def test_model_selection_decode_never_turns_bad_output_into_empty_valid_coverage(raw):
    from sage.runtime.evidence_coverage import decode_coverage_selection
    req = request()
    with pytest.raises(InvalidEvidenceCoverage):validate_coverage(req,decode_coverage_selection(req,raw))


def test_markdown_sections_preserve_all_conditions_and_literal_code_headings():
    from sage.runtime.evidence_coverage import decode_coverage_selection
    text = '# Tradeoff\n\nMigration can win.\n\nOnly if transfer is cheaper.\n\n```text\n# Not a heading\n```\n\n## Other case\n\nRecompute can win.\n\nWhen transfer costs more.\n'
    req = prepare_coverage('When does each win?',question_requirements('When does each win?'),(PlanSource('s','Comparison','',text),))
    ids = [p['id'] for p in json.loads(req.payload)['sources'][0]['paragraphs']]
    assert len(ids) == 2
    checked = validate_coverage(req,decode_coverage_selection(req,json.dumps(selection(req,ids))))
    assert ''.join(w.quote for w in checked.items[0].witnesses) == text


def test_coverage_purpose_is_bound_and_cannot_reuse_source_receipt():
    req = request();complete = prepare_coverage(req.question,req.requirements,req.sources,purpose='answer_completeness')
    assert req.digest != complete.digest
    with pytest.raises(InvalidEvidenceCoverage):validate_coverage(replace(complete,purpose='source_support'),'{}')
    with pytest.raises(InvalidEvidenceCoverage):prepare_coverage(req.question,req.requirements,req.sources,purpose='ignore-gaps')


def test_diagnostics_never_forward_unrecognized_model_or_exception_text():
    assert InvalidEvidenceCoverage('witness range not in authorized source').code == 'witness_out_of_range'
    assert InvalidEvidenceCoverage('secret-or-source-body').code == 'invalid_request_or_receipt'
