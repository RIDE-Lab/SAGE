import json
from dataclasses import replace
import pytest
from sage.runtime.natural_answer import (
    prepare_natural_answer, validate_natural_answer, audit_failures, audit_response_format,
)
from sage.runtime.source_audit import SourceAuditRejected, prepare_source_audit
from sage.runtime.source_answer_plan import PlanSource
from sage.runtime.evidence_workflow import EvidenceWorkflowRuntime, render_evidence_work
from sage.runtime.execution_observation import ExecutionObserver
from sage.runtime.flownet import FlowRequestOutcomeError

SOURCE = PlanSource('s', 'Status', 'https://example.org/full', 'The page does not display measurements.\nThe project is ongoing.')
GOOD = 'The project is ongoing; these sources do not establish whether measurements were performed.'
BAD = 'The authors did not perform measurements.'

class Ports:
    def __init__(self, failure=None):
        self.failure, self.writes, self.audits, self.coverages = failure, 0, 0, 0
    def assess(self, request):
        is_answer = request.sources[0].source_id == 'answer'
        self.coverages += int(is_answer)
        missing = is_answer and self.failure == 'incomplete' and self.coverages == 1
        return json.dumps({'digest':request.digest,'items':[{'requirement_id':r.requirement_id,
            'witnesses': [] if missing else [{'source_id':request.sources[0].source_id,'start_line':1,'end_line':1}]}
            for r in request.requirements]})
    def supplement(self, queries):
        raise AssertionError('source coverage complete')
    def write_answer(self, request, rejected, failures):
        self.writes += 1
        if self.writes == 2:
            assert rejected and failures
        return json.dumps({'digest':request.digest,'paragraphs': [BAD if self.failure in ('unsupported','always-bad') and self.writes == 1 else GOOD]})
    def audit_answer(self, request):
        self.audits += 1
        bad = self.failure == 'always-bad' or BAD in request.units
        return json.dumps({'digest':request.digest,'units':[{'id':i,'verdict':'unsupported' if bad else 'supported',
            'evidence':[] if bad else [{'source_id':'s','line_ids':[0,1]}]} for i in range(len(request.units))]})

@pytest.mark.parametrize('failure',[None,'unsupported','incomplete'])
def test_natural_answer_is_reviewed_and_repair_is_visible_and_bounded(failure):
    ports, events = Ports(failure), []
    runtime = EvidenceWorkflowRuntime()
    try:
        with ExecutionObserver(events.append).bind():
            result = runtime.run('What is known about measurements?', (SOURCE,), ports, timeout=5)
        assert result.answer == GOOD
        assert SOURCE.content not in result.answer and SOURCE.url not in result.answer
        assert ports.writes == ports.audits == (2 if failure else 1)
        assert sum(e.state=='started' and e.operator_key=='correct-natural-answer' for e in events)==int(bool(failure))
        assert render_evidence_work(result) == GOOD
        for corrupted in (replace(result, draft=BAD), replace(result,natural=replace(result.natural,answer=BAD)),
                          replace(result,natural=replace(result.natural,request_digest='stale'))):
            with pytest.raises((SourceAuditRejected,ValueError)):
                render_evidence_work(corrupted)
    finally:
        runtime.close()

def test_second_semantic_failure_never_publishes_or_falls_back_to_raw_documents():
    runtime, ports = EvidenceWorkflowRuntime(), Ports('always-bad')
    try:
        with pytest.raises(FlowRequestOutcomeError) as err:
            runtime.run('What is known about measurements?',(SOURCE,),ports,timeout=5)
        assert err.value.error_type == 'SourceAuditRejected'
        assert ports.writes == ports.audits == 2
    finally:
        runtime.close()

def test_model_cannot_exempt_substantive_claim_as_nonfactual():
    request=prepare_source_audit('Status?',BAD,{'s':SOURCE.content})
    raw=json.dumps({'digest':request.digest,'units':[{'id':0,'verdict':'nonfactual','evidence':[]}]})
    assert audit_failures(request,raw)==(0,)

@pytest.mark.parametrize('mutation',['extra','stale','duplicate','empty','link'])
def test_invalid_or_navigation_body_rejected(mutation):
    request=prepare_natural_answer('Status?',(SOURCE,))
    data={'digest':request.digest,'paragraphs':[GOOD]}
    if mutation=='extra':data['passed']=True
    if mutation=='stale':data['digest']='stale'
    if mutation=='empty':data['paragraphs']=[' ']
    if mutation=='link':data['paragraphs']=['See https://example.org/full']
    raw=json.dumps(data)
    if mutation=='duplicate':raw=raw[:-1]+',"paragraphs":["overridden"]}'
    with pytest.raises(SourceAuditRejected):validate_natural_answer(request,raw)

