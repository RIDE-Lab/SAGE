import json
from types import SimpleNamespace
import pytest
from sage.runtime.web_research import WebDecision, execute_web_research
from sage.runtime.web_search_quality import prepare_search_review, validate_search_review, anchored_query, matches_compound_subject


def hit(title):
    return SimpleNamespace(title=title,url='https://example.org/'+title,snippet=title)


def test_compound_terms_do_not_confuse_shared_acronyms_or_change_english_prose():
    assert anchored_query('KV Cache 迁移')=='"KV Cache" 迁移'
    assert not matches_compound_subject('KV Cache 迁移','KV 广告','Key Visual')
    assert matches_compound_subject('KV Cache 迁移','KV-cache transfer','')
    assert anchored_query('When should I recompute caches?')=='When should I recompute caches?'


def test_relevance_retry_is_bounded_and_never_returns_rejected_hits():
    calls=[];events=[]
    def search(q):
        calls.append(q)
        return [hit('ads')] if len(calls)==1 else [hit('cache')]
    def review(req):
        if req.count == 0:
            return json.dumps({'digest':req.digest,'selected':[],'retry_query':None})
        return json.dumps({'digest':req.digest,'selected':[] if len(calls)==1 else [0],
                          'retry_query':'"KV Cache" migration recomputation' if len(calls)==1 else None})
    d=WebDecision('always','guest',True,'approved','KV Cache 迁移')
    result=execute_web_research(d,search,lambda *a:events.append(a),review=review)
    assert [h.title for h in result]==['cache'] and len(calls)==2
    assert events[-1][0]=='completed'
    def missing(req):
        return json.dumps({'digest':req.digest,'selected':[],'retry_query':'faithful query '+str(len(json.loads(req.payload)['attempted_queries']))})
    calls.clear()
    assert execute_web_research(d,search,lambda *a:None,review=missing)==[]
    assert len(calls)==2


def test_malformed_or_stale_relevance_fails_closed():
    req=prepare_search_review('query',[hit('a')])
    for patch in ({'digest':'stale'},{'selected':[1]},{'selected':[True]},{'selected':[0,0]},
                  {'selected':[0],'retry_query':'another'}, {'selected':[],'retry_query':'api_key=secret'}):
        raw={'digest':req.digest,'selected':[0],'retry_query':None,**patch}
        with pytest.raises(ValueError):validate_search_review(req,json.dumps(raw))
    events=[]
    d=WebDecision('always','guest',True,'approved','query')
    assert execute_web_research(d,lambda q:[hit('a')],lambda *a:events.append(a),review=lambda _: '{}')==[]
    assert events[-1][0]=='failed'


def test_provider_timeout_gets_one_distinct_read_retry_without_changing_subject():
    calls=[]; events=[]
    def search(query):
        calls.append(query)
        if len(calls)==1:
            raise TimeoutError('untrusted provider error')
        return [hit('cache')]
    def review(req):
        data=json.loads(req.payload)
        assert data['query']=='KV Cache 迁移'
        return json.dumps({'digest':req.digest,'selected':[0] if req.count else [],
            'retry_query':None if req.count else ('cache migration' if data['attempted_queries'] else 'KV Cache 迁移')})
    result=execute_web_research(WebDecision('auto','guest',True,'approved','KV Cache 迁移'),
        search,lambda *e:events.append(e),review=review)
    assert len(result)==1 and calls==['KV Cache 迁移','cache migration']
    assert events[-1][0]=='completed' and 'untrusted provider error' not in str(events)


def test_late_provider_cannot_replace_successful_retry_or_extend_total_deadline():
    from threading import Event
    from time import monotonic
    release=Event(); finished=Event(); calls=[];events=[]
    def search(query):
        calls.append(query)
        if query=='first':
            try:
                release.wait(2)
                return [hit('stale')]
            finally:
                finished.set()
        return [hit('current')]
    def review(req):
        data=json.loads(req.payload)
        return json.dumps({'digest':req.digest,'selected':[0] if req.count else [],
            'retry_query':None if req.count else ('second' if data['attempted_queries'] else 'first')})
    started=monotonic()
    try:
        result=execute_web_research(WebDecision('auto','guest',True,'approved','query'),
            search,lambda *a:events.append(a),review=review,timeout_seconds=1,search_timeout_seconds=.03,
            public_context='published computing research scope')
        assert [h.title for h in result]==['current'] and monotonic()-started < 1
        saved=list(events)
    finally:
        release.set()
        assert finished.wait(1)
    assert events==saved and calls==['first','second']
