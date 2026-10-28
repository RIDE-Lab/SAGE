"""Lossless source-group recovery for typed model context admission failures.

Partitioning never splits a document or changes the complete question. Every
child receipt is validated before its witnesses can enter the parent receipt.
"""

import json

from sage.runtime.evidence_coverage import (
    InvalidEvidenceCoverage,
    prepare_coverage,
    validate_coverage,
)
from sage.runtime.model_errors import ModelRequestRejected


def assess_source_groups(request, assess, repair=None):
    """Try the bound snapshot, bisect only on explicit capacity rejection.

    At most 2N-1 assessment calls for N sources. Each malformed child receipt
    may be corrected once against that exact child snapshot. Transport failures
    and invalid corrections propagate; they never become a coverage gap.
    """
    try:
        return assess(request)
    except ModelRequestRejected as error:
        if (error.code != 'prompt_capacity' or request.purpose != 'source_support'
                or len(request.sources) < 2):
            raise
    middle = len(request.sources) // 2
    receipts = []
    for sources in (request.sources[:middle], request.sources[middle:]):
        child = prepare_coverage(request.question, request.requirements, sources,
                                 purpose=request.purpose)
        raw = assess_source_groups(child, assess, repair)
        try:
            receipt = validate_coverage(child, raw)
        except InvalidEvidenceCoverage as error:
            if repair is None or error.code == "invalid_request_or_receipt":
                raise
            # Do not repair against the over-capacity parent, relabel a digest,
            # truncate a source, or restart already accepted model invocations.
            raw = repair(child, raw, error.code)
            receipt = validate_coverage(child, raw)
        receipts.append(receipt)
    merged = {'digest': request.digest, 'items': [
        {'requirement_id': requirement.requirement_id,
         'witnesses': [{'source_id': witness.source_id, 'start_line': witness.start_line,
                        'end_line': witness.end_line} for receipt in receipts for item in receipt.items
                       if item.requirement_id == requirement.requirement_id
                       for witness in item.witnesses]}
        for requirement in request.requirements]}
    raw = json.dumps(merged, ensure_ascii=False)
    validate_coverage(request, raw)
    return raw
