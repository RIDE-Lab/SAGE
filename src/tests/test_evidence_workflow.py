import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Barrier

import pytest

from sage.runtime.evidence_coverage import EvidenceRequirement, InvalidEvidenceCoverage
from sage.runtime.answer_task import AnswerTask
from sage.runtime.evidence_workflow import (
    EvidenceWorkflowRuntime,
    render_evidence_work,
    render_evidence_work_blocks,
)
from sage.runtime.execution_observation import ExecutionObserver
from sage.runtime.flownet import (
    FlowRequestOutcomeError,
    bootstrap_runtime,
    build_runtime_client,
    flow,
    stateless,
)
from sage.runtime.source_answer_plan import PlanSource

SOURCE = PlanSource("a", "A", "https://example.org/a", "A owns state.")
SUPPLEMENT = PlanSource("b", "B", "https://example.org/b", "B composes extensions.\n\n- complete\n  - nested")


def test_factual_task_never_calls_experiment_port_for_research_nouns():
    runtime, ports = EvidenceWorkflowRuntime(), Ports()
    try:
        result = runtime.run("What experiments and research questions does A report?", (SOURCE,),
                             ports, timeout=5, task=AnswerTask("facts"))
        assert not result.research and result.experiment_plan is None
    finally:
        runtime.close()


def test_conflicting_legacy_flag_is_rejected_before_any_work():
    runtime = object.__new__(EvidenceWorkflowRuntime)
    with pytest.raises(ValueError, match="contradicts"):
        runtime.run("What does A report?", (), None, timeout=5,
                    research=True, task=AnswerTask("facts"))


class Ports:
    def __init__(self, *, missing=False, no_evidence=False, omit=False):
        self.calls = []
        self.missing = missing
        self.no_evidence = no_evidence
        self.omit = omit

    def assess(self, request):
        self.calls.append(("assess", request.digest))
        return json.dumps({"digest": request.digest, "items": [{"requirement_id": r.requirement_id,
            "witnesses": [] if self.no_evidence or self.missing and request.sources[0].source_id != "answer" and (not any(call[0] == "supplement" for call in self.calls) or request.question == "A and B?" and len(request.sources) < 2) else [
                {"source_id": s.source_id, "start_line": 1, "end_line": len(s.content.splitlines())} for s in request.sources]}
            for r in request.requirements]})

    def supplement(self, queries):
        self.calls.append(("supplement", queries))
        return () if self.no_evidence else (SUPPLEMENT,)

    def compose(self, request, assessment):
        self.calls.append(("compose", request.digest))
        return json.dumps({"digest": request.digest,
            "source_ids": [] if self.omit else [s.source_id for s in request.sources],
            "inferences": []})

    def write_answer(self, request, rejected=None, failures=()):
        data = json.loads(request.payload)
        # Deterministic test prose; real paraphrase/entailment has live acceptance.
        text = "\n\n".join(s["content"] for s in data["sources"])
        import re
        text = re.sub(r"\[([^]]+)\]\(https?://[^)]+\)", r"\1", text)
        return json.dumps({"digest": request.digest, "paragraphs": [text]})

    def audit_answer(self, request):
        return json.dumps({"digest": request.digest, "units": [
            {"id": i, "verdict": "supported", "evidence": [{"source_id": request.sources[0][0], "line_ids": [0]}]}
            for i in range(len(request.units))]})

    def plan_experiments(self, request, plan):
        self.calls.append(("experiments", request.digest))
        common = {"mechanism": "mechanism", "control_value": "off", "treatment_value": "on",
            "shared_conditions": {"model":"synthetic", "hardware":"synthetic", "workload":"same workload",
                "software":"pinned", "precision":"bf16", "warmup_operations":0, "replay_seed":42}, "fault_events": []}
        return json.dumps({"digest": request.digest, "experiments": [
            {**common, "kind": "invariant", "invariant": "unique ownership"},
            {**common, "kind": "performance", "metric": "QPS", "direction": "higher",
                "threshold_gain": 0.2, "confidence": 0.95, "target_power": 0.8,
                "sampling_unit": "paired_replay"}]})


