"""Capability-bounded web research policy and observed execution, for any host.

Semantic intent is a proposal, not permission. Never supply retrieved evidence,
conversation history, attachments or credentials to an external query builder.
Unknown disclosure scope is denied, including in user-required mode.
"""

import re
from collections.abc import Callable
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime
from time import monotonic

from sage.runtime.read_tool_deadline import ReadToolDeadlineExceeded, read_tool_executor

_deadline = ContextVar("sage_web_research_deadline", default=None)


def web_research_deadline():
    return _deadline.get()


@contextmanager
def web_research_budget(seconds):
    """One cooperative deadline, isolated per request and never extended by a port."""
    deadline = monotonic() + seconds
    previous = _deadline.get()
    token = _deadline.set(min(previous, deadline) if previous is not None else deadline)
    try:
        yield _deadline.get()
    finally:
        _deadline.reset(token)


WEB_RESEARCH_INSTRUCTIONS = """
Also return web_intent with exactly need, disclosure, query, local_only_span.
Classify ONLY current_question, not history, course_context or retrieved sources.
need: forbidden (the user prohibits web access), current (time-sensitive/current external facts), sources (the user asks to
search public web/documents or externally sourced research), none (stable
explanation, local Wiki/lab facts, writing, scheduling or self-contained work).
disclosure: public for a clearly public subject; private for internal lab,
personal, unpublished, confidential or credential-bearing content; unknown for
ambiguous references such as 'it', 'that project', or unresolved pronouns.
Disclosure is INDEPENDENT of whether web search is initially needed. General
scientific concepts, experimental methods and technical comparisons without
personal/internal qualifiers are public, even when need=none. Unknown means an
unresolved private/public referent, NOT that the answer or source is unknown.
query: a nonempty VERBATIM CONTIGUOUS span of current_question preserving both
the public subject AND the requested relation/question,
without personal/private context, when disclosure=public, otherwise null.
Provide this public span even for need=none, so the runtime can supplement
public evidence if local coverage is later found insufficient. Do not infer
private context from the user's identity or from history.
local_only_span: a verbatim current-question span explicitly limiting evidence
to Wiki/private/local sources (e.g. '根据公开 Wiki' or 'only our private KB'),
otherwise null. A preference to retrieve Wiki is NOT a prohibition on web.
Public software release/documentation questions have local_only_span=null.
Do not resolve web queries using history. A quote/negation is not a search
instruction; 'do not search' and Wiki/private-only constraints must be honored.
These fields do not grant membership or source access. Never mark internal lab
progress public simply because its project name is public.
"""


@dataclass(frozen=True)
class WebIntent:
    need: str = "none"
    disclosure: str = "unknown"
    query: str | None = None
    local_only_span: str | None = None

    @staticmethod
    def json_schema():
        return {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "need": {"type": "string", "enum": ["forbidden", "current", "sources", "none"]},
                "disclosure": {"type": "string", "enum": ["public", "private", "unknown"]},
                "query": {"type": ["string", "null"]},
                "local_only_span": {"type": ["string", "null"]},
            },
            "required": ["need", "disclosure", "query", "local_only_span"],
        }

    def __post_init__(self):
        if (
            not isinstance(self.need, str)
            or self.need not in {"forbidden", "current", "sources", "none"}
            or not isinstance(self.disclosure, str)
            or self.disclosure not in {"public", "private", "unknown"}
            or (self.query is not None and not isinstance(self.query, str))
            or (self.local_only_span is not None and not isinstance(self.local_only_span, str))
        ):
            raise ValueError("invalid web intent values")

    @classmethod
    def parse(cls, value):
        if value is None:
            return cls()
        if (
            not isinstance(value, dict)
            or not {"need", "disclosure", "query"} <= set(value)
            or set(value) - {"need", "disclosure", "query", "local_only_span"}
        ):
            raise ValueError("invalid web intent")
        return cls(**value)

    def validate_query(self, question):
        """Validate the proposed span, without expanding its disclosure scope."""
        if self.disclosure != "public":
            return
        query = self.query
        if self.need == "none" and query is None:
            return  # A stable explanation need not invent an outbound query.
        if not query or not query.strip() or query not in question or len(query) > 512:
            raise ValueError("public web query requires a verbatim current-question subject")
        # A one-character slice of a larger word is not a complete subject.
        # Standalone/quoted characters remain legal (e.g. asking about '大').
        if len(query) == 1 and query.isalnum():
            occurrences = tuple(re.finditer(re.escape(query), question))
            if all(
                (m.start() > 0 and (question[m.start()-1].isalnum() or question[m.start()-1] == "_"))
                or (m.end() < len(question) and (question[m.end()].isalnum() or question[m.end()] == "_"))
                for m in occurrences
            ):
                raise ValueError("public web query is a one-character word fragment")


