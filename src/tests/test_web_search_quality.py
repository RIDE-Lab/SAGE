import json
from types import SimpleNamespace
import pytest
from sage.runtime.web_research import WebDecision, execute_web_research
from sage.runtime.web_search_quality import prepare_search_review, validate_search_review, anchored_query


def hit(title):
    return SimpleNamespace(title=title,url='https://example.org/'+title,snippet=title)


def test_compound_terms_do_not_confuse_shared_acronyms_or_change_english_prose():
    assert anchored_query('KV Cache 迁移')=='"KV Cache" 迁移'
    assert anchored_query('When should I recompute caches?')=='When should I recompute caches?'


def test_relevance_retry_is_bounded_and_never_returns_rejected_hits():
    calls=[];events=[]
    def search(q):
        calls.append(q)
        return [hit('ads')] if len(calls)==1 else [hit('cache')]
    def review(req):
        if req.count == 0:
            return receipt(req, {'digest':req.digest,'selected':[],'retry_query':None})
        return receipt(req, {'digest':req.digest,'selected':[] if len(calls)==1 else [0],
                          'retry_query':'"KV Cache" migration recomputation' if len(calls)==1 else None})
    d=WebDecision('always','guest',True,'approved','KV Cache 迁移')
    result=execute_web_research(d,search,lambda *a:events.append(a),review=review)
    assert [h.title for h in result]==['cache'] and len(calls)==2
    assert events[-1][0]=='completed'
    def missing(req):
        return receipt(req, {'digest':req.digest,'selected':[],'retry_query':'faithful query '+str(len(json.loads(req.payload)['attempted_queries']))})
    calls.clear()
    assert execute_web_research(d,search,lambda *a:None,review=missing)==[]
    assert len(calls)==2


def test_malformed_or_stale_relevance_fails_closed():
    req=prepare_search_review('query',[hit('a')])
    for patch in ({'digest':'stale'},{'selected':[1]},{'selected':[True]},{'selected':[0,0]},
                  {'selected':[],'retry_query':'api_key=secret'}):
        raw={'digest':req.digest,'selected':[0],'retry_query':None,**patch}
        with pytest.raises(ValueError):validate_search_review(req,receipt(req, raw))
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
        return receipt(req, {'digest':req.digest,'selected':[0] if req.count else [],
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
        return receipt(req, {'digest':req.digest,'selected':[0] if req.count else [],
            'domain_context':None,
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


def test_public_scope_is_explicit_bound_and_applied_to_provider_query():
    req = prepare_search_review('优化实验', [], public_context='Computer systems research')
    raw = {'digest': req.digest, 'selected': [], 'retry_query': 'performance evaluation',
           'domain_context': 'Computer systems research'}
    assert validate_search_review(req, receipt(req, raw))[1] == '"Computer systems research" performance evaluation'
    # An explicit question outside the site's field need not be constrained.
    raw.update(domain_context=None, retry_query='weather forecast')
    assert validate_search_review(req, receipt(req, raw))[1] == 'weather forecast'
    raw['domain_context'] = 'PRIVATE inferred research topic'
    with pytest.raises(ValueError, match='unapproved search domain'):
        validate_search_review(req, receipt(req, raw))
    del raw['domain_context']
    with pytest.raises(ValueError):
        validate_search_review(req, receipt(req, raw))


def test_only_accepted_sources_are_hydrated_and_late_bodies_do_not_erase_excerpts():
    from threading import Event
    release = Event(); finished = Event(); events = []; extracted = []
    candidates = [hit('irrelevant'), hit('source')]
    def review(req):
        return receipt(req, {'digest': req.digest, 'selected': [1] if req.count else [], 'retry_query': None})
    def hydrate(hits):
        extracted.extend(h.title for h in hits)
        try:
            release.wait(2)
            return [hit('late')]
        finally:
            finished.set()
    try:
        result = execute_web_research(WebDecision('auto','guest',True,'approved','query'),
            lambda q: candidates, lambda *e: events.append(e), review=review,
            hydrate=hydrate, search_timeout_seconds=.03, timeout_seconds=1)
        assert [h.title for h in result] == ['source']
        assert extracted == ['source'] and events[-1][0] == 'completed'
        snapshot = list(events)
    finally:
        release.set()
        assert finished.wait(1)
    assert events == snapshot


def receipt(req, data):
    if req.count and 'candidate_roles' not in data:
        data['candidate_roles'] = [dict(id=i, studied_object='fixture subject',
            measured_outcome='fixture relation', role='direct' if i in data['selected'] else 'irrelevant')
            for i in range(req.count)]
    return json.dumps(data)


def test_background_cannot_be_promoted_by_selected_ids_and_roles_must_be_complete():
    req = prepare_search_review('computer performance', [hit('computer'), hit('advertising')])
    raw = dict(digest=req.digest, selected=[0,1], retry_query=None, candidate_roles=[
        dict(id=0, studied_object='software', measured_outcome='latency', role='direct'),
        dict(id=1, studied_object='customers', measured_outcome='sales', role='background')])
    assert validate_search_review(req, json.dumps(raw))[0] == (0,)
    raw['candidate_roles'][1]['id'] = 0
    with pytest.raises(ValueError, match='invalid search candidate role'):
        validate_search_review(req, json.dumps(raw))
    raw['candidate_roles'] = []
    with pytest.raises(ValueError, match='incomplete search candidate roles'):
        validate_search_review(req, json.dumps(raw))


def test_partial_sources_trigger_complementary_read_and_deduplicate_urls():
    queries = []; events = []
    def search(query):
        queries.append(query)
        return [hit('one')] if len(queries) == 1 else [hit('one'), hit('two')]
    def review(req):
        data = json.loads(req.payload)
        if not req.count:
            return receipt(req, dict(digest=req.digest, selected=[], retry_query='first'))
        return receipt(req, dict(digest=req.digest, selected=list(range(req.count)),
            retry_query='complementary relation' if len(queries) == 1 else None))
    result = execute_web_research(WebDecision('auto','guest',True,'approved','public comparison'),
        search, lambda *e: events.append(e), review=review)
    assert queries == ['first','complementary relation']
    assert [h.title for h in result] == ['one','two']
    assert events[-1][0] == 'completed'


def test_complementary_provider_failure_preserves_accepted_source():
    queries = []
    def search(query):
        queries.append(query)
        if len(queries) == 2:
            raise TimeoutError('second endpoint')
        return [hit('one')]
    def review(req):
        return receipt(req, dict(digest=req.digest, selected=[0] if req.count else [],
            retry_query='complement' if req.count else 'first'))
    result = execute_web_research(WebDecision('auto','guest',True,'approved','public comparison'),
        search, lambda *e: None, review=review)
    assert len(queries) == 2 and [h.title for h in result] == ['one']
