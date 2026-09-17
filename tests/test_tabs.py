"""Tab resolution. Getting this wrong means acting on the wrong page."""

from __future__ import annotations

import pytest

from conftest import FakeBackend
from tabpilot import tabs as tabs_module
from tabpilot.backends.base import TabInfo
from tabpilot.errors import NoTabError


def test_explicit_id_wins(backend: FakeBackend):
    assert tabs_module.resolve(backend, tab_id="t2").url.endswith("/two")


def test_unknown_id_lists_what_is_open(backend: FakeBackend):
    with pytest.raises(NoTabError) as caught:
        tabs_module.resolve(backend, tab_id="nope")
    message = str(caught.value)
    assert "t1" in message and "t2" in message
    assert "url_pattern" in caught.value.remedy


def test_url_pattern_is_a_regex(backend: FakeBackend):
    assert tabs_module.resolve(backend, url_pattern=r"example\.com/two").id == "t2"


def test_invalid_regex_is_reported_as_such(backend: FakeBackend):
    with pytest.raises(NoTabError, match="not a valid regex"):
        tabs_module.resolve(backend, url_pattern="[unclosed")


def test_no_match_lists_what_is_open(backend: FakeBackend):
    with pytest.raises(NoTabError, match="matches"):
        tabs_module.resolve(backend, url_pattern="nothing-like-this")


def test_focus_breaks_an_ambiguous_pattern():
    backend = FakeBackend(tabs=[
        TabInfo(id="a", title="A", url="https://example.com/x/1"),
        TabInfo(id="b", title="B", url="https://example.com/x/2", active=True),
    ])
    assert tabs_module.resolve(backend, url_pattern=r"example\.com/x").id == "b"


def test_ambiguity_without_focus_refuses_to_guess():
    """Silently picking one would act on the wrong page half the time."""
    backend = FakeBackend(tabs=[
        TabInfo(id="a", title="A", url="https://example.com/x/1"),
        TabInfo(id="b", title="B", url="https://example.com/x/2"),
    ])
    with pytest.raises(NoTabError) as caught:
        tabs_module.resolve(backend, url_pattern=r"example\.com/x")
    assert "2 tabs match" in str(caught.value)
    assert "tab_id" in caught.value.remedy


def test_falls_back_to_the_focused_tab(backend: FakeBackend):
    assert tabs_module.resolve(backend).id == "t1"


def test_a_lone_tab_needs_no_focus():
    backend = FakeBackend(tabs=[TabInfo(id="only", title="Only", url="https://example.com")])
    assert tabs_module.resolve(backend).id == "only"


def test_several_unfocused_tabs_is_an_error():
    backend = FakeBackend(tabs=[
        TabInfo(id="a", title="A", url="https://a.example"),
        TabInfo(id="b", title="B", url="https://b.example"),
    ])
    with pytest.raises(NoTabError, match="which tab you meant"):
        tabs_module.resolve(backend)


def test_no_tabs_at_all(backend: FakeBackend):
    with pytest.raises(NoTabError, match="No tabs are open"):
        tabs_module.resolve(FakeBackend(tabs=[]))


def test_render_list_filters_by_pattern(backend: FakeBackend):
    rendered = tabs_module.render_list(backend.list_tabs(), r"/two$")
    assert "t2" in rendered and "t1" not in rendered


def test_render_list_says_so_when_empty(backend: FakeBackend):
    assert "No open tabs" in tabs_module.render_list([])
