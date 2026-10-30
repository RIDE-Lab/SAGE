from threading import Event
from time import monotonic

import pytest

from sage.runtime import web_research as runtime
from sage.runtime.read_tool_deadline import ReadToolExecutor


def test_blocked_primary_leaves_time_for_backup_and_late_result_cannot_replace_it(monkeypatch):
    executor = ReadToolExecutor(capacity=2)
    monkeypatch.setattr(runtime, "search_provider_executor", executor)
    release = Event()
    events = []
    seen = []

    def blocked():
        seen.append("primary")
        release.wait(2)
        return ["late primary"]

    def backup():
        seen.append("backup")
        assert runtime.web_research_deadline() > monotonic()
        return ["usable backup"]

    try:
        with runtime.web_research_budget(.3):
            result = runtime.search_provider_fallback(
                [("primary", blocked), ("backup", backup)], observe=lambda *e: events.append(e))
        assert result == ["usable backup"]
        assert seen == ["primary", "backup"]
        assert events[0] == ("primary", "failed", "ReadToolDeadlineExceeded")
        assert events[1] == ("backup", "completed", None)
    finally:
        release.set()
        executor.close()
    assert result == ["usable backup"]


def test_unused_time_is_available_to_later_providers_and_success_stops_fallback():
    allowances = []
    def empty():
        allowances.append(runtime.web_research_deadline() - monotonic())
        return []
    def success():
        allowances.append(runtime.web_research_deadline() - monotonic())
        return ["evidence"]
    with runtime.web_research_budget(3):
        assert runtime.search_provider_fallback([
            ("empty", empty), ("success", success),
            ("unused", lambda: pytest.fail("must not duplicate successful search"))]) == ["evidence"]
    assert .9 < allowances[0] <= 1
    assert 1.3 < allowances[1] <= 1.5


def test_failed_and_successfully_empty_providers_are_not_conflated():
    def failure():
        raise TimeoutError("secret must not reach observer")
    events = []
    with runtime.web_research_budget(1):
        with pytest.raises(RuntimeError, match="providers_unavailable"):
            runtime.search_provider_fallback([("down", failure)], observe=lambda *e: events.append(e))
        assert runtime.search_provider_fallback([("down", failure), ("empty", lambda: [])]) == []
    assert events == [("down", "failed", "TimeoutError")]


def test_requires_an_existing_deadline_and_does_not_extend_parent():
    with pytest.raises(ValueError, match="deadline_required"):
        runtime.search_provider_fallback([])
    with runtime.web_research_budget(1):
        parent = runtime.web_research_deadline()
        with runtime.web_research_budget(50):
            assert runtime.search_provider_fallback([("read", lambda: [runtime.web_research_deadline() <= parent])]) == [True]
        assert runtime.web_research_deadline() == parent


def test_concurrent_outer_reads_do_not_starve_nested_provider_calls(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    outer = ReadToolExecutor(capacity=4)
    providers = ReadToolExecutor(capacity=4)
    monkeypatch.setattr(runtime, 'search_provider_executor', providers)
    barrier = Barrier(4)
    def search():
        barrier.wait(2)
        with runtime.web_research_budget(1):
            return runtime.search_provider_fallback([('healthy', lambda: ['body'])])
    try:
        with ThreadPoolExecutor(max_workers=4) as callers:
            results = list(callers.map(lambda _: outer.call(search, timeout=3), range(4)))
        assert results == [['body']] * 4
    finally:
        outer.close()
        providers.close()