@dataclass(frozen=True)
class WebDecision:
    mode: str
    identity: str
    allowed: bool
    reason: str
    query: str | None = None


def decide_web_research(
    *,
    question: str,
    mode: str,
    identity: str,
    intent: WebIntent,
    available: bool = True,
    source_restricted: bool = False,
    active: bool = True,
    contains_private_context: bool = False,
    evidence_gap: bool = False,
) -> WebDecision:
    if mode not in {"auto", "never", "always"}:
        raise ValueError("invalid web mode")
    if identity not in {"guest", "account", "api_token", "slack", "admin"}:
        raise ValueError("unverified identity kind")

    def denied(reason):
        return WebDecision(mode, identity, False, reason)

    if mode == "never":
        return denied("用户禁止联网，本轮未发送外部搜索请求。")
    if intent.need == "forbidden":
        return denied("当前问题要求不联网，本轮未发送外部搜索请求。")
    if not available:
        return denied("联网能力未启用，本轮未搜索。")
    if not active:
        return denied("本轮为已解决或业务操作，不启动无关联的网页搜索。")
    if source_restricted or intent.local_only_span is not None:
        return denied("本轮限定 Wiki／私有来源，不扩大到外部网页。")
    # This host flag is trusted; model proposals cannot override it.
    if contains_private_context or intent.disclosure != "public":
        return denied("未确认可公开的搜索主题；私密或含糊内容不会发送到外部搜索。")
    query = intent.query
    if not query or not query.strip() or query not in question or len(query) > 512:
        return denied("缺少可逐字核对的当前问题搜索主题，本轮未搜索。")
    try:
        intent.validate_query(question)
    except ValueError:
        return denied("缺少可逐字核对的当前问题搜索主题，本轮未搜索。")
    # Defense in depth, not a claim of complete secret/PII detection.
    if re.search(
        r"(?i)(?:bearer\s|sk-[a-z0-9_-]{8}|-----BEGIN|password\s*[:=]|"
        r"api[_ -]?key\s*[:=]|[\w.+-]+@[\w.-]+\.[a-z]{2,})",
        question,
    ):
        return denied("当前问题包含疑似敏感标识，不发送到外部搜索。")
    if mode == "auto" and intent.need == "none" and not evidence_gap:
        return denied("本轮无需最新外部信息，优先使用授权来源或直接回答。")
    return WebDecision(
        mode,
        identity,
        True,
        "用户要求联网，已通过公开主题边界检查。"
        if mode == "always"
        else "本轮需要最新信息或公开网页依据，已选择联网。",
        query,
    )


