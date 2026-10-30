"""One native, read-only evidence coverage / repair / composition workflow.

Applications provide permission-aware retrieval and model adapters, not a second
scheduler. All decisions use immutable receipts. There is one bounded repair
round, never automatic replay of business writes or an unbounded model loop.
"""

from contextvars import ContextVar
from dataclasses import dataclass, replace

from sage.runtime.answer_task import AnswerTask
from sage.runtime.coverage_partition import assess_source_groups
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
from sage.runtime.natural_answer import (
    NaturalAnswerReceipt,
    audit_failures,
    prepare_natural_answer,
    validate_natural_answer,
)
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
from sage.runtime.source_audit import SourceAuditRejected, AuditProtocolRejected, prepare_source_audit

_ports = ContextVar("sage_evidence_workflow_ports")


@dataclass(frozen=True)
class EvidenceWork:
    request: CoverageRequest
    candidates: tuple[PlanSource, ...]
    candidate_digest: str
    research: bool = False
    experiments_requested: bool = False
    assessment: CoverageAssessment | None = None
    assessment_failure: str | None = None
    rejected_assessment: str | None = None
    queries: tuple[str, ...] = ()
    repair_attempted: bool = False
    plan: SourceAnswerPlan | None = None
    answer: str | None = None
    experiment_plan: ExperimentPlan | None = None
    natural: NaturalAnswerReceipt | None = None
    draft: str | None = None
    answer_failures: tuple[str, ...] = ()
    rejected_audit: str | None = None
    audit_failure: str | None = None
    final_audit_required: bool = False
    final_audit: str | None = None


def _selected_request(work):
    # Always select against the COMPLETE candidate snapshot, not the previously
    # narrowed set. Repeated selection must not progressively erase subjects.
    # A matching identifier is a relevance hint, not an independent fact check.
    complete = prepare_coverage(work.request.question, work.request.requirements, work.candidates, public_context=work.request.public_context)
    if complete.digest != work.candidate_digest:
        raise InvalidEvidenceCoverage("authorized candidate snapshot changed")
    indices = subject_candidate_indices(question_identifiers(work.request.question), tuple(
        source.title + "\n" + source.content for source in work.candidates))
    return prepare_coverage(work.request.question, work.request.requirements,
        tuple(source for index, source in enumerate(work.candidates)
              if index in indices or source.kind == "conversation"), public_context=work.request.public_context)


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
        raw = assess_source_groups(work.request, _ports.get().assess,
            getattr(_ports.get(), "repair_assessment", None))
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


@stateless(uri="stateless://sage/evidence/plan-retrieval-repair", namespace="sage")
def plan_retrieval_repair(work):
    planner = getattr(_ports.get(), "plan_retrieval_repair", None)
    if planner is None:
        return work
    from sage.runtime.evidence_query_plan import validate_query_plan
    raw = planner(work.request, work.assessment)
    return replace(work, queries=validate_query_plan(work.request, work.assessment, raw))


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
    complete = prepare_coverage(work.request.question, work.request.requirements, tuple(sources.values()), public_context=work.request.public_context)
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
    used = _answer_source_ids(work)
    plan = SourceAnswerPlan(request.digest,
        tuple(source.source_id for source in request.sources if source.source_id in used), ())
    if work.research:
        final_request = prepare_source_plan(work.request.question, work.request.sources, allow_inferences=True)
        plan = SourceAnswerPlan(final_request.digest, plan.source_ids, ())
    return replace(work, plan=plan)


def _natural_request(work):
    used = set(work.plan.source_ids)
    return prepare_natural_answer(work.request.question,
        tuple(s for s in work.request.sources if s.source_id in used),
        coverage_request=work.request, assessment=work.assessment)


def _audit_request(work, answer):
    used = set(work.plan.source_ids)
    return prepare_source_audit(work.request.question, answer,
        {s.source_id: s.content for s in work.request.sources if s.source_id in used},
        source_kinds={s.source_id: s.kind for s in work.request.sources if s.source_id in used},
        public_context=work.request.public_context)


def _answer_coverage_request(work, answer):
    return prepare_coverage(work.request.question, work.request.requirements,
        (PlanSource("answer", "Answer", "", answer),), purpose="answer_completeness", public_context=work.request.public_context)


