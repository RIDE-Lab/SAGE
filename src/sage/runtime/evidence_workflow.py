"""One native, read-only evidence coverage / repair / composition workflow.

Applications provide permission-aware retrieval and model adapters, not a second
scheduler. All decisions use immutable receipts. There is one bounded repair
round, never automatic replay of business writes or an unbounded model loop.
"""

from contextvars import ContextVar
from dataclasses import dataclass, replace

from sage.runtime.evidence_coverage import (
    CoverageAssessment,
    CoverageRequest,
    EvidenceRequirement,
    InvalidEvidenceCoverage,
    RequirementCoverage,
    missing_question_spans,
    prepare_coverage,
    question_requirements,
    repair_queries,
    validate_coverage,
)
from sage.runtime.evidence_relevance import question_identifiers, subject_candidate_indices
from sage.runtime.flownet import bootstrap_runtime, build_runtime_client, flow, stateless
from sage.runtime.research_experiments import (
    ExperimentPlan,
    prepare_experiments,
    render_experiments,
    validate_experiments,
)
from sage.runtime.source_answer_plan import (
    PlanSource,
    SourceAnswerBlock,
    SourceAnswerPlan,
    prepare_source_plan,
    render_source_plan_blocks,
)

_ports = ContextVar("sage_evidence_workflow_ports")

from sage.runtime.answer_task import AnswerTask

@dataclass(frozen=True)
class EvidenceWork:
    request: CoverageRequest
    candidates: tuple[PlanSource, ...]
    candidate_digest: str
    research: bool = False
    assessment: CoverageAssessment | None = None
    assessment_failure: str | None = None
    rejected_assessment: str | None = None
    queries: tuple[str, ...] = ()
    repair_attempted: bool = False
    plan: SourceAnswerPlan | None = None
    answer: str | None = None
    experiment_plan: ExperimentPlan | None = None


def _selected_request(work):
    # Always select against the COMPLETE candidate snapshot, not the previously
    # narrowed set. Repeated selection must not progressively erase subjects.
    # A matching identifier is a relevance hint, not an independent fact check.
    complete = prepare_coverage(work.request.question, work.request.requirements, work.candidates)
    if complete.digest != work.candidate_digest:
        raise InvalidEvidenceCoverage("authorized candidate snapshot changed")
    indices = subject_candidate_indices(question_identifiers(work.request.question), tuple(
        source.title + "\n" + source.content for source in work.candidates))
    return prepare_coverage(work.request.question, work.request.requirements,
        tuple(work.candidates[index] for index in indices))


@stateless(uri="stateless://sage/evidence/select", namespace="sage")
def select_evidence(work):
    return replace(work, request=_selected_request(work))


@stateless(uri="stateless://sage/evidence/assess", namespace="sage")
def assess(work):
    # A coverage gap is valid data. An invalid model serialization may get
    # ONE explicit native correction node, using the identical bound request.
    if not work.request.sources:
        assessment = CoverageAssessment(work.request.digest, tuple(
            RequirementCoverage(r.requirement_id, ()) for r in work.request.requirements))
    else:
        raw = _ports.get().assess(work.request)
        try:
            assessment = validate_coverage(work.request, raw)
        except InvalidEvidenceCoverage as exc:
            if exc.code == "invalid_request_or_receipt" or not callable(getattr(_ports.get(), "repair_assessment", None)):
                raise
            return replace(work, assessment=None, queries=(), assessment_failure=exc.code,
                rejected_assessment=raw)
    return replace(work, assessment=assessment, queries=repair_queries(work.request, assessment),
        assessment_failure=None, rejected_assessment=None)


@stateless(uri="stateless://sage/evidence/assessment-invalid", namespace="sage")
def assessment_invalid(work):
    return work.assessment_failure is not None


@stateless(uri="stateless://sage/evidence/assessment-valid", namespace="sage")
def assessment_valid(work):
    return work.assessment_failure is None


@stateless(uri="stateless://sage/evidence/correct-assessment", namespace="sage")
def correct_assessment(work):
    if work.assessment_failure is None or work.rejected_assessment is None:
        raise InvalidEvidenceCoverage("correction requires a rejected assessment")
    raw = _ports.get().repair_assessment(work.request, work.rejected_assessment, work.assessment_failure)
    # Never turn invalid output into empty witnesses, trust a model passed flag,
    # rebind a digest, widen sources, or recurse on a second invalid response.
    assessment = validate_coverage(work.request, raw)
    return replace(work, assessment=assessment, queries=repair_queries(work.request, assessment),
        assessment_failure=None, rejected_assessment=None)