def test_named_subject_selection_excludes_unrelated_long_manual_without_losing_provenance():
    runtime, ports, events = EvidenceWorkflowRuntime(), Ports(), []
    wanted = replace(SOURCE, source_id="overview", title="Wiki | ExampleState and ABC",
        content="ExampleState manages state. ABC composes extensions.")
    manual = replace(SOURCE, source_id="manual", title="Wiki | unrelated manual", content="unrelated text\n"*25000)
    original = ports.assess
    def assess_selected(request):
        assert request.sources == (wanted,) or request.sources[0].source_id == "answer"
        assert "unrelated text" not in request.payload
        return original(request)
    ports.assess = assess_selected
    try:
        with ExecutionObserver(events.append).bind():
            result = runtime.run("ExampleState 和 ABC 分别是什么？", (wanted, manual), ports, timeout=5)
        assert result.candidates == (wanted, manual)
        assert result.request.sources == (wanted,)
        assert wanted.content in result.answer and manual.content not in result.answer
        assert any(e.operator_key == "select-evidence" and e.state == "completed" for e in events)
        for changed in ((wanted,), (wanted, replace(manual, content="replacement"))):
            with pytest.raises(InvalidEvidenceCoverage, match="candidate snapshot changed"):
                render_evidence_work(replace(result, candidates=changed))
    finally:
        runtime.close()


def test_repair_reselects_against_complete_candidates_and_stable_ids():
    runtime, ports = EvidenceWorkflowRuntime(), Ports(missing=True)
    wrong = replace(SOURCE, source_id="manual", content="generic methodology")
    found = replace(SUPPLEMENT, source_id="specific", title="ExampleState", content="ExampleState owns state.")
    def supplement(queries):
        ports.calls.append(("supplement", queries))
        return (found,)
    ports.supplement = supplement
    try:
        result = runtime.run("ExampleState?", (wrong,), ports, timeout=5)
        assert result.candidates == (wrong, found)
        assert result.request.sources == (found,)
        assert result.request.sources[0].source_id == "specific"
        assert found.content in result.answer and wrong.content not in result.answer
        assert render_evidence_work(result) == result.answer
    finally:
        runtime.close()


@pytest.mark.parametrize("missing", [False, True])
def test_only_evidence_gaps_execute_one_native_supplement_and_reassessment(missing):
    runtime, ports, events = EvidenceWorkflowRuntime(), Ports(missing=missing), []
    try:
        with ExecutionObserver(events.append).bind():
            result = runtime.run("A and B?", (SOURCE,), ports, timeout=5)
        starts = [e.operator_key for e in events if e.state == "started"]
        assert starts.count("supplement-evidence") == int(missing)
        assert starts.count("reassess-coverage") == int(missing)
        assert starts.count("verify-evidence-answer") == 1
        assert starts.count("plan-experiments") == 0
        assert result.repair_attempted is missing
        assert result.assessment.missing_ids == ()
        assert SOURCE.content in result.answer
        assert (SUPPLEMENT.content in result.answer) is missing
        assert render_evidence_work(result) == result.answer
        assert "\n\n".join(block.markdown for block in render_evidence_work_blocks(result)).strip() == result.answer
        assert runtime._endpoint._requests == {}
    finally:
        runtime.close()


def test_unresolved_after_one_repair_is_not_hidden_or_claimed_false():
    runtime, ports = EvidenceWorkflowRuntime(), Ports(no_evidence=True)
    try:
        result = runtime.run("Does B exist?", (SOURCE,), ports, timeout=5)
        assert [c[0] for c in ports.calls] == ["assess", "supplement", "assess"]
        assert result.assessment.missing_ids == ("question",)
        assert "仍待补充的依据" in result.answer and "> Does B exist?" in result.answer
        assert "不能据此认定相关事实不存在" in result.answer
        blocks = render_evidence_work_blocks(result)
        assert blocks[-1].kind == "notice" and blocks[-1].source_url is None
        assert "\n\n".join(b.markdown for b in blocks).strip() == result.answer
    finally:
        runtime.close()