def _review_failures(work, receipt):
    if receipt.request_digest != _natural_request(work).digest:
        raise SourceAuditRejected("stale natural answer receipt")
    failed = tuple("unit:" + str(i) for i in audit_failures(_audit_request(work, receipt.answer), receipt.audit))
    coverage = validate_coverage(_answer_coverage_request(work, receipt.answer), receipt.coverage)
    # Preserve every aspect that the authorized source assessment could cover.
    missing = set(coverage.missing_ids) - set(work.assessment.missing_ids)
    return failed + tuple("question:" + i for i in sorted(missing))


@stateless(uri="stateless://sage/evidence/write-answer", namespace="sage")
def write_answer(work):
    if work.plan is None or not work.plan.source_ids:
        return work
    request = _natural_request(work)
    answer = validate_natural_answer(request, _ports.get().write_answer(request, work.draft, work.answer_failures))
    return replace(work, draft=answer, natural=None, answer_failures=(), final_audit=None)


def _review_receipt(work, audit):
    receipt = NaturalAnswerReceipt(_natural_request(work).digest, work.draft, audit,
        _ports.get().assess(_answer_coverage_request(work, work.draft)))
    return replace(work, natural=receipt, answer_failures=_review_failures(work, receipt),
                   rejected_audit=None, audit_failure=None)


@stateless(uri="stateless://sage/evidence/review-answer", namespace="sage")
def review_answer(work):
    if work.draft is None:
        return work
    request = _audit_request(work, work.draft)
    try:
        audit = _ports.get().audit_answer(request)
    except AuditProtocolRejected as exc:
        if exc.digest != request.digest or not callable(getattr(_ports.get(), "repair_audit", None)):
            raise SourceAuditRejected("unrepairable audit receipt") from exc
        return replace(work, natural=None, rejected_audit=exc.response, audit_failure=exc.code)
    return _review_receipt(work, audit)


@stateless(uri="stateless://sage/evidence/audit-invalid", namespace="sage")
def audit_invalid(work):
    return work.audit_failure is not None


@stateless(uri="stateless://sage/evidence/audit-valid", namespace="sage")
def audit_valid(work):
    return work.audit_failure is None


@stateless(uri="stateless://sage/evidence/correct-audit", namespace="sage")
def correct_audit(work):
    if work.draft is None or work.audit_failure is None or work.rejected_audit is None:
        raise SourceAuditRejected("audit correction requires a rejected receipt")
    # Same draft, question and full source snapshot. No retry of generation,
    # retrieval or business actions. A second invalid receipt propagates.
    try:
        audit = _ports.get().repair_audit(_audit_request(work, work.draft),
                                         work.rejected_audit, work.audit_failure)
    except AuditProtocolRejected as exc:
        raise SourceAuditRejected("audit correction failed validation") from exc
    return _review_receipt(work, audit)


def _reviewed(stream, prefix):
    checked = stream.map(review_answer, operator_key=prefix)
    corrected = checked.filter(audit_invalid, operator_key=prefix+"-invalid").map(
        correct_audit, operator_key=prefix+"-correct-audit")
    return corrected.connect(checked.filter(audit_valid, operator_key=prefix+"-valid"))


@stateless(uri="stateless://sage/evidence/answer-rejected", namespace="sage")
def answer_rejected(work):
    return bool(work.answer_failures)


@stateless(uri="stateless://sage/evidence/answer-accepted", namespace="sage")
def answer_accepted(work):
    return not work.answer_failures



@stateless(uri="stateless://sage/evidence/needs-final-audit", namespace="sage")
def needs_final_audit(work):
    return work.final_audit_required and work.draft is not None


@stateless(uri="stateless://sage/evidence/no-final-audit", namespace="sage")
def no_final_audit(work):
    return not (work.final_audit_required and work.draft is not None)


@stateless(uri="stateless://sage/evidence/final-audit", namespace="sage")
def final_audit(work):
    # Fast reviews may guide a single bounded revision, but cannot authorize
    # publication under this policy. Audit EVERY unit of the final draft once,
    # with the same complete immutable sources, not just previously failed units.
    if work.natural is None or work.draft != work.natural.answer:
        raise SourceAuditRejected("final audit requires a bound reviewed draft")
    try:
        audit = _ports.get().final_audit_answer(_audit_request(work, work.draft))
    except AuditProtocolRejected as exc:
        raise SourceAuditRejected("final audit receipt failed validation") from exc
    receipt = replace(work.natural, audit=audit)
    failures = _review_failures(work, receipt)
    if failures:
        raise SourceAuditRejected("final answer failed source or completeness review")
    return replace(work, natural=receipt, final_audit=audit, answer_failures=())