def test_audit_schema_binds_each_sources_own_line_ids():
    from jsonschema import validate, ValidationError
    request=prepare_source_audit('Status?',GOOD,{'short':'one','long':'line\n'*100})
    schema=audit_response_format(request)['json_schema']['schema']
    data={'digest':request.digest,'units':[{'id':0,'verdict':'supported','checked_sources':[{'source_id':'short','block_ids':['p0']}]}]}
    with pytest.raises(ValidationError):validate(data,schema)


def test_conversation_evidence_scope_is_bound_through_workflow_and_review():
    from sage.runtime.evidence_coverage import prepare_coverage, question_requirements, validate_coverage
    from sage.runtime.evidence_workflow import EvidenceWork, _selected_request, _audit_request, _natural_request
    from sage.runtime.source_answer_plan import SourceAnswerPlan
    dialogue = PlanSource("dialogue", "Prior turn", "", "Assistant: proposed 30%, not a measurement", kind="conversation")
    document = PlanSource("doc", "ExampleProject", "", "ExampleProject has no result here.")
    question = "What did the previous ExampleProject answer propose?"
    req = prepare_coverage(question, question_requirements(question), (document, dialogue))
    work = EvidenceWork(req, (document, dialogue), req.digest)
    selected = replace(work, request=_selected_request(work))
    assert dialogue in selected.request.sources  # Not discarded by subject-name filtering.
    receipt = validate_coverage(selected.request, json.dumps({'digest':selected.request.digest,
        'items':[{'requirement_id':r.requirement_id, 'witnesses':[{
            'source_id':'dialogue','start_line':1,'end_line':1}]} for r in selected.request.requirements]}))
    work = replace(selected, assessment=receipt, plan=SourceAnswerPlan("unused", ("dialogue",), ()))
    draft = _natural_request(work)
    audit = _audit_request(work, "The previous answer proposed 30%, not a measured result.")
    assert json.loads(draft.payload)["sources"][0]["kind"] == "conversation"
    assert json.loads(draft.payload)['evidence_plan'][0]['passages'][0] == {'source_id':'dialogue','start_line':1,'end_line':1}
    assert json.loads(audit.payload)["sources"][0]["kind"] == "conversation"
    untyped = prepare_source_audit(question, "The previous answer proposed 30%, not a measured result.", {"dialogue": dialogue.content})
    assert untyped.digest != audit.digest
    changed = replace(dialogue, kind="document")
    assert prepare_coverage(question, req.requirements, (document, changed)).digest != req.digest
    with pytest.raises(ValueError):
        prepare_source_audit(question, "Answer", {"dialogue":dialogue.content}, source_kinds={"missing":"conversation"})


def test_repair_feedback_resolves_sentence_ids_not_paragraph_indices():
    from sage.runtime.natural_answer import natural_answer_payload
    request = prepare_natural_answer("What is known?", (SOURCE,))
    rejected = "项目仍在进行。作者从未测量。\n\n来源没有展示数据。"
    payload = json.loads(natural_answer_payload(request, rejected, ("unit:1", "question:question")))
    assert payload["failed_units"] == [{"id": 1, "text": "作者从未测量。", "failure": "not_entailed_by_sources"}]
    assert payload["missing_requirement_ids"] == ["question"]
    assert payload["digest"] == request.digest
    assert payload["sources"] == json.loads(request.payload)["sources"]
    assert natural_answer_payload(request) == request.payload
    for failures in (("unit:9",), ("unit:-1",), ("bogus",)):
        with pytest.raises(ValueError):
            natural_answer_payload(request, rejected, failures)