def _assessed(stream, prefix):
    checked = stream.map(assess, operator_key=prefix)
    corrected = checked.filter(assessment_invalid, operator_key=prefix+"-invalid").map(
        correct_assessment, operator_key=prefix+"-correction")
    return corrected.connect(checked.filter(assessment_valid, operator_key=prefix+"-valid"))


@stateless(uri="stateless://sage/evidence/needs-repair", namespace="sage")
def needs_repair(work):
    return bool(work.queries)


@stateless(uri="stateless://sage/evidence/no-repair", namespace="sage")
def no_repair(work):
    return not work.queries


@stateless(uri="stateless://sage/evidence/supplement", namespace="sage")
def supplement(work):
    sources = {source.source_id: source for source in work.candidates}
    # This port is read-only and must reapply the SAME captured identity/source
    # capability filter. Model text never supplies roles or expands source scope.
    for source in _ports.get().supplement(work.queries):
        if not isinstance(source, PlanSource):
            raise InvalidEvidenceCoverage("repair adapter must return authorized source records")
        if source.source_id in sources and sources[source.source_id] != source:
            raise InvalidEvidenceCoverage("source identity changed during coverage workflow")
        sources[source.source_id] = source
    complete = prepare_coverage(work.request.question, work.request.requirements, tuple(sources.values()))
    return replace(work, candidates=complete.sources, candidate_digest=complete.digest,
        assessment=None, repair_attempted=True)


@stateless(uri="stateless://sage/evidence/compose", namespace="sage")
def compose(work):
    if work.assessment is None:
        raise InvalidEvidenceCoverage("composition requires fresh coverage")
    if not work.request.sources:
        return replace(work, answer="当前允许检索的来源不足以回答这个问题；这不表示相关事实不存在。")
    request = prepare_source_plan(work.request.question, work.request.sources, allow_inferences=False)
    # Coverage already bound the permitted facts to exact source witnesses.
    # A second model choosing document IDs could add unrelated documents,
    # even when EVERY requirement had empty witnesses. Derive the plan from
    # the validated receipt instead; ordering follows the candidate snapshot.
    used = {w.source_id for item in work.assessment.items for w in item.witnesses}
    plan = SourceAnswerPlan(request.digest,
        tuple(source.source_id for source in request.sources if source.source_id in used), ())
    if work.research:
        final_request = prepare_source_plan(work.request.question, work.request.sources, allow_inferences=True)
        plan = SourceAnswerPlan(final_request.digest, plan.source_ids, ())
    return replace(work, plan=plan)


def _research_requested(work):
    return work.research and work.plan is not None and bool(work.plan.source_ids)


@stateless(uri="stateless://sage/evidence/research-requested", namespace="sage")
def research_requested(work):
    return _research_requested(work)


@stateless(uri="stateless://sage/evidence/research-not-requested", namespace="sage")
def research_not_requested(work):
    return not _research_requested(work)


@stateless(uri="stateless://sage/evidence/plan-experiments", namespace="sage")
def plan_experiments(work):
    request = prepare_source_plan(work.request.question, work.request.sources, allow_inferences=True)
    experiments = prepare_experiments(request, work.plan.source_ids)
    plan = validate_experiments(experiments, _ports.get().plan_experiments(experiments, work.plan))
    inferences = render_experiments(experiments, plan)
    return replace(work, experiment_plan=plan,
        plan=SourceAnswerPlan(request.digest, work.plan.source_ids, inferences))


