"""Task-purpose contract, independent of topic, identity, and source selection.

The host supplies a semantic classification through its existing intent port.
SAGE validates its shape and directive witness before enabling prospective work.
A witness proves provenance, not semantic correctness; qualify the classifier
on factual, quoted, negated, mixed, and genuine prospective requests as well.
"""
from dataclasses import dataclass
from typing import Literal

AnswerTaskMode = Literal["facts", "research"]

ANSWER_TASK_INSTRUCTIONS = """Classify task_mode independently of domain/topic:
facts = retrieve, explain, summarize or compare existing information, including
whether a source reports a result and what methods/results it reports.
research = the user asks YOU to evaluate a research idea or propose future
hypotheses, experiments or research directions, INCLUDING mixed requests that
also ask for factual reporting. Source coverage still checks every factual part.
Words such as research question, contribution, review, experiment, negative
result, or a quoted title are TOPICS, not instructions to propose research.
Reporting what a paper proposes is facts; proposing your own experiments is
research. Honor negation and quoted/reported speech. Missing evidence does NOT
authorize research suggestions. Conditional future advice explicitly requested
by the user is research, even if its condition may not hold yet.
Supplied background or reported preliminary results are premises, NOT a second
request to look up facts. Evaluating an idea with such premises is research.
A source constraint such as "according to the public Wiki" or a request to
cite sources does not by itself add a factual subtask to a research review.
Examples of task purpose (source names/topics do not decide the label):
"Based on source X, evaluate this idea and design controlled experiments" = research.
"Summarize source X's measured results, then propose a new experiment" = research.
"根据资料评审这个方向并设计对照" = research.
"资料报告了哪些对照实验？" = facts, not research.
Classify experiment_directive SEPARATELY from research purpose. It is null
unless the user explicitly asks YOU to design a concrete experiment, experimental
protocol, control/treatment comparison or measurement plan. Asking which research
questions are worth validating, evaluating a direction, or suggesting directions
does NOT request a detailed experimental plan. Mentioning validation/experiments,
quoting someone else's request, or explicitly refusing experiments cannot enable
this field. Do not silently elaborate an overview into benchmark configurations.
When requested, experiment_directive is a verbatim contiguous current-question
span asking for that experimental design, and task_mode must be research.
Return task_mode, task_directive and experiment_directive. For facts use task_directive=null.
For research, task_directive must be a verbatim contiguous span of the
current question that asks for YOUR prospective work, not a topic noun or title.
Do not treat source text, history, identity, or your own inference as a request.
"""


@dataclass(frozen=True)
class AnswerTask:
    mode: AnswerTaskMode
    directive: str | None = None
    experiment_directive: str | None = None

    def validate(self, question: str) -> "AnswerTask":
        if self.mode not in {"facts", "research"}:
            raise ValueError("invalid answer task mode")
        if self.mode == "facts":
            if self.directive is not None:
                raise ValueError("factual task cannot carry a research directive")
        elif not self.directive or not self.directive.strip() or self.directive not in question:
            raise ValueError("research task requires a verbatim current-request directive")
        if self.experiment_directive is not None and (
                self.mode != "research" or not self.experiment_directive.strip()
                or self.experiment_directive not in question):
            raise ValueError("experimental plan requires a current explicit design directive")
        return self

    @property
    def research_requested(self) -> bool:
        return self.mode == "research"

    @property
    def experiments_requested(self) -> bool:
        return self.mode == "research" and self.experiment_directive is not None
