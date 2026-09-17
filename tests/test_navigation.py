"""Regressions for two races found by driving a real Chrome.

Both produced *misleading* failures rather than honest ones, which is why they
are pinned here: the symptom pointed at the caller's selector or at a tab that
looked open, not at the actual cause.
"""

from __future__ import annotations

import json

import pytest

from conftest import FakeBackend, FakeSession
from tabpilot import interact
from tabpilot.config import Config
from tabpilot.errors import JSError, TimeoutError_


class TestWaitUntilLoaded:
    """A fresh tab sits at about:blank, whose readyState is ALREADY 'complete'
    before the requested URL begins loading. Waiting only on readyState returns
    instantly, the next tool runs against a blank page, and the error surfaces
    as "no element matches your selector"."""

    def _session(self, wait_results):
        backend = FakeBackend(responses={"wait_for": list(wait_results)})
        session = FakeSession(backend, Config(timeout_ms=1000))
        return backend, session, backend.list_tabs()[0]

    def test_it_waits_for_the_url_to_change_before_checking_readystate(self):
        backend, session, tab = self._session([
            {"ok": True, "satisfied": False, "detail": "predicate -> false"},  # still about:blank
            {"ok": True, "satisfied": True, "detail": "predicate -> true"},    # url changed
            {"ok": True, "satisfied": True, "detail": "predicate -> true"},    # readyState
        ])
        interact.wait_until_loaded(session, tab, previous_url="about:blank")
        assert backend.payload_calls("wait_for") == 3

    def test_the_url_check_compares_against_the_previous_url(self):
        seen = []

        def answer(options):
            seen.append(options["predicate"])
            return {"ok": True, "satisfied": True, "detail": "predicate -> true"}

        backend = FakeBackend(responses={"wait_for": answer})
        session = FakeSession(backend, Config(timeout_ms=1000))
        interact.wait_until_loaded(
            session, backend.list_tabs()[0], previous_url="https://old.example/page"
        )
        assert json.dumps("https://old.example/page") in seen[0]
        assert "readyState" in seen[1]

    def test_a_url_that_never_changes_is_not_an_error(self):
        """A reload, or a same-page anchor, legitimately keeps the same URL.
        Failing there would break `navigate` to the page you are already on."""
        def answer(options):
            predicate = options["predicate"]
            satisfied = "readyState" in predicate  # the URL never budges
            return {"ok": True, "satisfied": satisfied, "detail": f"predicate -> {satisfied}"}

        backend = FakeBackend(responses={"wait_for": answer})
        session = FakeSession(backend, Config(timeout_ms=1000))
        result = interact.wait_until_loaded(
            session, backend.list_tabs()[0], previous_url="https://same.example/x", timeout_ms=600
        )
        assert "Condition met" in result
        assert "readyState" in result

    def test_a_document_that_never_finishes_still_times_out(self):
        backend, session, tab = self._session(
            [{"ok": True, "satisfied": False, "detail": "predicate -> false"}] * 100
        )
        with pytest.raises(TimeoutError_):
            interact.wait_until_loaded(session, tab, previous_url="about:blank", timeout_ms=500)


class TestCloseTabConfirmation:
    """`/json/close` only *requests* the close; the target lingers in
    `/json/list` afterwards. Returning immediately let a caller close a tab, list
    tabs, and still see it — or resolve a url_pattern onto the dying tab."""

    def _backend(self, list_sequence):
        from tabpilot.backends.cdp import CDPBackend

        backend = CDPBackend(port=9999)
        calls = {"list": 0}

        def fake_http(path, method="GET"):
            if path == "/json/list":
                index = min(calls["list"], len(list_sequence) - 1)
                calls["list"] += 1
                return list_sequence[index]
            return ""

        backend._http = fake_http  # type: ignore[method-assign]
        backend._drop_socket = lambda _tab_id: None  # type: ignore[method-assign]
        return backend, calls

    def test_it_returns_once_the_target_is_gone(self):
        backend, calls = self._backend([
            [{"id": "doomed", "type": "page", "url": "https://x.test"}],  # still there
            [],                                                           # gone
        ])
        backend.close_tab("doomed")
        assert calls["list"] >= 2

    def test_an_immediate_teardown_needs_only_one_check(self):
        backend, calls = self._backend([[]])
        backend.close_tab("doomed")
        assert calls["list"] == 1

    def test_a_tab_that_refuses_to_close_is_reported_not_assumed(self):
        """A beforeunload dialog keeps the tab alive. Silently claiming success
        would leave the caller believing a page it is still on was closed."""
        from tabpilot.backends import cdp

        backend, _ = self._backend([[{"id": "doomed", "type": "page", "url": "https://x.test"}]])
        original = cdp.CLOSE_CONFIRM_TIMEOUT_S
        cdp.CLOSE_CONFIRM_TIMEOUT_S = 0.2
        try:
            with pytest.raises(JSError) as caught:
                backend.close_tab("doomed")
        finally:
            cdp.CLOSE_CONFIRM_TIMEOUT_S = original
        assert "still open" in str(caught.value)
        assert "beforeunload" in caught.value.remedy or "leave" in caught.value.remedy