def test_answer_focus_preserves_validated_passages_and_complete_sources_without_promoting_tampering():
    from sage.runtime.evidence_coverage import prepare_coverage, question_requirements, validate_coverage, InvalidEvidenceCoverage
    question = 'What is the status?'
    req = prepare_coverage(question, question_requirements(question), (SOURCE,))
    assessment = validate_coverage(req, json.dumps({'digest': req.digest, 'items':[
        {'requirement_id':'question', 'witnesses':[{'source_id':'s','start_line':2,'end_line':2}]}]}))
    answer = prepare_natural_answer(question, (SOURCE,), coverage_request=req, assessment=assessment)
    data = json.loads(answer.payload)
    assert data['sources'][0]['content'] == SOURCE.content
    assert data['evidence_plan'] == [{'question_part': question, 'passages':[
        {'source_id':'s','start_line':2,'end_line':2}]}]
    assert answer.digest != prepare_natural_answer(question,(SOURCE,)).digest
    witness = assessment.items[0].witnesses[0]
    forged = replace(assessment, items=(replace(assessment.items[0], witnesses=(replace(witness,quote='Invented result'),)),))
    with pytest.raises(InvalidEvidenceCoverage):
        prepare_natural_answer(question,(SOURCE,),coverage_request=req,assessment=forged)
    with pytest.raises(ValueError, match='supporting source'):
        prepare_natural_answer(question,(),coverage_request=req,assessment=assessment)
    with pytest.raises(ValueError, match='snapshot'):
        prepare_natural_answer(question,(replace(SOURCE,content='changed'),),coverage_request=req,assessment=assessment)
    with pytest.raises(ValueError, match='matching question'):
        prepare_natural_answer('another question',(SOURCE,),coverage_request=req,assessment=assessment)
    with pytest.raises(ValueError, match='matching question'):
        prepare_natural_answer(question,(SOURCE,),coverage_request=replace(req,purpose='answer_completeness'),assessment=assessment)


def test_single_line_audit_schema_cannot_force_duplicate_line_references():
    from jsonschema import validate, ValidationError
    request = prepare_source_audit('Status?', 'It is ongoing.', {'one':'Ongoing.'})
    schema = audit_response_format(request)['json_schema']['schema']
    raw = {'digest':request.digest,'units':[{'id':0,'verdict':'supported',
        'checked_sources':[{'source_id':'one','block_ids':['p0','p0']}]}]}
    with pytest.raises(ValidationError):
        validate(raw, schema)
    raw['units'][0]['checked_sources'][0]['block_ids'] = ['p0']
    validate(raw, schema)
    from sage.runtime.source_audit import decode_audit_blocks
    assert audit_failures(request, decode_audit_blocks(request, json.dumps(raw))) == ()


@pytest.mark.parametrize('failure', [None, 'twice', 'stale', 'transport', 'unsupported'])
def test_audit_wire_repair_is_visible_bound_and_never_restarts_generation(failure):
    from sage.runtime.source_audit import AuditProtocolRejected
    class WirePorts(Ports):
        repairs = 0
        original_request = None
        def audit_answer(self, request):
            self.original_request = request
            if failure == 'transport':
                raise TimeoutError('transport failure')
            raise AuditProtocolRejected('wrong' if failure == 'stale' else request.digest,
                                        'untrusted malformed receipt', 'overlap')
        def repair_audit(self, request, rejected, failure_code):
            self.repairs += 1
            assert request == self.original_request
            assert rejected == 'untrusted malformed receipt' and failure_code == 'overlap'
            if failure == 'twice':
                raise AuditProtocolRejected(request.digest, 'still invalid', 'overlap')
            if failure == 'unsupported':
                return json.dumps({'digest':request.digest, 'units':[
                    {'id':i,'verdict':'unsupported','evidence':[]} for i in range(len(request.units))]})
            return super().audit_answer(request)
    runtime, ports, events = EvidenceWorkflowRuntime(), WirePorts(), []
    try:
        with ExecutionObserver(events.append).bind():
            if failure:
                with pytest.raises(FlowRequestOutcomeError) as error:
                    runtime.run('What is known about measurements?', (SOURCE,), ports, timeout=5)
                assert error.value.error_type == ("TimeoutError" if failure == "transport" else "SourceAuditRejected")
            else:
                assert runtime.run('What is known about measurements?', (SOURCE,), ports, timeout=5).answer == GOOD
        assert ports.writes == (2 if failure == 'unsupported' else 1)
        assert ports.repairs == (0 if failure in ('stale','transport') else 2 if failure == 'unsupported' else 1)
        assert sum(e.state == 'started' and e.operator_key == 'review-natural-answer-correct-audit' for e in events) == int(failure not in ('stale','transport'))
    finally:
        runtime.close()


