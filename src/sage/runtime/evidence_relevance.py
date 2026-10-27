"""Source-neutral named-subject relevance for already-authorized candidates."""

from __future__ import annotations

import re


def interleave_source_candidates(lanes, per_source_limit):
    """Keep each independently ranked source's bounded candidate budget.

    Adding an authorized source must not evict candidates from another source.
    Scores from different indexes are deliberately never compared here.
    This forms a candidate pool, not a final answer/evidence selection.
    """
    if type(per_source_limit) is not int or per_source_limit < 1:
        raise ValueError("positive per-source candidate limit required")
    bounded = tuple(tuple(lane[:per_source_limit]) for lane in lanes)
    return tuple(item for rank in range(per_source_limit) for lane in bounded
                 for item in lane[rank:rank+1])


def question_identifiers(question: str) -> tuple[str, ...]:
    """Literal acronym/mixed-case/versioned identifiers, never inferred roles.

    Deliberately conservative: ordinary capitalized prose and unidentified
    Chinese names remain for semantic retrieval rather than guessed entities.
    No project-name dictionary or generated query content is used.
    """
    return tuple(dict.fromkeys(term for term in
        re.findall(r"(?<![\w])([A-Za-z][A-Za-z0-9_.-]*)(?![\w])", question)
        if len(term) > 1 and (term.isupper() or sum(c.isupper() for c in term) > 1
                             or any(c.isdigit() for c in term))))


def subject_candidate_indices(subjects: tuple[str, ...], texts: tuple[str, ...]) -> tuple[int, ...]:
    """Prefer discriminating named subjects, not collection-wide labels.

    Preserve original order and all candidates for any requested subject. A name
    shared by every candidate cannot distinguish relevance (e.g. a source label).
    If no discriminating match exists, leave semantic retrieval unchanged. This
    is a relevance preference, never an authorization or truth check. Callers
    must supply complete permitted text, not truncated display excerpts.
    """
    matches = []
    for subject in dict.fromkeys(subjects):
        if not subject:
            continue
        pattern = re.compile(r"(?<![A-Za-z0-9_])" + re.escape(subject) + r"(?![A-Za-z0-9_])", re.I)
        indices = {i for i, text in enumerate(texts) if pattern.search(text)}
        if indices and len(indices) < len(texts):
            matches.append(indices)
    selected = set().union(*matches) if matches else set(range(len(texts)))
    return tuple(i for i in range(len(texts)) if i in selected)