def test_research_distinguishes_source_unknowns_from_generated_experimental_design():
    runtime = EvidenceWorkflowRuntime()
    try:
        result = runtime.run("Review A", (SOURCE,), Ports(no_evidence=True), timeout=3, research=True)
        assert result.experiment_plan is None
        assert "仍待补充的依据" in result.answer
        assert SOURCE.content not in result.answer
        assert "一次可复现的不变量违反" not in result.answer
    finally:
        runtime.close()


def test_research_activates_actual_experiment_plan_and_formal_checks_not_a_route_label():
    runtime, ports, events = EvidenceWorkflowRuntime(), Ports(), []
    try:
        with ExecutionObserver(events.append).bind():
            result = runtime.run("Propose an experiment about A", (SOURCE,), ports, timeout=5, research=True)
        assert [c[0] for c in ports.calls] == ["assess", "experiments", "assess"]
        assert any(e.operator_key == "plan-experiments" and e.state == "completed" for e in events)
        assert "单侧置信上界低于" in result.answer and SOURCE.content in result.answer
        assert "一次可复现的不变量违反" in result.answer
        with pytest.raises(InvalidEvidenceCoverage):
            render_evidence_work(replace(result, plan=replace(result.plan, inferences=("arbitrary verdict",))))
    finally:
        runtime.close()


def test_source_coverage_may_not_be_discarded_by_composition_and_failure_recovers():
    runtime = EvidenceWorkflowRuntime()
    try:
        result = runtime.run("A?", (SOURCE,), Ports(), timeout=3)
        assert SOURCE.content in result.answer
        with pytest.raises(InvalidEvidenceCoverage, match="differs from mapped evidence"):
            render_evidence_work(replace(result, plan=replace(result.plan, source_ids=())))
        assert runtime._endpoint._requests == {}
        with pytest.raises(InvalidEvidenceCoverage):
            render_evidence_work(replace(result, request=replace(result.request, digest="stale")))
    finally:
        runtime.close()


def test_empty_retrieval_can_repair_without_inventing_a_source():
    runtime = EvidenceWorkflowRuntime()
    try:
        found = runtime.run("B?", (), Ports(missing=True), timeout=3,
            requirements=(EvidenceRequirement("all", 0, 2),))
        assert found.repair_attempted and SUPPLEMENT.content in found.answer
        assert found.assessment.missing_ids == ()
        missing = runtime.run("B?", (), Ports(no_evidence=True), timeout=3)
        assert missing.plan is None and "不存在" in missing.answer
    finally:
        runtime.close()


def test_repair_cannot_rebind_existing_source_identity():
    runtime, ports = EvidenceWorkflowRuntime(), Ports(missing=True)
    ports.supplement = lambda queries: (replace(SOURCE, content="changed source"),)
    try:
        with pytest.raises(FlowRequestOutcomeError):
            runtime.run("A?", (SOURCE,), ports, timeout=3)
    finally:
        runtime.close()


def test_shared_runtime_keeps_concurrent_model_and_retrieval_ports_scoped():
    runtime, barrier = EvidenceWorkflowRuntime(), Barrier(2)
    try:
        def call(index):
            source = replace(SOURCE, source_id=f"source-{index}", content=f"Authorized data {index}")
            ports = Ports()
            original = ports.assess
            def assess(request):
                barrier.wait(3)
                assert request.sources == (source,) or request.sources[0].source_id == "answer"
                return original(request)
            ports.assess = assess
            return runtime.run(f"Question {index}", (source,), ports, timeout=5)
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(call, range(4)))
        for index, result in enumerate(results):
            assert f"Authorized data {index}" in result.answer
            assert result.request.sources[0].source_id == f"source-{index}"
        assert runtime._endpoint._requests == {}
    finally:
        runtime.close()