def test_public_subject_context_survives_all_evidence_stages_without_becoming_a_source():
    class ScopedPorts(Ports):
        def assess(self, request):
            assert request.public_context == 'Computer systems'
            assert request.question == 'What is known about measurements?'
            assert json.loads(request.payload)['public_context'] == 'Computer systems'
            return super().assess(request)
        def write_answer(self, request, rejected, failures):
            assert json.loads(request.payload)['public_context'] == 'Computer systems'
            return super().write_answer(request, rejected, failures)
        def audit_answer(self, request):
            data = json.loads(request.payload)
            assert data['public_context'] == 'Computer systems'
            assert {s['id'] for s in data['sources']} == {'s'}
            return super().audit_answer(request)
    runtime = EvidenceWorkflowRuntime()
    try:
        work = runtime.run('What is known about measurements?', (SOURCE,), ScopedPorts(),
                           timeout=5, public_context='Computer systems')
        assert work.answer == GOOD
        from sage.runtime.evidence_coverage import InvalidEvidenceCoverage
        with pytest.raises(InvalidEvidenceCoverage):
            render_evidence_work(replace(work,request=replace(work.request,public_context='changed')))
    finally:
        runtime.close()


def test_bound_revision_changes_only_rejected_sentence_and_preserves_layout():
    from sage.runtime.natural_answer import (prepare_natural_revision, natural_revision_response_format,
                                           apply_natural_revision)
    request = prepare_natural_answer('How?', (SOURCE,))
    answer = 'Stable sentence. Unsupported guarantee.\n\n- Keep the value 1.5.\n  Keep this too.'
    revision = prepare_natural_revision(request, answer, ('unit:1',))
    schema = natural_revision_response_format(revision)['json_schema']['schema']
    assert schema['properties']['replacements']['required'] == ['1']
    assert schema['properties']['additional_paragraphs']['maxItems'] == 0
    raw = json.dumps({'digest': revision.digest, 'replacements': {'1': None}, 'additional_paragraphs': []})
    result = validate_natural_answer(request, apply_natural_revision(request, revision, raw))
    assert result == 'Stable sentence.\n\n- Keep the value 1.5.\n  Keep this too.'
    # A repeated sentence is addressed by its position, never global replace.
    repeated = 'Same. Same. Same.'
    revision = prepare_natural_revision(request, repeated, ('unit:1',))
    raw = json.dumps({'digest': revision.digest, 'replacements': {'1': ' Changed.'}, 'additional_paragraphs': []})
    assert validate_natural_answer(request, apply_natural_revision(request, revision, raw)) == 'Same. Changed. Same.'


def test_revision_rejects_stale_missing_extra_and_unchanged_edits():
    from dataclasses import replace
    from sage.runtime.natural_answer import prepare_natural_revision, apply_natural_revision
    request = prepare_natural_answer('How?', (SOURCE,))
    revision = prepare_natural_revision(request, 'Fact. Guarantee.', ('unit:1',))
    base = {'digest': revision.digest, 'replacements': {'1': ' Qualified.'}, 'additional_paragraphs': []}
    for change in ({'digest': 'other'}, {'replacements': {}}, {'replacements': {'0': 'Changed'}},
                   {'replacements': {'1': 'Guarantee.'}}, {'replacements': {'1': ''}},
                   {'additional_paragraphs': ['Extra claim.']}):
        with pytest.raises(SourceAuditRejected):
            apply_natural_revision(request, revision, json.dumps({**base, **change}))
    altered = replace(revision, payload=revision.payload.replace('Guarantee.', 'Other.'))
    with pytest.raises(SourceAuditRejected): apply_natural_revision(request, altered, json.dumps(base))
    other = prepare_natural_answer('Another question?', (SOURCE,))
    with pytest.raises(SourceAuditRejected): apply_natural_revision(other, revision, json.dumps(base))


def test_revision_can_complete_missing_part_but_does_not_bypass_source_audit():
    from sage.runtime.natural_answer import prepare_natural_revision, apply_natural_revision
    request = prepare_natural_answer('How?', (SOURCE,))
    revision = prepare_natural_revision(request, 'Fact.', ('question:question',))
    raw = json.dumps({'digest': revision.digest, 'replacements': {}, 'additional_paragraphs': ['New claim.']})
    # Applying a proposal is separate from acceptance; runtime still reviews it.
    assert validate_natural_answer(request, apply_natural_revision(request, revision, raw)) == 'Fact.\n\nNew claim.'


def test_audit_decoding_selects_evidence_before_verdict():
    request = prepare_source_audit('Status?', GOOD, {'s': SOURCE.content})
    fields = audit_response_format(request)['json_schema']['schema']['properties']['units']['items']['properties']
    assert list(fields) == ['id', 'checked_sources', 'verdict']
