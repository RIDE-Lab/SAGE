import pytest

from sage.runtime.adaptive_workflow import (
    WorkflowCapabilities,
    WorkflowRequirements,
    plan_workflow,
)


def test_public_and_member_research_have_different_real_sources():
    requirements = WorkflowRequirements(research_review=True)
    guest = plan_workflow(requirements, WorkflowCapabilities("guest", ("wiki",), False))
    member = plan_workflow(requirements, WorkflowCapabilities("account", ("wiki", "private"), True))
    assert guest.route == member.route == "research"
    assert guest.quality == member.quality == "research"
    assert guest.sources == ("wiki",) and not guest.retrieve_memory
    assert member.sources == ("wiki", "private") and member.retrieve_memory


@pytest.mark.parametrize("requested,expected", [(None, ("wiki",)), ((), ()),
                                               (("private",), ()), (("wiki", "private"), ("wiki",))])
def test_requested_origins_only_narrow_capability(requested, expected):
    plan = plan_workflow(WorkflowRequirements(requested_sources=requested),
                         WorkflowCapabilities("guest", ("wiki",), False))
    assert plan.sources == expected
    assert plan.retrieve_knowledge == bool(expected)


@pytest.mark.parametrize("action", ["book_meeting", "review_queue", "human_handoff"])
def test_business_routes_do_not_read_irrelevant_evidence(action):
    plan = plan_workflow(WorkflowRequirements(action=action, research_review=True, web=True),
                         WorkflowCapabilities("account", ("wiki", "private"), True))
    assert plan.route == action and not plan.needs_retrieval
    assert not plan.write_knowledge


def test_write_requires_explicit_capability_not_identity_label():
    with pytest.raises(PermissionError):
        plan_workflow(WorkflowRequirements(action="admin_add_knowledge"),
                      WorkflowCapabilities("admin", ("wiki",), True))
    plan = plan_workflow(WorkflowRequirements(action="admin_add_knowledge"),
                         WorkflowCapabilities("admin", ("wiki",), True, write_knowledge=True))
    assert plan.write_knowledge and not plan.needs_retrieval


@pytest.mark.parametrize("requirements,route", [
    (WorkflowRequirements(resolved=True), "resolved"),
    (WorkflowRequirements(self_contained=True), "self_contained"),
    (WorkflowRequirements(knowledge=False), "contextual"),
])
def test_fast_routes_skip_unneeded_work(requirements, route):
    plan = plan_workflow(requirements, WorkflowCapabilities("guest", ("wiki",), True))
    assert plan.route == route
    assert not plan.retrieve_knowledge
    if route != "contextual":
        assert not plan.needs_retrieval


def test_unknown_action_fails_closed():
    with pytest.raises(ValueError):
        plan_workflow(WorkflowRequirements(action="grant_admin"),
                      WorkflowCapabilities("guest", ("wiki",), False))


def test_explicit_source_contract_is_distinct_from_opportunistic_retrieval():
    caps = WorkflowCapabilities("account", ("wiki",), True)
    contextual = plan_workflow(WorkflowRequirements(), caps)
    explicit = plan_workflow(WorkflowRequirements(requested_sources=("wiki",)), caps)
    assert contextual.retrieve_knowledge and not contextual.evidence_required
    assert explicit.retrieve_knowledge and explicit.evidence_required
    # A denied source constraint must stay evidence-required, not turn into an
    # unconstrained factual generation when the permitted source set is empty.
    denied = plan_workflow(WorkflowRequirements(requested_sources=("private",)), caps)
    assert denied.sources == () and not denied.retrieve_knowledge
    assert denied.evidence_required and denied.quality == "source_grounded"
    resolved = plan_workflow(WorkflowRequirements(requested_sources=("wiki",), resolved=True), caps)
    assert not resolved.evidence_required


def test_native_verification_budget_does_not_depend_on_user_deep_generation_toggle():
    from sage.runtime.adaptive_workflow import execution_budget_seconds
    assert execution_budget_seconds(standard=80, extended=240) == 80
    assert execution_budget_seconds(standard=80, extended=240, requires_native_verification=True) == 240
    assert execution_budget_seconds(standard=80, extended=240, explicit_deep_reasoning=True) == 240
    for values in ({'standard': 0, 'extended': 240}, {'standard': 80, 'extended': 40},
                   {'standard': 80, 'extended': float('inf')}):
        with pytest.raises(ValueError):execution_budget_seconds(**values)
