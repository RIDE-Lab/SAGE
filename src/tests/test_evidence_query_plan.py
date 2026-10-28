import json
import pytest
from sage.runtime.evidence_coverage import prepare_coverage, question_requirements, CoverageAssessment, RequirementCoverage
from sage.runtime.evidence_query_plan import validate_query_plan, query_plan_format, query_plan_payload


def fixture():
    question = '怎样区分系统优化的真实收益和实验干扰？'
    request = prepare_coverage(question, question_requirements(question), ())
    assessment = CoverageAssessment(request.digest, (RequirementCoverage('question', ()),))
    return request, assessment


def test_query_plan_preserves_missing_question_and_cannot_authorize_sources():
    request, assessment = fixture()
    raw = json.dumps({'digest': request.digest, 'queries': ['性能测量 控制变量 重复实验', '性能测量 控制变量 重复实验']})
    queries = validate_query_plan(request, assessment, raw)
    assert queries == (request.question, '性能测量 控制变量 重复实验')
    assert json.loads(query_plan_payload(request, assessment))['missing_parts'] == [request.question]
    assert query_plan_format(request)['json_schema']['schema']['additionalProperties'] is False


@pytest.mark.parametrize('changes', [
    {'digest': 'stale'}, {'queries': ['']}, {'queries': ['x']*4},
    {'queries': 'query'}, {'queries': ['x'*257]}, {'sources': ['private']},
])
def test_invalid_query_plan_rejected(changes):
    request, assessment = fixture()
    raw = {'digest': request.digest, 'queries': []}
    raw.update(changes)
    with pytest.raises(ValueError):
        validate_query_plan(request, assessment, json.dumps(raw))
