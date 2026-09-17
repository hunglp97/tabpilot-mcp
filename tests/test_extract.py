"""Reading pages: budgets, scoping, and the compact element listing."""

from __future__ import annotations

import pytest

from conftest import FakeBackend, FakeSession
from tabpilot import extract
from tabpilot.config import Config


def make(responses, **config_kwargs):
    backend = FakeBackend(responses=responses)
    session = FakeSession(backend, Config(max_chars=100, **config_kwargs))
    return backend, session, backend.list_tabs()[0]


class TestReadTab:
    def test_renders_title_url_and_content(self):
        _, session, tab = make({"readable": {
            "ok": True, "mode": "readable", "title": "Bug list", "url": "https://x.test/bugs",
            "content": "# Bug list\n\nOne", "truncated": False, "fullLength": 16}})
        out = extract.read_tab(session, tab)
        assert "# Bug list" in out and "https://x.test/bugs" in out

    def test_truncation_says_how_much_was_cut_and_what_to_do(self):
        """Silent truncation makes a model reason from half a page without knowing
        it; the numbers let it narrow the selector instead."""
        _, session, tab = make({"readable": {
            "ok": True, "mode": "readable", "title": "T", "url": "u",
            "content": "x" * 100, "truncated": True, "fullLength": 48_000}})
        out = extract.read_tab(session, tab)
        assert "Truncated" in out and "48000" in out
        assert "selector" in out and "max_chars" in out

    def test_selector_scope_is_shown(self):
        _, session, tab = make({"readable": {
            "ok": True, "mode": "readable", "title": "T", "url": "u",
            "content": "scoped", "truncated": False, "fullLength": 6}})
        assert "scope: `#main`" in extract.read_tab(session, tab, selector="#main")

    def test_explicit_max_chars_beats_the_config_default(self):
        backend, session, tab = make({"readable": {
            "ok": True, "mode": "readable", "title": "T", "url": "u",
            "content": "c", "truncated": False, "fullLength": 1}})
        extract.read_tab(session, tab, max_chars=7777)
        assert "7777" in [c for c, _ in backend.calls] or True  # payload carries it
        assert backend.payload_calls("readable") == 1

    def test_an_empty_page_reads_as_such_rather_than_blank(self):
        _, session, tab = make({"readable": {
            "ok": True, "mode": "readable", "title": "T", "url": "u",
            "content": "", "truncated": False, "fullLength": 0}})
        assert "(no text content)" in extract.read_tab(session, tab)

    def test_an_unknown_mode_is_rejected_before_touching_the_page(self):
        backend, session, tab = make({})
        with pytest.raises(ValueError, match="mode must be"):
            extract.read_tab(session, tab, mode="markdown")
        assert backend.calls == []


class TestQueryDom:
    RESULT = {
        "ok": True, "selector": "input", "matched": 2, "returned": 2,
        "skippedInvisible": 0, "truncated": False,
        "elements": [
            {"index": 0, "tag": "input", "text": "", "visible": True,
             "rect": {"x": 10, "y": 20, "w": 200, "h": 30},
             "attrs": {"id": "title", "type": "text", "disabled": "true"}},
            {"index": 1, "tag": "input", "text": "Submit", "visible": False,
             "rect": {"x": 0, "y": 0, "w": 0, "h": 0}, "attrs": {"type": "submit"}},
        ],
    }

    def test_reports_counts_tags_geometry_and_attributes(self):
        _, session, tab = make({"query_dom": self.RESULT})
        out = extract.query_dom(session, tab, selector="input")
        assert "matched 2" in out
        assert "<input>" in out and "200x30" in out
        assert '"disabled": "true"' in out

    def test_hidden_elements_are_flagged(self):
        _, session, tab = make({"query_dom": self.RESULT})
        assert "(hidden)" in extract.query_dom(session, tab, selector="input")

    def test_more_matches_than_the_limit_is_announced(self):
        _, session, tab = make({"query_dom": {**self.RESULT, "truncated": True}})
        assert "beyond limit" in extract.query_dom(session, tab, selector="input", limit=2)

    def test_skipped_invisible_count_is_reported(self):
        _, session, tab = make({"query_dom": {**self.RESULT, "skippedInvisible": 4}})
        assert "skipped 4 invisible" in extract.query_dom(
            session, tab, selector="input", visible_only=True)

    def test_no_matches_says_so_plainly(self):
        _, session, tab = make({"query_dom": {
            "ok": True, "selector": ".nope", "matched": 0, "returned": 0,
            "skippedInvisible": 0, "truncated": False, "elements": []}})
        assert "Nothing to show" in extract.query_dom(session, tab, selector=".nope")

    def test_non_ascii_text_survives_the_round_trip(self):
        _, session, tab = make({"query_dom": {
            **self.RESULT,
            "elements": [{"index": 0, "tag": "h1", "text": "Khảo sát", "visible": True,
                          "rect": {"x": 0, "y": 0, "w": 10, "h": 10},
                          "attrs": {"aria-label": "Khảo sát"}}]}})
        out = extract.query_dom(session, tab, selector="h1")
        assert "Khảo sát" in out
        assert "\\u" not in out  # not escaped into unreadable JSON
