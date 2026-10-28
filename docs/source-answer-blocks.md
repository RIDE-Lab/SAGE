# Source answer presentation blocks

`sage.runtime.source_answer_plan.render_source_plan_blocks(request, plan)`
returns an immutable tuple of `SourceAnswerBlock` values after the same plan
validation used by canonical text rendering. It does not retrieve sources or
change authorization: callers must still supply only authorized source content.

Each block has `kind`, `markdown`, `source_id`, and `source_url`. Kinds are
`notice`, `source`, `source_link`, and `inference`. Only `source` blocks have a
relative-link base; it comes from the selected `PlanSource`, not model prose.
Inference and notice blocks cannot inherit the preceding document's URL.
Source IDs and order follow the validated selection, including reordered sources.

`render_source_plan` uses these same blocks, joining Markdown with two newlines
and stripping the complete result. This preserves the existing canonical answer
exactly and avoids independent text/block rendering paths. Document bodies,
reference definitions, code, headings and relative URLs are not rewritten.

Transport adapters can serialize blocks with `dataclasses.asdict`. They must bind
the block list to its canonical answer and persist both in the same delivery
record. Frontends should render each source block using its explicit base URL,
not infer boundaries from headings or apply one base URL to a multi-source answer.
They must validate permitted navigation schemes and HTML escaping. Missing bases
are unknown; consumers must not invent a URL or use the application's origin.

These blocks convey provenance, not independent factual validation. They do not
certify scientific suggestions, and must not be used to skip authorization or
freshness checks for restored historical content.

### Conversation evidence

`PlanSource.kind` distinguishes `document` from `conversation`. Applications may
supply complete prior turns from the authenticated owner's current conversation.
They must capture the authorized snapshot once; digests and generated summaries
are not verbatim conversation evidence. SAGE binds the kind into source selection,
coverage, natural-answer generation, independent review and publication receipts.
A prior turn may support what was said, suggested or qualified; it cannot establish
that a proposed number, deployment or experiment is true. The same source review
and bounded correction apply, with no fallback publishing of rejected text.

Natural-answer repair payloads resolve verifier sentence IDs to exact rejected sentence text using the same tokenizer as source audits. Paragraph positions are never used as sentence IDs; invalid feedback is rejected. A corrected draft still requires fresh source and completeness receipts.

Coverage receipts bind their purpose: source support versus answer completeness. A responsive, source-audited limitation can satisfy completeness without inventing a positive fact; it does not establish missing source evidence. Correction uses a dedicated repair instruction with the exact failed sentences and the same strict final checks.