def test_research_plan_cannot_replace_factual_sources():
    runtime, ports = EvidenceWorkflowRuntime(), Ports()
    ports.plan_experiments = lambda request, plan: json.dumps({"digest": request.digest,
        "source_ids": [], "inferences": []})
    try:
        with pytest.raises(FlowRequestOutcomeError):
            runtime.run("Review A", (SOURCE,), ports, timeout=3, research=True)
    finally:
        runtime.close()


@stateless(uri="stateless://test/coverage-parent", namespace="test")
def parent_quality(payload):
    runtime, ports = payload
    return runtime.run("A?", (SOURCE,), ports, timeout=3)


@flow(uri="flow://test/coverage-parent", namespace="test")
def parent_quality_flow(init_stream):
    return init_stream.map(parent_quality)


def test_nested_quality_failure_preserves_leaf_type_for_durable_classification():
    bootstrap, runtime, ports = bootstrap_runtime(node_mode="local"), EvidenceWorkflowRuntime(), Ports()
    ports.assess = lambda request: "invalid JSON"
    endpoint = parent_quality_flow.bind("test-quality-in", "test-quality-out").endpoint(
        client=build_runtime_client(owner="test", runtime_host=bootstrap.runtime_host), reuse_existing=False)
    try:
        with pytest.raises(FlowRequestOutcomeError) as caught:
            endpoint.call((runtime, ports), timeout=5)
        assert caught.value.error_type == "InvalidEvidenceCoverage"
        assert "invalid coverage JSON" in caught.value.message
        assert endpoint._requests == {} and runtime._endpoint._requests == {}
    finally:
        endpoint.close()
        runtime.close()
        bootstrap.shutdown()


def test_invalid_mapping_gets_one_visible_strict_correction():
    class Correctable(Ports):
        def assess(self, request):
            if request.sources[0].source_id == 'answer':
                return Ports.assess(self, request)
            self.calls.append(('initial-invalid', request.digest))
            return '{not json'
        def repair_assessment(self, request, rejected, code):
            self.calls.append(('correct', request.digest))
            assert rejected == '{not json' and code == 'invalid_json'
            return Ports.assess(self, request)
    runtime, ports, events = EvidenceWorkflowRuntime(), Correctable(), []
    try:
        with ExecutionObserver(events.append, trace_id='correct-coverage').bind():
            result = runtime.run('Explain A?', (SOURCE,), ports, timeout=5)
        assert result.assessment and result.rejected_assessment is None
        assert sum(kind == 'correct' for kind, _ in ports.calls) == 1
        assert any(e.operator_key == 'assess-coverage-correction' for e in events)
        assert SOURCE.content in render_evidence_work(result)
    finally:
        runtime.close()


@pytest.mark.parametrize('bad', ['{}', '{not json', json.dumps({'digest':'other','items':[]})])
def test_invalid_correction_fails_closed_without_recursion_or_composition(bad):
    class BadCorrection(Ports):
        corrections = 0
        def assess(self, request): return '{}'
        def repair_assessment(self, request, rejected, code):
            self.corrections += 1
            return bad
        def compose(self, *args):
            pytest.fail('invalid mapping cannot reach composition')
    runtime, ports = EvidenceWorkflowRuntime(), BadCorrection()
    try:
        with pytest.raises(FlowRequestOutcomeError) as caught:
            runtime.run('Explain A?', (SOURCE,), ports, timeout=5)
        assert caught.value.error_type == 'InvalidEvidenceCoverage'
        assert ports.corrections == 1
    finally:
        runtime.close()


def test_source_mutation_during_correction_is_not_admitted():
    class Mutating(Ports):
        def assess(self, request): return '{}'
        def repair_assessment(self, request, rejected, code):
            changed = replace(request.sources[0], content='Forged facts.')
            object.__setattr__(request, 'sources', (changed,))
            return Ports.assess(self, request)
        def compose(self, *args): pytest.fail('changed snapshot cannot reach composition')
    runtime = EvidenceWorkflowRuntime()
    try:
        with pytest.raises(FlowRequestOutcomeError) as caught:
            runtime.run('Explain A?', (SOURCE,), Mutating(), timeout=5)
        assert caught.value.error_type == 'InvalidEvidenceCoverage'
    finally:
        runtime.close()


