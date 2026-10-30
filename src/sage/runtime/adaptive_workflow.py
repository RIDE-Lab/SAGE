"""Deterministic, capability-bounded execution planning for assistant flows.

Adapters supply verified capabilities, not model-proposed roles. A plan only
narrows those capabilities; retrieval adapters still enforce document ACLs.
It is an execution contract, not a claim that evidence or an answer is correct.
"""

from dataclasses import dataclass
from typing import Literal
from math import isfinite


def execution_budget_seconds(*, standard, extended, requires_native_verification=False,
                             explicit_deep_reasoning=False):
    """Allocate the host's declared budget from actual execution requirements.

    Publication-grade native verification is backend work even when the user
    disables deep answer generation. This does not change either quality gate,
    retry count or model output allowance. Hosts must use this same decision for
    cancellation and timing reports; it is not an unbounded deadline extension.
    """
    if (not isfinite(standard) or not isfinite(extended) or standard <= 0
            or extended < standard or type(requires_native_verification) is not bool
            or type(explicit_deep_reasoning) is not bool):
        raise ValueError("invalid workflow execution budget")
    return extended if requires_native_verification or explicit_deep_reasoning else standard


@dataclass(frozen=True)
class WorkflowCapabilities:
    identity: Literal["guest", "account", "api_token", "slack", "admin"]
    sources: tuple[str, ...]
    memory: bool
    write_knowledge: bool = False

    def __post_init__(self):
        if self.identity not in {"guest", "account", "api_token", "slack", "admin"}:
            raise ValueError("unknown verified identity kind")
        if len(self.sources) != len(set(self.sources)) or any(not s for s in self.sources):
            raise ValueError("invalid source capabilities")


@dataclass(frozen=True)
class WorkflowRequirements:
    action: str = "answer"
    resolved: bool = False
    self_contained: bool = False
    research_review: bool = False
    knowledge: bool = True
    memory: bool = True
    web: bool = False
    requested_sources: tuple[str, ...] | None = None


@dataclass(frozen=True)
class AdaptiveWorkflowPlan:
    route: str
    identity: str
    sources: tuple[str, ...]
    retrieve_knowledge: bool
    retrieve_memory: bool
    search_web: bool
    quality: Literal["direct", "standard", "source_grounded", "research"]
    write_knowledge: bool
    evidence_required: bool = False

    @property
    def needs_retrieval(self) -> bool:
        return self.retrieve_knowledge or self.retrieve_memory or self.search_web


def plan_workflow(
    requirements: WorkflowRequirements, capabilities: WorkflowCapabilities,
) -> AdaptiveWorkflowPlan:
    """Select real work without accepting authorization from problem text.

    None means all authorized origins; an explicit empty source set means none.
    A research request cannot widen permissions. Resolved/business turns do not
    pay for irrelevant retrieval. Capability denial is explicit, never fallback.
    """
    action = requirements.action
    if action not in {"answer", "ask_followup", "book_meeting", "admin_add_knowledge",
                      "review_queue", "human_handoff"}:
        raise ValueError("unsupported workflow action")
    if action == "admin_add_knowledge" and not capabilities.write_knowledge:
        raise PermissionError("knowledge-write capability required")
    selected = tuple(s for s in capabilities.sources if
                     requirements.requested_sources is None or s in requirements.requested_sources)
    business = action in {"book_meeting", "admin_add_knowledge", "review_queue", "human_handoff"}
    active = not requirements.resolved and not business
    evidence_required = (active and requirements.knowledge and not requirements.self_contained
                         and requirements.requested_sources is not None)
    knowledge = active and requirements.knowledge and not requirements.self_contained and bool(selected)
    memory = active and requirements.memory and not requirements.self_contained and capabilities.memory
    web = active and requirements.web
    sources = selected if knowledge else ()
    quality = ("direct" if not active else "research" if requirements.research_review
               else "source_grounded" if knowledge or evidence_required else "standard")
    route = ("resolved" if requirements.resolved else action if business else
             "research" if requirements.research_review else "evidence" if knowledge or web or evidence_required
             else "contextual" if memory else "self_contained")
    return AdaptiveWorkflowPlan(route, capabilities.identity, sources, knowledge, memory, web,
                                quality, action == "admin_add_knowledge", evidence_required)
