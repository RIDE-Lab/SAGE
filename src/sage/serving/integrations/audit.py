from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any


def verify_decision_certificate_chain(
    rows: list[dict[str, Any]],
    chain: dict[str, Any],
) -> list[str]:
    errors: list[str] = []
    if chain.get("schema_version") != AUDIT_SCHEMA_VERSION:
        errors.append("chain schema mismatch")
    if chain.get("hash_algorithm") != AUDIT_HASH_ALGORITHM:
        errors.append("chain hash algorithm mismatch")
    if chain.get("record_count") != len(rows):
        errors.append("chain record count mismatch")
    previous_digest = GENESIS_DIGEST
    for index, source_row in enumerate(rows):
        row = deepcopy(source_row)
        decision_trace = row.get("decision_trace")
        if not isinstance(decision_trace, dict):
            errors.append(f"trace row {index} is missing decision_trace")
            continue
        certificate = decision_trace.get("certificate")
        if not isinstance(certificate, dict):
            errors.append(f"trace row {index} is missing certificate")
            continue
        observed_digest = certificate.pop("record_digest", None)
        if certificate.get("record_index") != index:
            errors.append(f"trace row {index} record index mismatch")
        if certificate.get("previous_digest") != previous_digest:
            errors.append(f"trace row {index} previous digest mismatch")
        if certificate.get("policy_identity") != chain.get("policy_identity"):
            errors.append(f"trace row {index} policy identity mismatch")
        if certificate.get("policy_config_digest") != chain.get("policy_config_digest"):
            errors.append(f"trace row {index} policy config digest mismatch")
        expected_digest = payload_digest(row)
        if observed_digest != expected_digest:
            errors.append(f"trace row {index} record digest mismatch")
        if isinstance(observed_digest, str):
            previous_digest = observed_digest
    if chain.get("genesis_digest") != GENESIS_DIGEST:
        errors.append("chain genesis digest mismatch")
    if chain.get("root_digest") != previous_digest:
        errors.append("chain root digest mismatch")
    return errors

AUDIT_SCHEMA_VERSION = "vamos.decision-certificate.v1"
AUDIT_HASH_ALGORITHM = "sha256"
GENESIS_DIGEST = "0" * 64


def canonical_json(payload: Any) -> str:
    return json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def payload_digest(payload: Any) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def build_decision_certificate_chain(
    trace_rows: list[dict[str, Any]],
    *,
    policy_identity: str,
    policy_config: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    policy_config_digest = payload_digest(policy_config)
    previous_digest = GENESIS_DIGEST
    certified_rows: list[dict[str, Any]] = []

    for index, source_row in enumerate(trace_rows):
        row = deepcopy(source_row)
        decision_trace = row.get("decision_trace")
        if not isinstance(decision_trace, dict):
            raise ValueError(f"Trace row {index} is missing decision_trace")
        certificate = {
            "schema_version": AUDIT_SCHEMA_VERSION,
            "record_index": index,
            "policy_identity": policy_identity,
            "policy_config_digest": policy_config_digest,
            "previous_digest": previous_digest,
        }
        decision_trace["certificate"] = certificate
        record_digest = payload_digest(row)
        certificate["record_digest"] = record_digest
        previous_digest = record_digest
        certified_rows.append(row)

    chain = {
        "schema_version": AUDIT_SCHEMA_VERSION,
        "hash_algorithm": AUDIT_HASH_ALGORITHM,
        "record_count": len(certified_rows),
        "policy_identity": policy_identity,
        "policy_config_digest": policy_config_digest,
        "genesis_digest": GENESIS_DIGEST,
        "root_digest": previous_digest,
    }
    return certified_rows, chain
