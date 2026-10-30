"""Compile the same JSON schema into compact grammar for a declared vLLM port.

Only insignificant JSON formatting is restricted. String contents, source text,
answer length and semantic validation are unchanged. Unsupported configurations
fail explicitly; there is no unstructured-output fallback.
"""
from functools import lru_cache
import json


@lru_cache(maxsize=128)
def _compile(schema_json: str) -> str:
    try:
        from xgrammar import Grammar
    except ImportError as exc:
        raise RuntimeError("compact JSON grammar requires SAGE[structured-output]") from exc
    return str(Grammar.from_json_schema(schema_json, any_whitespace=False,
        indent=None, separators=(",", ":"), strict_mode=False))


def compact_json_grammar(schema: dict) -> str:
    if not isinstance(schema, dict):
        raise ValueError("JSON schema object required")
    # Preserve property declaration order: planners classify source roles before
    # selecting evidence. Sorting keys would change constrained decoding order.
    return _compile(json.dumps(schema, ensure_ascii=False, separators=(",", ":"), allow_nan=False))
