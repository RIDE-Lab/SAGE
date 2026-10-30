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


When an adapter declares `requires_final_audit=True`, every draft review calls
its publication-grade auditor directly. It checks **every** sentence against the
complete immutable source snapshot; a faster preliminary verdict cannot authorize
publication. An accepted, unchanged draft is not audited a second time. A bounded
revision clears the old receipt and repeats the same full audit on the revised
answer. Malformed serialization gets at most one bound correction at the same
review strength. Rendering requires the exact final audit and completeness receipts.
No-source notices do not fabricate an audit event. Model qualification and actual
end-to-end latency remain separate acceptance requirements.

Coverage wire v7 is a sparse source-local set: each question requirement is
mandatory, and selected paragraph keys have value `true`; omitted keys mean not
selected. Explicit `false`, unknown/cross-source keys and duplicate keys are
rejected, not silently normalized. Full source text and immutable paragraph IDs
remain unchanged; only serialization of negative decisions is removed.

Research proposals and workload-scoped findings must retain their scope. A
paper advocating a method is not evidence that all alternatives fail or that its
method is universally necessary. Qualified source reports and conditional
mathematical consequences remain admissible. Qualify this distinction with real
positive and negative controls, not only protocol fixtures.

Execution time is separate from generation depth. `execution_budget_seconds`
selects the host's declared extended budget when publication requires native
verification, even if the user disables deep generation. Hosts must apply the
same selection to worker cancellation and user-visible timing. This remains a
bounded overall deadline, with unchanged per-tool timeouts, admission limits and
idempotency; it is not permission to silently retry a generation or write.

Adapters may additionally require `requires_scope_audit=True`. The independent
`scope_check` receives the question and exact answer units, bound to the same
full-source digest, without repeating source rhetoric. It can only veto an
unqualified generalization and trigger a bounded revision. Its counterexamples
are never evidence and cannot authorize facts. Every unit needs an explicit
decision; missing, duplicate or stale decisions fail closed. Rendering requires
both this receipt and the unchanged publication-grade source/completeness checks.
Revisions rerun all checks. Real-model false-positive and false-negative controls
remain necessary; schema validity is not a semantic quality measurement.

### Question identity and source scope

The optional `interpret_question` model port receives only the exact original
question and configured public subject context. It cannot see retrieved sources.
Its validated relation is frozen into coverage, partition, supplemental search,
answer construction and completeness receipts. The relation is a model
interpretation, not proof of relevance: original-question live acceptance remains
required. Changing it invalidates the bound source assessment.

A proposal in one paper must remain attributed and scoped in natural prose.
General methodology retrieval asks for complementary baseline evidence when a
new proposal alone does not explain established controls. Each answer unit must
be both responsive and entailed; being copied from a source cannot authorize a
tangent or an unqualified universal claim.

Source admission additionally compares only the declared established relations
with the frozen information need. This separate review can remove witnesses
from a candidate, never introduce evidence or promote background. Its receipt
is bound to the entire original coverage mapping. Full source text remains
available for the publication-grade audit. A controlled native-admission
experiment was inconsistent and was not retained as an extra slow gate.

Bound revisions also reject a replacement that reproduces an unaffected sentence
or duplicates a newly inserted sentence. This is an explicit validation failure,
not automatic deletion: the runtime does not silently rewrite approved prose.
