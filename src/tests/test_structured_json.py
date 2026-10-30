import json
import pytest

from sage.runtime.structured_json import compact_json_grammar


def test_compact_grammar_preserves_schema_and_string_whitespace():
    pytest.importorskip('xgrammar')
    from xgrammar.testing import _is_grammar_accept_string
    schema = {'type':'object','additionalProperties':False,'required':['text','ids'],
        'properties':{'text':{'type':'string'},'ids':{'type':'array','maxItems':2,
            'items':{'type':'string','enum':['p0','p1']}}}}
    original = json.dumps(schema)
    grammar = compact_json_grammar(schema)
    for text in ('a  b\n c', '中文 引用 "原文"', ' ' * 200):
        compact = json.dumps({'text':text,'ids':['p0','p1']},ensure_ascii=False,separators=(',',':'))
        assert _is_grammar_accept_string(grammar,compact)
    assert not _is_grammar_accept_string(grammar, '{' + ' ' * 100 + '"text":"ok","ids":[]}')
    for data in ({'text':'ok','ids':['unknown']}, {'text':'ok','ids':['p0']*3},
                 {'text':'ok','ids':[],'extra':True}, {'ids':[]}):
        assert not _is_grammar_accept_string(grammar,json.dumps(data,separators=(',',':')))
    assert json.dumps(schema) == original
    assert compact_json_grammar(schema) == grammar


def test_compact_grammar_preserves_planner_field_order():
    pytest.importorskip('xgrammar')
    from xgrammar.testing import _is_grammar_accept_string
    schema = {'type':'object','additionalProperties':False,'required':['source_roles','items'],
        'properties':{'source_roles':{'type':'boolean'},'items':{'type':'boolean'}}}
    grammar = compact_json_grammar(schema)
    assert _is_grammar_accept_string(grammar,'{"source_roles":true,"items":false}')
    assert not _is_grammar_accept_string(grammar,'{"items":false,"source_roles":true}')


def test_compact_grammar_does_not_silently_replace_invalid_schema():
    with pytest.raises(ValueError): compact_json_grammar([])
