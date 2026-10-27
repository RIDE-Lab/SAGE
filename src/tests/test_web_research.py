import pytest

from sage.runtime.web_research import (
    WebIntent,
    decide_web_research,
    execute_web_research,
    web_research_budget,
    web_research_deadline,
)


def test_one_deadline_cannot_be_extended_by_nested_adapters_and_is_reset():
    assert web_research_deadline() is None
    with web_research_budget(1) as deadline:
        with web_research_budget(60):
            assert web_research_deadline() == deadline
        assert web_research_deadline() == deadline
    assert web_research_deadline() is None


def decision(**overrides):
    fields = dict(
        question="vLLM 最新发布",
        mode="auto",
        identity="guest",
        intent=WebIntent("current", "public", "vLLM 最新发布"),
    )
    fields.update(overrides)
    return decide_web_research(**fields)


@pytest.mark.parametrize("identity", ["guest", "account", "api_token", "slack", "admin"])
def test_public_current_information_is_available_to_each_verified_identity(identity):
    assert decision(identity=identity).allowed


def test_controls_and_local_source_boundary():
    assert not decision(mode="never").allowed
    assert not decision(source_restricted=True, mode="always").allowed
    assert not decision(active=False).allowed
    assert not decision(available=False).allowed
    assert not decision(intent=WebIntent("none", "public", "vLLM")).allowed
    assert decision(mode="always", intent=WebIntent("none", "public", "vLLM")).allowed
    assert not decision(
        mode="always", intent=WebIntent("sources", "public", "vLLM", "只查 Wiki")
    ).allowed


def test_malformed_proposals_fail_with_explicit_validation_errors():
    for value in (["current"], {}, False):
        with pytest.raises(ValueError):
            WebIntent.parse(dict(need=value, disclosure="public", query="vLLM"))


def test_native_decoding_schema_covers_all_semantic_fields_without_granting_authority():
    schema = WebIntent.json_schema()
    assert schema["additionalProperties"] is False
    assert set(schema["properties"]) == set(schema["required"])
    for need in schema["properties"]["need"]["enum"]:
        assert WebIntent.parse(
            dict(need=need, disclosure="public", query="vLLM", local_only_span=None)
        )


@pytest.mark.parametrize("identity", ["guest", "account", "slack", "admin"])
def test_identity_never_authorizes_disclosure_of_private_or_unknown_content(identity):
    for scope in ("private", "unknown"):
        assert not decision(
            identity=identity, mode="always", intent=WebIntent("current", scope, "vLLM")
        ).allowed
    assert not decision(identity=identity, contains_private_context=True).allowed


def test_queries_cannot_be_invented_from_history_or_include_common_secrets():
    assert not decision(intent=WebIntent("current", "public", "history secret")).allowed
    assert not decision(question="vLLM api_key=secret").allowed
    with pytest.raises(ValueError):
        WebIntent.parse({"need": "current", "disclosure": "public", "query": "x", "role": "admin"})


def test_observations_distinguish_skip_running_empty_failure_and_sources():
    events = []

    def observe(*args):
        events.append(args)

    assert execute_web_research(decision(mode="never"), lambda _: pytest.fail(), observe) == []
    assert events[-1][0] == "skipped"
    execute_web_research(decision(), lambda _: [], observe)
    assert [e[0] for e in events[-2:]] == ["running", "empty"]
    assert events[-1][2]

    def broken(_):
        raise TimeoutError("private upstream payload")

    execute_web_research(decision(), broken, observe)
    assert events[-1][0] == "failed" and "private upstream" not in str(events)
    assert execute_web_research(decision(), lambda _: ["source"], observe) == ["source"]
    assert events[-1][0] == "completed"


def test_public_query_cannot_shrink_a_word_to_one_character():
    intent = WebIntent(need='sources', disclosure='public', query='大')
    with pytest.raises(ValueError, match='word fragment'):
        intent.validate_query('大模型生成一段回答，背后经历了哪些步骤？')
    decision = decide_web_research(question='大模型生成一段回答，背后经历了哪些步骤？',
        mode='auto',identity='account',intent=intent)
    assert not decision.allowed and decision.query is None
    # A genuine standalone single-character subject is not banned.
    quoted = '请搜索“大”的字源。'
    intent.validate_query(quoted)
    assert decide_web_research(question=quoted,mode='always',identity='guest',intent=intent).allowed


def test_public_query_missing_or_rewritten_is_rejected_without_expanding_question():
    for query in (None, '', 'rewritten external subject'):
        with pytest.raises(ValueError, match='verbatim'):
            WebIntent(need='sources',disclosure='public',query=query).validate_query('A public subject?')
