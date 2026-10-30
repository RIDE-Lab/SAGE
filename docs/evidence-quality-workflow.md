# Native evidence quality workflow

`sage.runtime.evidence_workflow.EvidenceWorkflowRuntime` is a single persistent
native flow for read-only evidence work. Applications supply request-scoped
`assess`, `supplement`, `write_answer`, `audit_answer` and `plan_experiments`
ports, not another scheduler.

The flow is:

1. Assess coverage against the complete question and authorized source snapshot.
2. Route only missing evidence through one supplement round and fresh assessment.
3. Compose a source-constrained plan; mapped witnesses may not disappear from
   the selected factual source set.
4. Research requests with selected factual sources execute a separate typed
   experiment-plan stage. Ordinary factual requests do not incur this model
   stage. The model supplies mechanism settings, shared conditions and
   measurements, never verdict or falsifier prose. SAGE renders the distinct
   invariant counterexample and performance confidence-bound rules. Selected
   factual sources cannot be changed by this stage.
5. Compose natural prose from the selected sources, audit every sentence and
   check question completeness. At most one semantic revision is reviewed again.
   Render any remaining evidence gaps as original question spans, explicitly not
   negative factual conclusions.

Native observations expose these actual decisions, nodes and call relationships.
The complete declared graph includes inactive repair/research branches. A
completed condition is not evidence that its branch was selected.

## Evidence contract

`prepare_coverage` requires non-overlapping contiguous spans partitioning EVERY
character of the question. `question_requirements` supplies conservative
syntactic sentence/list units; it does not claim semantic decomposition. Adapters
with a qualified parser may supply finer partitions. Qualifiers, references and
the full original question remain available to the assessor in every case.

The assessor returns exactly one receipt per requirement, bound to a digest of
the question, spans and complete sources. Witnesses are exact nonempty substrings
of authorized source IDs. Unknown IDs, dropped requirements, altered source text,
forged/stale receipts, duplicated JSON fields and unwitnessed prose are rejected.
Requests are rebound to their canonical content during validation. No snippets,
document truncation, response budget lowering or source mutation is used.

An exact witness proves provenance, NOT entailment or independent truth. The
model's relevance/completeness assessment still needs real-question qualification.
Never advertise a coverage receipt as a guarantee that the answer is correct.
Research proposals likewise remain labeled prospective inferences, not facts.
Candidate implementations are explicitly not claims about the project's current
architecture, actual experiments or results. Relative performance gains require
positive comparable baseline measurements; inconclusive intervals do not
falsify a gain threshold. One reproducible invariant violation can falsify its
stated universal claim, independently of statistical significance or speedup.

## Adapter obligations

The immutable adaptive plan distinguishes `evidence_required` (an explicitly
source-constrained request) from opportunistic knowledge retrieval. Empty Wiki
retrieval must not disable ordinary advice or a caller's own conversation memory.
Conversely, denied/empty explicitly requested sources must not silently turn into
unconstrained factual generation. Resolved/business turns remain resolved.

- Supply only sources visible to the verified caller. Every supplement search
  and document hydration must reapply the same captured identity and source scope;
  question/model text must never widen permissions. There are no write ports.
- Supplement accepts only missing original question spans and returns immutable
  `PlanSource` records. Conflicting reuse of a source ID is rejected. The runtime
  preserves all existing sources rather than replacing them with an abstract.
- `assess(request)` returns coverage JSON; `write_answer` returns natural prose
  bound to the factual plan and `audit_answer` returns a complete source receipt.
  `plan_experiments(request, plan)`
  returns typed experiment JSON (`experiment_response_format`). Fact selection
  is fixed before this stage. The exact experiment receipt is revalidated and
  canonically rendered before publication; arbitrary replacement prose fails.
  These ports must use configured models
  and auth helpers without logging credentials or private request payloads.
- Respect the caller's remaining deadline. Native child requests inherit
  cancellation. No business writes are replayed, and gaps do not start an
  unbounded search/generation loop.

Structured child failure types survive the parent native flow boundary, so a
permanent validation rejection cannot become a generic retryable generation
failure simply because a quality subflow was introduced.

This module's synthetic unit/integration fixtures do not qualify real model
quality, live ACL behavior, application integration or production deployment.

## Natural composition and bounded correction

The factual answer is composed as question-led prose, followed by a complete
sentence-level source audit and a separate question-completeness check. Sources
stay in the basis records, not as pasted documents in the answer. The optional
published `public_context` is bound throughout coverage, partitioning, retrieval
planning, composition and audit; it disambiguates a discipline, never supplies
facts or changes source permissions.

Model coverage now uses source-local decisions. Each source first identifies its
subject and selects a role. Background/irrelevant decisions have no evidence field;
a direct decision contains a boolean per immutable paragraph for every question
requirement. Source IDs, requirement IDs and paragraph IDs are unique object keys,
and each source can select only its own paragraphs. Missing decisions, duplicates,
unknown keys and cross-source references are rejected, not silently removed.
This makes role/selection contradictions unrepresentable under the declared grammar.
The independent canonical receipt still binds exact, complete source ranges.
Boolean decisions grow linearly with the number of paragraphs; context admission
and whole-source partitioning remain explicit rather than truncating source text.
The audit wire protocol selects `checked_sources` blocks before deciding each
verdict; these are inspected passages, which may include counterevidence.
The decoder validates every reference and expands only *supported* units to the
canonical positive-evidence receipt. An unsupported verdict always remains a
rejection, even when it cites an inspected passage. No evidence text is truncated.

A malformed audit receipt may be corrected once against the same draft/source
snapshot in an observed native node. This cannot rerun retrieval, generation or
business effects. Terminal validation failures keep `SourceAuditRejected` rather
than being mistaken for transport ambiguity.

Semantic answer correction uses a bound revision: one explicit replacement or
removal per failed sentence, with other sentences preserved. Additional paragraphs
are permitted only for missing requested parts. Stale snapshots, omitted edits,
unchanged rejected sentences, unauthorized expansion and invalid references fail.
Applying a revision does not accept it: the entire revised answer is audited again.

Adapters may declare the verified vLLM grammar capability and install
`SAGE[structured-output]`. `compact_json_grammar` restricts insignificant JSON
whitespace while retaining the schema, string content and answer length. It fails
explicitly without its optional dependency; it does not fall back to unconstrained
text. Prose generation and entailment review have different model requirements:
qualify the configured model sampling and native reasoning on real positive and
negative controls, not only shape-valid JSON. This library does not select or
silently replace the application's model.


When an adapter declares `requires_final_audit=True`, the ordinary source reviews
are preflight feedback only. One final native `final-source-audit` node reviews
**every** sentence of the final draft against the complete immutable source
snapshot, after any bounded correction. The final receipt replaces the preflight
receipt; rendering requires that exact receipt. The draft's already-bound
completeness receipt is revalidated without another model call. A final rejection
or malformed receipt stops publication; it cannot fall back to preflight approval.
No-source notices do not fabricate an audit event. Adapters without this policy
retain their existing qualified review contract. Model qualification and actual
end-to-end latency remain separate acceptance requirements.