def test_empty_witnesses_cannot_publish_an_unrelated_document():
    ports, runtime = Ports(no_evidence=True), EvidenceWorkflowRuntime()
    ports.compose = lambda *args: pytest.fail('a model must not override coverage source selection')
    try:
        result = runtime.run('How is a response generated?', (SOURCE,), ports, timeout=5)
        assert result.plan.source_ids == ()
        assert SOURCE.content not in result.answer
        assert result.assessment.missing_ids == ('question',)
        forged = replace(result, plan=replace(result.plan, source_ids=(SOURCE.source_id,)))
        with pytest.raises(InvalidEvidenceCoverage, match='differs from mapped evidence'):
            render_evidence_work(forged)
    finally:
        runtime.close()


def test_complementary_evidence_is_not_reduced_to_the_shortest_source():
    short = replace(SOURCE, source_id='short', content='Migration helps if transfer costs less than rebuilding.')
    long = replace(SUPPLEMENT, source_id='long', content='Recomputation may be cheaper if reuse is unlikely. Include contention and waiting.')
    runtime, events = EvidenceWorkflowRuntime(), []
    try:
        with ExecutionObserver(events.append).bind():
            result = runtime.run('When is each alternative cheaper?', (long,short), Ports(), timeout=5)
        assert result.request.sources == (long,short)
        assert result.plan.source_ids == ('long','short')
        assert long.content in result.answer and short.content in result.answer
        assert not any(e.operator_key=='minimize-evidence' for e in events)
    finally:
        runtime.close()


def test_invalid_answer_completeness_assessment_is_not_silently_accepted():
    class BadFocus(Ports):
        def assess(self, request):
            return 'invalid' if len(request.sources)==1 else super().assess(request)
    runtime = EvidenceWorkflowRuntime()
    try:
        with pytest.raises(FlowRequestOutcomeError):
            runtime.run('What do A and B do?', (SOURCE,SUPPLEMENT), BadFocus(), timeout=5)
    finally:
        runtime.close()


def test_research_overview_does_not_run_experiments_without_design_request():
    runtime, ports, events = EvidenceWorkflowRuntime(), Ports(), []
    try:
        with ExecutionObserver(events.append).bind():
            result = runtime.run("A有哪些值得验证的研究问题？", (SOURCE,), ports, timeout=5,
                task=AnswerTask("research", "有哪些值得验证的研究问题"))
        assert result.research and not result.experiments_requested
        assert result.experiment_plan is None and result.plan.inferences == ()
        assert not any(e.operator_key=="plan-experiments" and e.state=="started" for e in events)
        assert "非来源事实" not in result.answer
        result = runtime.run("为 A 设计对照实验", (SOURCE,), Ports(), timeout=5,
            task=AnswerTask("research", "设计对照实验", "设计对照实验"))
        assert result.experiment_plan is not None
    finally:
        runtime.close()


def test_missing_coverage_plans_search_inside_native_flow_and_revalidates_sources():
    class PlannedPorts(Ports):
        def plan_retrieval_repair(self, request, assessment):
            self.calls.append(('query_plan', request.digest))
            return json.dumps({'digest': request.digest, 'queries': ['B extension composition']})
    ports = PlannedPorts(missing=True)
    runtime = EvidenceWorkflowRuntime()
    events = []
    try:
        with ExecutionObserver(events.append).bind():
            result = runtime.run('A and B?', (SOURCE,), ports, timeout=10, task=AnswerTask('facts'))
        assert ('supplement', ('A and B?', 'B extension composition')) in ports.calls
        assert sum(c[0] == 'query_plan' for c in ports.calls) == 1
        assert any(e.operator_key == 'plan-retrieval-repair' and e.state == 'completed' for e in events)
        assert result.assessment.missing_ids == ()
        assert len(result.candidates) == 2
    finally:
        runtime.close()
