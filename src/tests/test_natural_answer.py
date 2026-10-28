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
    data={'digest':request.digest,'units':[{'id':0,'verdict':'supported','evidence':[{'source_id':'short','line_ids':[50]}]}]}
    with pytest.raises(ValidationError):validate(data,schema)


def test_conversation_evidence_scope_is_bound_through_workflow_and_review():
    from sage.runtime.evidence_coverage import prepare_coverage, question_requirements
    from sage.runtime.evidence_workflow import EvidenceWork, _selected_request, _audit_request, _natural_request
    from sage.runtime.source_answer_plan import SourceAnswerPlan
    dialogue = PlanSource("dialogue", "Prior turn", "", "Assistant: proposed 30%, not a measurement", kind="conversation")
    document = PlanSource("doc", "ExampleProject", "", "ExampleProject has no result here.")
    question = "What did the previous ExampleProject answer propose?"
    req = prepare_coverage(question, question_requirements(question), (document, dialogue))
    work = EvidenceWork(req, (document, dialogue), req.digest)
    selected = replace(work, request=_selected_request(work))
    assert dialogue in selected.request.sources  # Not discarded by subject-name filtering.
    work = replace(selected, plan=SourceAnswerPlan("unused", ("dialogue",), ()))
    draft = _natural_request(work)
    audit = _audit_request(work, "The previous answer proposed 30%, not a measured result.")
    assert json.loads(draft.payload)["sources"][0]["kind"] == "conversation"
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