def execute_web_research(
    decision: WebDecision, search: Callable, observe: Callable, *, timeout_seconds=8, review: Callable | None = None, public_context: str = "", search_timeout_seconds: float = 8,
    hydrate: Callable | None = None,
):
    """Observe only actual provider execution, not an optimistic workflow graph.

    The host owns HTTP transport/ACLs. Errors never appear as successful search.
    No evidence is automatically persisted or granted to a private corpus.
    """
    if not decision.allowed:
        observe("skipped", decision.reason, None)
        return []
    observe("running", "正在搜索公开网页。", None)
    try:
        with web_research_budget(timeout_seconds) as deadline:
            from sage.runtime.web_search_quality import prepare_search_review, validate_search_review
            query = decision.query
            hits = []
            attempted_queries = []
            provider_failed = False
            if review is not None:
                observe("running", "正在组织公开搜索词。", None)
                plan = prepare_search_review(decision.query, [], public_context=public_context)
                raw = read_tool_executor.call(lambda r=plan: review(r), timeout=max(0, deadline-monotonic()))
                _, rewritten = validate_search_review(plan, raw)
                query = rewritten or query
            for attempt in range(2):
                attempted_queries.append(query)
                try:
                    candidates = read_tool_executor.call(
                        lambda q=query: list(search(q)), timeout=max(0, min(search_timeout_seconds, deadline - monotonic())))
                    provider_failed = False
                except Exception:
                    if review is None:
                        raise
                    candidates = []
                    provider_failed = True
                    observe("running", "搜索入口未返回结果，正在调整公开搜索词。", None)
                if review is None:
                    hits = candidates
                    if hits and hydrate is not None and monotonic() < deadline:
                        accepted = tuple(hits)
                        try:
                            enriched = read_tool_executor.call(lambda h=accepted: list(hydrate(h)),
                                timeout=max(0, min(search_timeout_seconds, deadline-monotonic())))
                            if [h.url for h in enriched] != [h.url for h in accepted]:
                                raise ValueError("extraction changed selected source identities")
                            hits = enriched
                        except Exception:
                            observe("running", "部分网页正文暂未取得，保留已取得的来源摘录。", None)
                    break
                observe("running", "正在检查网页是否切题。", None)
                # Retain previously accepted partial evidence. A failed second
                # provider call cannot erase the first source; no request replay
                # or private context is introduced by this read-only supplement.
                if not candidates and hits:
                    break
                combined = {hit.url: hit for hit in hits}
                for candidate in candidates:
                    combined.setdefault(candidate.url, candidate)
                candidates = list(combined.values())
                request = prepare_search_review(decision.query, candidates, attempted_queries=attempted_queries, public_context=public_context)
                raw = read_tool_executor.call(lambda r=request: review(r), timeout=max(0, deadline-monotonic()))
                selected, retry = validate_search_review(request, raw)
                hits = [candidates[i] for i in selected]
                if hits and hydrate is not None and monotonic() < deadline:
                    observe("running", "正在读取入选网页正文。", None)
                    accepted = tuple(hits)
                    try:
                        enriched = read_tool_executor.call(lambda h=accepted: list(hydrate(h)),
                            timeout=max(0, min(search_timeout_seconds, deadline-monotonic())))
                        if [h.url for h in enriched] != [h.url for h in accepted]:
                            raise ValueError("extraction changed selected source identities")
                    except Exception:
                        observe("running", "部分网页正文暂未取得，保留已取得的来源摘录。", None)
                    else:
                        if any(getattr(h, "source_content", None) != getattr(old, "source_content", None)
                               for h, old in zip(enriched, accepted)):
                            observe("running", "正在核对正文是否真正回答本轮问题。", None)
                            body_request = prepare_search_review(decision.query, enriched,
                                attempted_queries=attempted_queries, public_context=public_context)
                            body_raw = read_tool_executor.call(lambda r=body_request: review(r),
                                timeout=max(0, deadline-monotonic()))
                            body_selected, retry = validate_search_review(body_request, body_raw)
                            hits = [enriched[i] for i in body_selected]
                if not retry or retry in attempted_queries or attempt:
                    break
                query = retry
                observe("running", "正在补搜问题尚未覆盖的部分。" if hits else "候选网页不够相关，正在调整搜索词。", None)
            if monotonic() > deadline:
                raise TimeoutError("web research deadline exceeded")
    except ReadToolDeadlineExceeded:
        if not hits:
            observe("failed", "搜索等待超时；外部请求可能仍在清理，迟到结果不会作为依据。", None)
            return []
        observe("running", "补充检索等待超时，保留此前已取得的候选来源；迟到结果不会作为依据。", None)
    except Exception:
        if not hits:
            observe("failed", "网页搜索失败或超时；不能声称已取得联网依据。", None)
            return []
        observe("running", "补充检索未完成，保留此前已取得的候选来源。", None)
    if not hits and provider_failed:
        observe("failed", "搜索入口未返回可用结果；本轮没有取得联网依据。", None)
        return []
    checked_at = datetime.now(UTC).isoformat()
    observe(
        "completed" if hits else "empty",
        f"网页搜索返回 {len(hits)} 条候选来源；候选不等于事实已核实。"
        if hits
        else "网页搜索已执行，但未取得可用来源。",
        checked_at,
    )
    return hits