def render_evidence_work_blocks(work: EvidenceWork) -> tuple[SourceAnswerBlock, ...]:
    """Canonical lossless parts, binding coverage and final source selection."""
    if work.assessment is None:
        raise InvalidEvidenceCoverage("missing final coverage")
    if work.request != _selected_request(work):
        raise InvalidEvidenceCoverage("selected evidence differs from complete candidate snapshot")
    gaps = missing_question_spans(work.request, work.assessment)
    if work.plan is None:
        if work.request.sources:
            raise InvalidEvidenceCoverage("missing final source plan")
        parts = [SourceAnswerBlock("notice", "当前允许检索的来源不足以回答这个问题；这不表示相关事实不存在。")]
    else:
        request = prepare_source_plan(work.request.question, work.request.sources, allow_inferences=work.research)
        if work.research and work.plan.source_ids:
            if work.experiment_plan is None:
                raise InvalidEvidenceCoverage("research answer requires a structured experiment plan")
            experiments = prepare_experiments(request, work.plan.source_ids)
            if work.plan.inferences != render_experiments(experiments, work.experiment_plan):
                raise InvalidEvidenceCoverage("research prose differs from canonical experiment plan")
        used = {w.source_id for item in work.assessment.items for w in item.witnesses}
        if used != set(work.plan.source_ids):
            raise InvalidEvidenceCoverage("composition differs from mapped evidence")
        parts = list(render_source_plan_blocks(request, work.plan))
    if gaps:
        # These are original question spans, not model-authored negative claims.
        quoted = "\n".join("> " + line for query in gaps for line in query.splitlines())
        notice = ("## 来源事实边界\n\n本次来源未能逐项证实下列提问中的全部事实前提。"
            "上面的实验设计是本回答提出的候选方案，不代表项目当前实现、已有实验或验证状态；"
            "来源未写明的信息仍然未知，不能据此认定事实不存在。"
            if work.experiment_plan is not None else
            "## 仍待补充的依据\n\n本次允许检索的来源未充分覆盖以下提问部分；不能据此认定相关事实不存在。")
        parts.append(SourceAnswerBlock("notice", notice + "覆盖匹配由模型提出、原文片段由运行时核对，"
            "不等同于独立事实核验。\n\n" + quoted))
    return tuple(parts)


def render_evidence_work(work: EvidenceWork) -> str:
    return "\n\n".join(block.markdown for block in render_evidence_work_blocks(work)).strip()


@stateless(uri="stateless://sage/evidence/verify", namespace="sage")
def verify(work):
    return replace(work, answer=render_evidence_work(work))


@flow(uri="flow://sage/evidence-quality", namespace="sage")
def evidence_quality_flow(init_stream):
    checked = _assessed(init_stream.map(select_evidence, operator_key="select-evidence"), "assess-coverage")
    repaired = _assessed(checked.filter(needs_repair, operator_key="coverage-gap").map(
        supplement, operator_key="supplement-evidence").map(
        select_evidence, operator_key="select-repaired-evidence"), "reassess-coverage")
    planned = repaired.connect(checked.filter(no_repair, operator_key="coverage-ready")).map(
        compose, operator_key="compose-source-plan")
    reviewed = planned.filter(research_requested, operator_key="research-experiments").map(
        plan_experiments, operator_key="plan-experiments").connect(
        planned.filter(research_not_requested, operator_key="no-experiment-plan"))
    return reviewed.map(verify, operator_key="verify-evidence-answer")


class EvidenceWorkflowRuntime:
    def __init__(self):
        self._bootstrap = bootstrap_runtime(node_mode="local")
        try:
            client = build_runtime_client(owner="sage-evidence", runtime_host=self._bootstrap.runtime_host)
            self._endpoint = evidence_quality_flow.bind("evidence-quality-in", "evidence-quality-out").endpoint(
                client=client, reuse_existing=False)
        except BaseException:
            self._bootstrap.shutdown()
            raise

    def run(self, question: str, sources: tuple[PlanSource, ...], ports, *, timeout: float,
            requirements: tuple[EvidenceRequirement, ...] | None = None, research: bool = False,
            task: AnswerTask | None = None) -> EvidenceWork:
        if task is not None:
            task.validate(question)
            if research and not task.research_requested:
                raise ValueError("research flag contradicts factual task")
            research = task.research_requested
        request = prepare_coverage(question, requirements if requirements is not None else
            question_requirements(question), sources)
        token = _ports.set(ports)
        try:
            outputs = self._endpoint.call(EvidenceWork(request, candidates=sources,
                candidate_digest=request.digest, research=research), timeout=timeout)
        finally:
            _ports.reset(token)
        if len(outputs) != 1:
            raise InvalidEvidenceCoverage("invalid evidence workflow output count")
        return outputs[0]

    def close(self):
        self._endpoint.close()
        self._bootstrap.shutdown()
