import json
import pytest
from sage.runtime.coverage_partition import assess_source_groups
from sage.runtime.evidence_coverage import prepare_coverage, question_requirements, validate_coverage, InvalidEvidenceCoverage
from sage.runtime.source_answer_plan import PlanSource
from sage.runtime.model_errors import ModelRequestRejected

def request(n=4, purpose='source_support'):
    question='Explain A. Explain B.'
    return prepare_coverage(question,question_requirements(question),tuple(
        PlanSource(str(i),str(i),'https://example.org/'+str(i),'full text\nlast line\n') for i in range(n)),purpose=purpose)

def mapping(r):
    return json.dumps({'digest':r.digest,'items':[{'requirement_id':q.requirement_id,'witnesses':[
        {'source_id':s.source_id,'start_line':1,'end_line':2} for s in r.sources]} for q in r.requirements]})

def test_capacity_groups_preserve_all_documents_questions_and_witnesses():
    parent=request();calls=[]
    def assess(r):
        calls.append(r)
        assert r.question==parent.question and r.requirements==parent.requirements
        assert all(s in parent.sources for s in r.sources)
        if len(r.sources)>1:raise ModelRequestRejected('prompt_capacity')
        return mapping(r)
    receipt=validate_coverage(parent,assess_source_groups(parent,assess))
    assert len(calls)==7
    assert all([w.source_id for w in item.witnesses]==['0','1','2','3'] for item in receipt.items)

@pytest.mark.parametrize('error',[ModelRequestRejected('model_access_denied'),TimeoutError()])
def test_non_capacity_errors_never_replayed(error):
    calls=[]
    def assess(r):calls.append(r);raise error
    with pytest.raises(type(error)):assess_source_groups(request(),assess)
    assert len(calls)==1

@pytest.mark.parametrize('r',[request(1),request(2,'answer_completeness')])
def test_no_document_or_answer_truncation(r):
    calls=[]
    def assess(q):calls.append(q);raise ModelRequestRejected('prompt_capacity')
    with pytest.raises(ModelRequestRejected):assess_source_groups(r,assess)
    assert len(calls)==1

def test_child_receipt_cannot_forge_other_group_or_parent_digest():
    parent=request(2)
    def assess(r):
        if len(r.sources)>1:raise ModelRequestRejected('prompt_capacity')
        return mapping(parent)
    with pytest.raises(InvalidEvidenceCoverage):assess_source_groups(parent,assess)

def test_invalid_normal_mapping_keeps_existing_correction_path():
    assert assess_source_groups(request(),lambda r:'invalid')=='invalid'
