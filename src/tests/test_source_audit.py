"""Protocol tests; semantic auditor qualification is a separate acceptance gate."""

import json

import pytest
from sage.runtime.source_audit import (
    InvalidSourceAudit,
    prepare_source_audit,
    validate_source_audit,
)


def fixture():
    request = prepare_source_audit(
        "状态？", "仍在研究。\n尚未做基准。", {"wiki": "仍在研究。"}
    )
    response = {
        "digest": request.digest,
        "units": [
            {
                "id": 0,
                "verdict": "supported",
                "evidence": [{"source_id": "wiki", "line_ids": [0]}],
            },
            {"id": 1, "verdict": "unsupported", "evidence": []},
        ],
    }
    return request, response


def test_supported_quote_does_not_override_unsupported_unit():
    request, response = fixture()
    result = validate_source_audit(request, json.dumps(response))
    assert not result.passed
    assert result.unsupported_units == (1,)


def test_complete_supported_answer_passes():
    request = prepare_source_audit("状态？", "仍在研究。", {"wiki": "仍在研究。"})
    _, response = fixture()
    response["digest"] = request.digest
    response["units"].pop()
    assert validate_source_audit(request, json.dumps(response)).passed


@pytest.mark.parametrize(
    "mutation",
    [
        "omitted",
        "duplicate",
        "boolean_id",
        "stale",
        "invented_line",
        "invented_source",
        "empty_lines",
        "boolean_line",
        "duplicate_line",
        "legacy_quote",
        "empty_evidence",
        "unknown_verdict",
        "extra_field",
        "wrong_type",
        "nonfactual_with_evidence",
    ],
)
def test_invalid_audits_fail_closed(mutation):
    request, response = fixture()
    unit = response["units"][0]
    if mutation == "omitted":
        response["units"].pop()
    elif mutation == "duplicate":
        response["units"].append(unit)
    elif mutation == "boolean_id":
        unit["id"] = False
    elif mutation == "stale":
        response["digest"] = "other answer"
    elif mutation == "invented_line":
        unit["evidence"][0]["line_ids"] = [999]
    elif mutation == "invented_source":
        unit["evidence"][0]["source_id"] = "private-not-authorized"
    elif mutation == "empty_lines":
        unit["evidence"][0]["line_ids"] = []
    elif mutation == "boolean_line":
        unit["evidence"][0]["line_ids"] = [False]
    elif mutation == "duplicate_line":
        unit["evidence"][0]["line_ids"] = [0, 0]
    elif mutation == "legacy_quote":
        unit["evidence"][0] = {"source_id": "wiki", "quote": "仍在研究。"}
    elif mutation == "empty_evidence":
        unit["evidence"] = []
    elif mutation == "unknown_verdict":
        unit["verdict"] = "probably"
    elif mutation == "extra_field":
        unit["confidence"] = 1
    elif mutation == "wrong_type":
        response["units"] = {}
    elif mutation == "nonfactual_with_evidence":
        unit["verdict"] = "nonfactual"
    with pytest.raises(InvalidSourceAudit):
        validate_source_audit(request, json.dumps(response))


@pytest.mark.parametrize(
    "response", ["OK", "{}", "[]", "null", '{"digest":1,"digest":2}']
)
def test_malformed_response_is_not_a_pass(response):
    request, _ = fixture()
    with pytest.raises(InvalidSourceAudit):
        validate_source_audit(request, response)


def test_snapshot_preserves_full_sources_and_binds_question_answer_evidence():
    sources = {"wiki": "x" * 20000 + " https://example.org/full/path/"}
    request = prepare_source_audit("question", "# heading\n\nanswer", sources)
    assert "\n".join(line["text"] for line in json.loads(request.payload)["sources"][0]["lines"]) == sources["wiki"]
    assert request.units == ("# heading", "answer")
    assert (
        prepare_source_audit("other", "# heading\n\nanswer", sources).digest
        != request.digest
    )
    assert prepare_source_audit("question", "changed", sources).digest != request.digest
    sources["wiki"] = "changed"
    assert request.sources[0][1].startswith("x" * 20000)
    assert (
        prepare_source_audit("question", "# heading\n\nanswer", sources).digest
        != request.digest
    )


@pytest.mark.parametrize(
    "answer,sources", [("", {"a": "b"}), ("answer", {}), ("answer", {"a": ""})]
)
def test_empty_inputs_rejected(answer, sources):
    with pytest.raises(ValueError):
        prepare_source_audit("question", answer, sources)


def test_evidence_lines_are_lossless_and_blank_lines_cannot_support_claims():
    source = "# 标题\n\n仍在**研究**，不是性能结论。\n"
    request = prepare_source_audit("状态", "仍在研究。", {"wiki": source})
    lines = json.loads(request.payload)["sources"][0]["lines"]
    assert [line["id"] for line in lines] == [0, 1, 2, 3]
    assert "\n".join(line["text"] for line in lines) == source
    response = {"digest": request.digest, "units": [{
        "id": 0, "verdict": "supported", "evidence": [{"source_id": "wiki", "line_ids": [2]}],
    }]}
    assert validate_source_audit(request, json.dumps(response)).passed
    response["units"][0]["evidence"][0]["line_ids"] = [1, 3]
    with pytest.raises(InvalidSourceAudit):
        validate_source_audit(request, json.dumps(response))