def _answer_source_ids(work):
    # Retain all relevant evidence selected by the bound assessment. A short
    # source can answer one aspect while losing the comparison's other cases.
    return {w.source_id for item in work.assessment.items for w in item.witnesses}


def _research_requested(work):
    return work.experiments_requested and work.plan is not None and bool(work.plan.source_ids)


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
        if work.experiments_requested and work.plan.source_ids:
            if work.experiment_plan is None:
                raise InvalidEvidenceCoverage("research answer requires a structured experiment plan")
            experiments = prepare_experiments(request, work.plan.source_ids)
            if work.plan.inferences != render_experiments(experiments, work.experiment_plan):
                raise InvalidEvidenceCoverage("research prose differs from canonical experiment plan")
        used = _answer_source_ids(work)
        if used != set(work.plan.source_ids):
            raise InvalidEvidenceCoverage("composition differs from mapped evidence")
        if work.plan.source_ids:
            if work.natural is None or _review_failures(work, work.natural):
                raise SourceAuditRejected("natural answer failed source or completeness review")
            if work.draft != work.natural.answer:
                raise SourceAuditRejected("answer changed after review")
            if work.final_audit_required and (work.final_audit is None or work.final_audit != work.natural.audit):
                raise SourceAuditRejected("missing bound final source audit")
            parts = [SourceAnswerBlock("answer", work.natural.answer)]
            if work.plan.inferences:
                parts.extend(b for b in render_source_plan_blocks(request, work.plan) if b.kind == "inference")
        else:
            parts = [SourceAnswerBlock("notice", "当前允许检索的来源不足以回答这个问题；这不表示相关事实不存在。")]
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
        plan_retrieval_repair, operator_key="plan-retrieval-repair").map(
        supplement, operator_key="supplement-evidence").map(
        select_evidence, operator_key="select-repaired-evidence"), "reassess-coverage")
    planned = repaired.connect(checked.filter(no_repair, operator_key="coverage-ready")).map(
        compose, operator_key="compose-source-plan")
    reviewed = planned.filter(research_requested, operator_key="research-experiments").map(
        plan_experiments, operator_key="plan-experiments").connect(
        planned.filter(research_not_requested, operator_key="no-experiment-plan"))
    drafted = _reviewed(reviewed.map(write_answer, operator_key="write-natural-answer"), "review-natural-answer")
    corrected = _reviewed(drafted.filter(answer_rejected, operator_key="answer-needs-correction").map(
        write_answer, operator_key="correct-natural-answer"), "review-corrected-answer")
    prepared = corrected.connect(drafted.filter(answer_accepted, operator_key="answer-review-passed"))
    final = prepared.filter(needs_final_audit, operator_key="requires-final-source-audit").map(
        final_audit, operator_key="final-source-audit")
    return final.connect(prepared.filter(no_final_audit, operator_key="no-final-source-audit")).map(
        verify, operator_key="verify-evidence-answer")


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
            task: AnswerTask | None = None, public_context: str = "") -> EvidenceWork:
        if task is not None:
            task.validate(question)
            if research and not task.research_requested:
                raise ValueError("research flag contradicts factual task")
            research = task.research_requested
        request = prepare_coverage(question, requirements if requirements is not None else
            question_requirements(question), sources, public_context=public_context)
        final_required = getattr(ports, "requires_final_audit", False)
        if type(final_required) is not bool or (final_required and not callable(getattr(ports, "final_audit_answer", None))):
            raise ValueError("final audit policy requires an explicit audit adapter")
        token = _ports.set(ports)
        try:
            outputs = self._endpoint.call(EvidenceWork(request, candidates=sources,
                candidate_digest=request.digest, research=research, final_audit_required=final_required,
                experiments_requested=research if task is None else task.experiments_requested), timeout=timeout)
        finally:
            _ports.reset(token)
        if len(outputs) != 1:
            raise InvalidEvidenceCoverage("invalid evidence workflow output count")
        return outputs[0]

    def close(self):
        self._endpoint.close()
        self._bootstrap.shutdown()
