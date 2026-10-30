# Automatic web research

`sage.runtime.web_research` owns one host-independent decision and execution
contract. Adapters supply verified identity, operator network availability,
explicit API source boundary and request-local private-context flags. A semantic
intent proposal supplies need, disclosure and an exact current-question query
span; it cannot grant a role or access a private source. Local retrieval ACLs
remain independently enforced by the host.

Modes: auto uses public current-information/source needs; never sends no external
request; always requires a safe, explicit public subject and cannot override
private/unknown disclosure, local-only source constraints or a network denial.
Resolved/business operations do not start unrelated searches. Missing local hits
are not permission to search. Current information can require searching even
when an older local source exists.

The local source preference of a retrieval planner is not a user prohibition on
web access. The immutable API source constraint remains authoritative. An
additional semantic local-only constraint is carried as `local_only_span` from
the current question. Model mistakes can conservatively deny a search; they
must never expand the trusted source capabilities.

The disclosure decision first binds a contiguous public span of the current
question. The optional query planner sees only that approved span, public
candidate snippets, and a configured published discipline. It may derive technical
search terms from that data; conversation history, private attachments and internal
retrieval-repair queries must never reach it or the external transport. Only ambiguous queries need this broad domain anchor; unambiguous technical
concepts should not require pages to contain the broad discipline name. An explicitly
selected public discipline is searched as a quoted phrase to prevent optional
bag-of-words matching from erasing its meaning. An explicit question about another
field overrides that default, with the decision bound in the relevance receipt. Common credential/email guards are defense in depth, not
complete secret/PII recognition. Semantic public/private classification is
fallible; the host should deny requests with private attachments/channel context
and must not describe this policy as a formal data-loss-prevention guarantee.
Unknown subject references require clarification rather than history expansion.

`WebIntent.json_schema()` exposes the same field contract for native structured
decoding. Shape constraints prevent missing/invalid enum fields; they do not prove
that a semantic label or a public-subject classification is correct. Hosts must
retain the runtime capability checks and fail closed on unresolved proposals.

`execute_web_research` emits running immediately before the real transport call,
then completed/empty/failed from its actual outcome. Skipped decisions never call
transport. Failed/empty results must not be displayed as retrieved or verified
evidence. Completed means candidates were retrieved, not that all claims are
true. Successful attempts carry a UTC retrieval time; hosts retain full evidence
links and display the policy reason. Old records without receipts stay unknown.

Transport exceptions must propagate to this contract; a provider chain that
silently turns all failures into an empty list cannot distinguish outages from
successful zero-result searches. Do not add a second scheduler or parallel
application policy to work around that ambiguity.

All providers share a ContextVar deadline. SDK transports must pass remaining
time rather than resetting their own default timeout. `ReadToolExecutor` bounds
caller waiting even when an OS resolver ignores a cooperative socket timeout.
It admits at most four outstanding read-only operations, retains capacity until
cleanup, and discards late results. Python cannot forcibly cancel running DNS;
the timeout receipt explicitly says cleanup may remain pending. This executor
must never be used to replay or abandon externally mutating operations.


Direct relevance does not imply complete coverage. The review may retain useful
candidates and request one complementary search for a missing comparison/scenario.
The second review sees the accepted sources plus new URL-deduplicated candidates,
never private retrieval context. The same two-attempt and total deadline apply.
A failed second provider call preserves previously accepted excerpts; these still
require downstream coverage and source checks and are never declared complete
merely because the read succeeded.


After selected pages are hydrated, their complete returned bodies are rechecked
against the question. A title or search snippet promising a relevant method is
not sufficient when the actual page is only catalog metadata or navigation.
A rejected body can activate the same one complementary search; it does not add
an unbounded loop. No body is summarized or truncated for this check. Previously
accepted evidence survives supplemental read/review failures, but downstream
coverage and fact checks still determine whether it can support an answer.