def test_supported_sentence_cannot_mask_appended_inference_or_split_urls():
    answer = "资料提醒设计不等于收益。这意味着目前只是理论。来源 https://lab.example.org/a/b/。"
    request = prepare_source_audit("状态", answer, {"wiki": "设计不等于收益。"})
    assert request.units == (
        "资料提醒设计不等于收益。", "这意味着目前只是理论。", "来源 https://lab.example.org/a/b/。",
    )
    assert "".join(request.units) == answer


def test_numbered_steps_are_not_split_into_spurious_numeric_claims():
    request = prepare_source_audit("Steps?", "1. Process input.\n2. Generate output.", {"s":"Process input then generate output."})
    assert request.units == ("1. Process input.", "2. Generate output.")
    bad = prepare_source_audit("Status?", "1. Still researching. No tests exist.", {"s":"Still researching."})
    assert bad.units == ("1. Still researching.", " No tests exist.")



def block_fixture():
    request = prepare_source_audit('Compare?', 'One claim. Another claim.',
        {'a': '# First\n' + '\n'.join(f'line {i}' for i in range(300)) + '\n# Last\nTail', 'b': 'Only one line'})
    data = {'digest': request.digest, 'units': [
        {'id': 0, 'verdict': 'supported', 'checked_sources': [{'source_id': 'a', 'block_ids': ['p0','p1']}]},
        {'id': 1, 'verdict': 'unsupported', 'checked_sources': []}]}
    return request, data


def test_block_audit_is_lossless_and_keeps_unsupported_verdict():
    from sage.runtime.source_audit import decode_audit_blocks, block_audit_payload
    request, data = block_fixture()
    before = json.loads(request.payload)
    wire = json.loads(block_audit_payload(request))
    for old, new in zip(before['sources'], wire['sources']):
        assert old['lines'] == new['lines'] and old['kind'] == new['kind']
    decoded = decode_audit_blocks(request, json.dumps(data))
    assert json.loads(decoded)['units'][0]['evidence'][0]['line_ids'] == list(range(303))
    assert validate_source_audit(request, decoded).unsupported_units == (1,)
    assert len(json.dumps(data)) < len(decoded)


@pytest.mark.parametrize('mutation', ['duplicate', 'unknown_block', 'unknown_source',
    'wrong_source', 'duplicate_source', 'missing_unit', 'stale', 'old_wire', 'empty', 'extra', 'nonstring'])
def test_block_audit_rejects_invalid_receipts(mutation):
    from sage.runtime.source_audit import decode_audit_blocks
    request, data = block_fixture()
    ref = data['units'][0]['checked_sources'][0]
    if mutation == 'duplicate': ref['block_ids'].append('p0')
    if mutation == 'unknown_block': ref['block_ids'] = ['p999']
    if mutation == 'unknown_source': ref['source_id'] = 'missing'
    if mutation == 'wrong_source': ref['source_id'] = 'b'
    if mutation == 'duplicate_source': data['units'][0]['checked_sources'].append(ref.copy())
    if mutation == 'missing_unit': data['units'].pop()
    if mutation == 'stale': data['digest'] = 'stale'
    if mutation == 'old_wire': ref.pop('block_ids'); ref['line_ids'] = [0]
    if mutation == 'empty': ref['block_ids'] = []
    if mutation == 'extra': ref['reason'] = 'trust me'
    if mutation == 'nonstring': ref['block_ids'] = [True]
    with pytest.raises(InvalidSourceAudit): decode_audit_blocks(request, json.dumps(data))


def test_block_audit_rejects_duplicate_json_fields():
    from sage.runtime.source_audit import decode_audit_blocks
    request, data = block_fixture()
    raw = json.dumps(data).replace('"block_ids":', '"block_ids": [], "block_ids":')
    with pytest.raises(InvalidSourceAudit): decode_audit_blocks(request, raw)


@pytest.mark.parametrize('text', ['one\u2028two\nthree', 'one\rtwo\nthree', 'one\r\n\r\ntwo\n', '# Section\n\nOne.\n'])
def test_audit_block_endpoints_use_the_canonical_newline_convention(text):
    from sage.runtime.source_audit import audit_blocks, block_audit_payload, decode_audit_blocks
    request = prepare_source_audit('Status?', 'A claim.', {'s': text})
    choices = audit_blocks(request)
    data = {'digest': request.digest, 'units': [{'id':0,'verdict':'supported',
        'checked_sources':[{'source_id':'s','block_ids':list(choices)}]}]}
    result = json.loads(decode_audit_blocks(request,json.dumps(data)))
    ids = result['units'][0]['evidence'][0]['line_ids']
    lines = json.loads(block_audit_payload(request))['sources'][0]['lines']
    assert all(0 <= i < len(lines) for i in ids)
    assert {i for i,l in enumerate(text.split('\n')) if l.strip()}.issubset(ids)


def test_checked_counterevidence_never_turns_rejection_into_support():
    from sage.runtime.source_audit import decode_audit_blocks
    request, data = block_fixture()
    data['units'][1]['checked_sources'] = [{'source_id':'a','block_ids':['p0']}]
    decoded = decode_audit_blocks(request, json.dumps(data))
    assert validate_source_audit(request, decoded).unsupported_units == (1,)
    assert json.loads(decoded)['units'][1]['evidence'] == []
    data['units'][1]['checked_sources'][0]['block_ids'] = ['unknown']
    with pytest.raises(InvalidSourceAudit): decode_audit_blocks(request, json.dumps(data))
