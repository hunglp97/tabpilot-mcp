"""Screenshots. On a server this is the only thing that proves anything happened."""

from __future__ import annotations

import pytest

from conftest import FakeBackend, FakeSession
from tabpilot import evidence
from tabpilot.backends.base import Capability
from tabpilot.config import Config
from tabpilot.errors import ElementNotFoundError, UnsupportedByBackendError


def make(tmp_path, responses=None, capabilities=None):
    backend = FakeBackend(responses=responses or {}, capabilities=capabilities)
    session = FakeSession(backend, Config(screenshot_dir=tmp_path / "shots"))
    return backend, session, backend.list_tabs()[0]


def test_saves_the_file_and_reports_where(tmp_path):
    backend, session, tab = make(tmp_path)
    summary, path, data = evidence.screenshot(session, tab, label="checkout error")
    assert path.exists() and path.read_bytes() == data
    assert str(path) in summary
    assert path.name.endswith("-checkout-error.png")


def test_the_label_becomes_a_safe_filename(tmp_path):
    _, session, tab = make(tmp_path)
    _, path, _ = evidence.screenshot(session, tab, label="Khảo sát / 50% off?!")
    assert "/" not in path.name and "%" not in path.name and "?" not in path.name


def test_falls_back_to_the_tab_title_when_unlabelled(tmp_path):
    _, session, tab = make(tmp_path)
    _, path, _ = evidence.screenshot(session, tab)
    assert "first" in path.name


def test_full_page_is_passed_through_to_the_backend(tmp_path):
    backend, session, tab = make(tmp_path)
    summary, _, _ = evidence.screenshot(session, tab, full_page=True)
    kwargs = dict(backend.calls)["screenshot"]
    assert kwargs["full_page"] is True
    assert "full page" in summary


def test_element_clip_is_converted_from_viewport_to_page_coordinates(tmp_path):
    """locate reports viewport coordinates; a CDP clip is in page coordinates.
    Skipping the scroll offset crops the wrong region on any scrolled page."""
    backend, session, tab = make(tmp_path, responses={"locate": {
        "ok": True, "rect": {"x": 40, "y": 60, "w": 300, "h": 120},
        "scrollX": 5, "scrollY": 900, "matched": 1, "tag": "div", "text": "",
        "disabled": False, "visible": True, "inViewport": True, "x": 0, "y": 0,
        "devicePixelRatio": 1,
    }})
    summary, _, _ = evidence.screenshot(session, tab, selector=".banner")
    clip = dict(backend.calls)["screenshot"]["clip"]
    assert clip == {"x": 45, "y": 960, "width": 300, "height": 120, "scale": 1}
    assert "300x120" in summary


def test_a_zero_sized_element_is_refused(tmp_path):
    _, session, tab = make(tmp_path, responses={"locate": {
        "ok": True, "rect": {"x": 0, "y": 0, "w": 0, "h": 0}, "scrollX": 0, "scrollY": 0,
        "matched": 1, "tag": "div", "text": "", "disabled": False, "visible": False,
        "inViewport": False, "x": 0, "y": 0, "devicePixelRatio": 1,
    }})
    with pytest.raises(ElementNotFoundError, match="no size on screen"):
        evidence.screenshot(session, tab, selector=".hidden")


def test_a_backend_without_screenshots_says_how_to_get_them(tmp_path):
    """The AppleScript path used to skip capture silently, so runs looked
    successful while producing no evidence at all."""
    _, session, tab = make(tmp_path, capabilities=frozenset({Capability.EVAL}))
    with pytest.raises(UnsupportedByBackendError) as caught:
        evidence.screenshot(session, tab)
    assert "CDP" in caught.value.remedy


def test_an_unknown_format_is_rejected(tmp_path):
    _, session, tab = make(tmp_path)
    with pytest.raises(ValueError, match="png"):
        evidence.screenshot(session, tab, image_format="webp")


def test_the_directory_is_created_on_demand(tmp_path):
    target = tmp_path / "deep" / "nested" / "shots"
    backend = FakeBackend()
    session = FakeSession(backend, Config(screenshot_dir=target))
    evidence.screenshot(session, backend.list_tabs()[0])
    assert target.is_dir()
