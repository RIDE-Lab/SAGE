import json
from dataclasses import FrozenInstanceError, asdict

import pytest

from sage.runtime.source_answer_plan import (
    InvalidSourcePlan,
    PlanSource,
    prepare_source_plan,
    render_source_plan,
    render_source_plan_blocks,
    source_plan_response_format,
    validate_source_plan,
)

CONTENT = "# 研究\n\n本文不展示测量数据，完整结果见技术报告。\n\n1. 步骤\n   - 子项\n\n[报告][r]\n\n[r]: https://example.org/a/very/long/link/?a=1&b=2\n"


def request(**kwargs):
    return prepare_source_plan("是否测量？", (PlanSource("s", "标题", "https://example.org/full/", CONTENT),), **kwargs)


def response(req, **kwargs):
    return json.dumps(dict(digest=req.digest, source_ids=["s"], inferences=[], **kwargs))


def test_lossless_document_and_complete_links():
    req = request()
    answer = render_source_plan(req, validate_source_plan(req, response(req)))
    assert CONTENT in answer
    assert "https://example.org/full/" in answer
    assert "作者没有测量" not in answer
    assert json.loads(req.payload)["sources"][0]["content"] == CONTENT


def test_multisource_blocks_keep_distinct_bases_and_exact_canonical_answer():
    first = "# A\n\n[guide](../guide)\n\n```md\n## 来源原文 2\n```\n"
    second = "# B\n\n[guide](../guide)\n\n[r]: ./reference\n"
    req = prepare_source_plan("Compare", (
        PlanSource("a", "A", "https://a.example/docs/readme.md", first),
        PlanSource("b", "B", "https://b.example/other/readme.md", second),
    ), allow_inferences=True)
    plan = validate_source_plan(req, json.dumps({
        "digest": req.digest, "source_ids": ["b", "a"],
        "inferences": ["Proposed experiment.\n## not a factual heading"],
    }))
    blocks = render_source_plan_blocks(req, plan)
    source_blocks = [block for block in blocks if block.kind == "source"]
    assert [(b.source_id, b.source_url) for b in source_blocks] == [
        ("b", "https://b.example/other/readme.md"),
        ("a", "https://a.example/docs/readme.md"),
    ]
    assert source_blocks[0].markdown == "## 来源原文 1\n\n" + second
    assert source_blocks[1].markdown == "## 来源原文 2\n\n" + first
    assert all(b.source_url is None for b in blocks if b.kind != "source")
    assert blocks[-1].source_id is None
    assert blocks[-1].markdown.endswith("> Proposed experiment.\n> ## not a factual heading")
    expected = "\n\n".join([
        "以下是与问题相关的来源原文，保留完整内容与限定条件；不将来源未提及的情况推断为不存在。",
        "## 来源原文 1\n\n" + second,
        "[完整来源](https://b.example/other/readme.md)",
        "## 来源原文 2\n\n" + first,
        "[完整来源](https://a.example/docs/readme.md)",
        "> Proposed experiment.\n> ## not a factual heading",
    ])
    assert render_source_plan(req, plan) == expected
    assert "\n\n".join(b.markdown for b in blocks).strip() == expected
    wire = json.loads(json.dumps([asdict(block) for block in blocks]))
    assert wire[1]["source_url"] == "https://b.example/other/readme.md"
    with pytest.raises(FrozenInstanceError):
        blocks[1].source_url = "https://wrong.example/"


def test_blocks_revalidate_plan_and_do_not_invent_missing_source_url():
    req = prepare_source_plan("q", (PlanSource("s", "t", "", CONTENT),))
    plan = validate_source_plan(req, response(req))
    blocks = render_source_plan_blocks(req, plan)
    assert len(blocks) == 2 and blocks[1].source_url is None
    assert CONTENT in blocks[1].markdown
    with pytest.raises(InvalidSourcePlan, match="stale"):
        render_source_plan_blocks(request(), plan)


def test_empty_plan_has_only_unattributed_notice_block():
    req = request()
    plan = validate_source_plan(req, json.dumps({"digest": req.digest, "source_ids": [], "inferences": []}))
    blocks = render_source_plan_blocks(req, plan)
    assert len(blocks) == 1 and blocks[0].kind == "notice"
    assert blocks[0].source_id is None and blocks[0].source_url is None
    assert blocks[0].markdown == render_source_plan(req, plan)


def test_engine_schema_binds_authorized_ids_and_current_digest():
    req = request()
    fmt = source_plan_response_format(req)
    schema = fmt["json_schema"]["schema"]
    assert fmt["json_schema"]["strict"] is True
    assert schema["additionalProperties"] is False
    assert schema["properties"]["digest"]["enum"] == [req.digest]
    assert schema["properties"]["source_ids"]["items"]["enum"] == ["s"]
    assert schema["properties"]["inferences"]["maxItems"] == 0
    assert "maxItems" not in source_plan_response_format(request(allow_inferences=True))["json_schema"]["schema"]["properties"]["inferences"]


@pytest.mark.parametrize("field,value", [
    ("digest", "stale"), ("source_ids", ["unknown"]), ("source_ids", ["s", "s"]),
    ("source_ids", [True]), ("inferences", ["作者没有测量"]), ("inferences", "bad"),
])
def test_rejects_invalid_plan(field, value):
    req = request()
    data = json.loads(response(req))
    data[field] = value
    with pytest.raises(InvalidSourcePlan):
        validate_source_plan(req, json.dumps(data))


@pytest.mark.parametrize("raw", ["OK", "null", "{}", '{"digest":1,"digest":2}'])
def test_rejects_ambiguous_protocol(raw):
    with pytest.raises(InvalidSourcePlan):
        validate_source_plan(request(), raw)


def test_receipt_binds_question_sources_and_policy():
    req = request()
    plan = validate_source_plan(req, response(req))
    changed = prepare_source_plan("是否测量？", (PlanSource("s", "标题", "https://example.org/full/", CONTENT + "changed"),))
    for other in (changed, request(allow_inferences=True)):
        with pytest.raises(InvalidSourcePlan):
            render_source_plan(other, plan)


def test_inferences_explicit_and_separate():
    req = request(allow_inferences=True)
    data = json.loads(response(req))
    data["inferences"] = ["建议阅读报告。\n## 不应成为事实标题"]
    answer = render_source_plan(req, validate_source_plan(req, json.dumps(data)))
    assert "推论／建议（非来源事实）" not in answer
    assert "> ## 不应成为事实标题" in answer


def test_no_relevant_source_does_not_infer_absence():
    req = request()
    data = json.loads(response(req))
    data["source_ids"] = []
    assert "不能据此推断" in render_source_plan(req, validate_source_plan(req, json.dumps(data)))


@pytest.mark.parametrize("url", ["javascript:alert(1)", "https://example.org/\n#bad", "file:///secret"])
def test_unsafe_url_rejected(url):
    with pytest.raises(InvalidSourcePlan):
        prepare_source_plan("q", (PlanSource("s", "t", url, CONTENT),))
