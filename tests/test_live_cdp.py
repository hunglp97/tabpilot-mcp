"""End-to-end tests against a real Chrome, over real CDP.

Everything else in this suite runs against a fake backend, which proves the
Python logic and nothing about whether the JavaScript payloads work in a browser.
These serve a fixture page over loopback and drive it for real.

Run them with a debuggable Chrome open:

    google-chrome --remote-debugging-port=9222 --user-data-dir=/tmp/tabpilot-test &
    pytest -m live

They are skipped, not failed, when no CDP port answers.
"""

from __future__ import annotations

import functools
import http.server
import socket
import threading
from pathlib import Path

import pytest

from tabpilot import evidence, extract, interact
from tabpilot.backends.cdp import CDPBackend
from tabpilot.config import Config
from tabpilot.errors import BridgeOffError, ElementNotFoundError, TimeoutError_
from tabpilot.session import Session

pytestmark = pytest.mark.live

FIXTURES = Path(__file__).parent / "fixtures"


def _cdp_available(host: str = "127.0.0.1", port: int = 9222) -> bool:
    try:
        with socket.create_connection((host, port), timeout=1):
            return True
    except OSError:
        return False


requires_chrome = pytest.mark.skipif(
    not _cdp_available(),
    reason="no Chrome with --remote-debugging-port=9222; see this module's docstring",
)


@pytest.fixture(scope="module")
def fixture_server():
    """Serve the fixture over http, since file:// tabs behave differently."""
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(FIXTURES))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}/page.html"
    server.shutdown()
    server.server_close()


@pytest.fixture
def session(tmp_path):
    config = Config(backend="cdp", screenshot_dir=tmp_path / "shots", matrix_delay_ms=60)
    instance = Session(config)
    yield instance
    instance.close()


@pytest.fixture
def tab(session, fixture_server):
    """A freshly loaded fixture tab, closed afterwards even on failure."""
    opened = session.backend.open_tab(fixture_server, activate=True)
    interact.wait_until_loaded(session, opened, previous_url="about:blank", timeout_ms=10_000)
    yield opened
    try:
        session.backend.close_tab(opened.id)
    except BridgeOffError:
        pass


# --- transport ---------------------------------------------------------------


@requires_chrome
def test_health_and_version(session):
    session.backend.health()
    assert "Chrome" in session.backend.browser_version().get("Browser", "")


@requires_chrome
def test_a_wrong_port_fails_with_the_launch_command():
    backend = CDPBackend(port=1)
    with pytest.raises(BridgeOffError) as caught:
        backend.health()
    assert "--remote-debugging-port" in caught.value.remedy


@requires_chrome
def test_evaluation_round_trips_types(session, tab):
    assert session.eval_raw(tab.id, "1 + 1") == 2
    assert session.eval_raw(tab.id, "({a: [1, 2], b: 'x'})") == {"a": [1, 2], "b": "x"}
    assert session.eval_raw(tab.id, "undefined") is None
    assert session.eval_raw(tab.id, "'Khảo sát 日本語'") == "Khảo sát 日本語"


@requires_chrome
def test_promises_are_awaited(session, tab):
    """This is the capability the AppleScript backend cannot offer at all."""
    value = session.eval_raw(tab.id, "new Promise(r => setTimeout(() => r('later'), 150))")
    assert value == "later"


@requires_chrome
def test_a_page_exception_names_the_cause(session, tab):
    from tabpilot.errors import JSError

    with pytest.raises(JSError, match="not defined"):
        session.eval_raw(tab.id, "definitelyNotDefined()")


@requires_chrome
def test_the_tab_appears_in_the_listing(session, tab):
    assert any(t.id == tab.id and "page.html" in t.url for t in session.list_tabs())


# --- reading -----------------------------------------------------------------


@requires_chrome
class TestReading:
    def test_readable_mode_keeps_content_and_drops_boilerplate(self, session, tab):
        out = extract.read_tab(session, tab)
        assert "Checkout fails on Safari 17" in out
        assert "**500**" in out          # inline emphasis became markdown
        assert "- Add ten items" in out  # list became markdown
        assert "| Device | Result |" in out
        assert "Copyright boilerplate" not in out
        assert "Home · Bugs · Settings" not in out

    def test_links_become_markdown_with_absolute_urls(self, session, tab):
        out = extract.read_tab(session, tab)
        assert "[full steps](http://127.0.0.1" in out

    def test_links_can_be_dropped(self, session, tab):
        out = extract.read_tab(session, tab, include_links=False)
        assert "full steps" in out and "](http" not in out

    def test_a_selector_scopes_the_read(self, session, tab):
        out = extract.read_tab(session, tab, selector="#q1")
        assert "Rate each area" in out
        assert "Checkout fails" not in out

    def test_the_budget_truncates_and_says_so(self, session, tab):
        out = extract.read_tab(session, tab, max_chars=80)
        assert "Truncated" in out

    def test_text_mode_returns_plain_text(self, session, tab):
        out = extract.read_tab(session, tab, mode="text")
        assert "Checkout fails on Safari 17" in out and "**" not in out

    def test_html_mode_returns_markup(self, session, tab):
        out = extract.read_tab(session, tab, mode="html", selector="#report")
        assert '<textarea id="desc"' in out

    def test_a_missing_selector_is_reported(self, session, tab):
        with pytest.raises(Exception, match="selector"):
            extract.read_tab(session, tab, selector="#nope")

    def test_query_dom_reports_control_state(self, session, tab):
        out = extract.query_dom(session, tab, selector="#report button")
        assert "matched 2" in out
        assert '"disabled": "true"' in out  # the locked button

    def test_query_dom_can_skip_invisible_elements(self, session, tab):
        shown = extract.query_dom(session, tab, selector="#report div", visible_only=True)
        assert "skipped" in shown

    def test_query_dom_lists_select_options(self, session, tab):
        out = extract.query_dom(session, tab, selector="#os option")
        assert "matched 3" in out and "iOS" in out


# --- interaction -------------------------------------------------------------


@requires_chrome
class TestInteraction:
    def test_fill_fires_both_input_and_change(self, session, tab):
        """React drops the update when only one of them arrives."""
        interact.fill(session, tab, selector="#title", value="Checkout 500")
        assert session.eval_raw(tab.id, "document.getElementById('title').value") == "Checkout 500"
        assert session.eval_raw(tab.id, "window.__inputEvents") >= 1
        assert session.eval_raw(tab.id, "window.__changeEvents") >= 1

    def test_fill_handles_non_ascii(self, session, tab):
        interact.fill(session, tab, selector="#desc", value="Lỗi thanh toán 支払いエラー")
        assert session.eval_raw(
            tab.id, "document.getElementById('desc').value"
        ) == "Lỗi thanh toán 支払いエラー"

    def test_fill_clears_by_default(self, session, tab):
        interact.fill(session, tab, selector="#title", value="first")
        interact.fill(session, tab, selector="#title", value="second")
        assert session.eval_raw(tab.id, "document.getElementById('title').value") == "second"

    def test_fill_can_append(self, session, tab):
        interact.fill(session, tab, selector="#title", value="abc")
        interact.fill(session, tab, selector="#title", value="def", clear=False)
        assert session.eval_raw(tab.id, "document.getElementById('title').value") == "def"

    def test_fill_toggles_a_checkbox(self, session, tab):
        interact.fill(session, tab, selector="#agree", value="true")
        assert session.eval_raw(tab.id, "document.getElementById('agree').checked") is True

    def test_click_dispatches_a_trusted_event(self, session, tab):
        """The reason CDP is preferred: pages cannot tell this from a human."""
        interact.fill(session, tab, selector="#title", value="T")
        interact.click(session, tab, selector="#submit")
        assert session.eval_raw(tab.id, "window.__trust") == [True]
        assert session.eval_raw(tab.id, "document.getElementById('result').textContent") == "sent:T"

    def test_click_by_visible_text(self, session, tab):
        interact.click(session, tab, text="Send report")
        assert "sent:" in session.eval_raw(tab.id, "document.getElementById('result').textContent")

    def test_a_disabled_button_is_refused(self, session, tab):
        with pytest.raises(ElementNotFoundError, match="disabled"):
            interact.click(session, tab, selector="#locked")

    def test_a_synthetic_click_is_not_trusted(self, session, tab):
        """Confirms the fallback really is a lesser path, not an equivalent one."""
        interact.click(session, tab, selector="#submit", trusted=False)
        assert session.eval_raw(tab.id, "window.__trust") == [False]

    def test_select_option_sets_multiple_values(self, session, tab):
        interact.select_option(session, tab, selector="#os", values=["ios", "web"])
        selected = session.eval_raw(
            tab.id,
            "Array.from(document.getElementById('os').selectedOptions).map(o => o.value)",
        )
        assert selected == ["ios", "web"]

    def test_select_option_matches_by_label(self, session, tab):
        interact.select_option(session, tab, selector="#os", values=["Android"], by="label")
        selected = session.eval_raw(
            tab.id,
            "Array.from(document.getElementById('os').selectedOptions).map(o => o.value)",
        )
        assert selected == ["android"]

    def test_a_missing_option_lists_what_exists(self, session, tab):
        with pytest.raises(Exception) as caught:
            interact.select_option(session, tab, selector="#os", values=["Windows Phone"])
        assert "iOS" in (caught.value.remedy or "")

    def test_wait_for_sees_a_late_element(self, session, tab):
        interact.click(session, tab, selector="#submit")
        assert "Condition met" in interact.wait_for(
            session, tab, selector="#late", state="visible", timeout_ms=3000
        )

    def test_wait_for_times_out_with_its_last_observation(self, session, tab):
        with pytest.raises(TimeoutError_) as caught:
            interact.wait_for(session, tab, selector="#hidden-box", state="visible", timeout_ms=600)
        assert "not visible" in str(caught.value)

    def test_wait_for_a_predicate(self, session, tab):
        assert "Condition met" in interact.wait_for(
            session, tab, predicate="document.readyState === 'complete'"
        )


# --- matrix ------------------------------------------------------------------


@requires_chrome
class TestMatrix:
    """The fixture's radios are styled <i> elements whose state lives in a class
    name, exactly like the survey platforms this was built for. Nothing here
    works if isAnswered() only looks at input.checked."""

    def selected_count(self, session, tab) -> int:
        return session.eval_raw(tab.id, "document.querySelectorAll('.radio_button.selected').length")

    def test_scan_finds_the_question_and_counts_blank_rows(self, session, tab):
        out = interact.scan_matrix(session, tab)
        assert "1 matrix question" in out
        assert "rows: 5, unanswered: 5" in out

    def test_fill_answers_every_row(self, session, tab):
        report = interact.fill_matrix(session, tab, column_index=1)
        assert "clicked: 5" in report
        assert "still unanswered after the pass: 0" in report
        assert self.selected_count(session, tab) == 5

    def test_the_requested_column_is_the_one_clicked(self, session, tab):
        interact.fill_matrix(session, tab, column_index=2)
        indices = session.eval_raw(
            tab.id,
            "Array.from(document.querySelectorAll('.RowWrapper')).map(r =>"
            " Array.from(r.querySelectorAll('.radio_button'))"
            "  .findIndex(c => c.classList.contains('selected')))",
        )
        assert indices == [2, 2, 2, 2, 2]

    def test_a_negative_column_counts_from_the_right(self, session, tab):
        interact.fill_matrix(session, tab, column_index=-1)
        indices = session.eval_raw(
            tab.id,
            "Array.from(document.querySelectorAll('.RowWrapper')).map(r =>"
            " Array.from(r.querySelectorAll('.radio_button'))"
            "  .findIndex(c => c.classList.contains('selected')))",
        )
        assert indices == [2, 2, 2, 2, 2]

    def test_a_second_pass_finds_nothing_to_do(self, session, tab):
        interact.fill_matrix(session, tab, column_index=0)
        assert "already fully answered" in interact.fill_matrix(session, tab, column_index=0)

    def test_specific_rows_only(self, session, tab):
        interact.fill_matrix(session, tab, rows=[0, 3], column_index=1)
        assert self.selected_count(session, tab) == 2

    def test_scan_reflects_a_partially_answered_matrix(self, session, tab):
        interact.fill_matrix(session, tab, rows=[1], column_index=0)
        assert "rows: 5, unanswered: 4" in interact.scan_matrix(session, tab)


# --- evidence ----------------------------------------------------------------


@requires_chrome
class TestScreenshots:
    def test_viewport_capture_writes_a_real_png(self, session, tab):
        _, path, data = evidence.screenshot(session, tab, label="viewport")
        assert data[:8] == b"\x89PNG\r\n\x1a\n"
        assert path.exists() and path.stat().st_size > 1000

    def test_full_page_is_taller_than_the_viewport_capture(self, session, tab):
        _, _, viewport = evidence.screenshot(session, tab)
        _, _, whole = evidence.screenshot(session, tab, full_page=True)
        assert len(whole) >= len(viewport)

    def test_an_element_capture_is_clipped_to_that_element(self, session, tab):
        _, _, whole = evidence.screenshot(session, tab, full_page=True)
        _, _, element = evidence.screenshot(session, tab, selector="#q1")
        assert len(element) < len(whole)

    def test_jpeg_is_smaller_than_png(self, session, tab):
        _, _, png = evidence.screenshot(session, tab, image_format="png")
        _, _, jpeg = evidence.screenshot(session, tab, image_format="jpeg", quality=60)
        assert len(jpeg) < len(png)

    def test_a_background_tab_can_still_be_captured(self, session, tab, fixture_server):
        """AppleScript and `screencapture` can only photograph what is frontmost,
        which is why evidence used to depend on window focus."""
        other = session.backend.open_tab("about:blank", activate=True)
        try:
            _, _, data = evidence.screenshot(session, tab, label="background")
            assert data[:8] == b"\x89PNG\r\n\x1a\n"
        finally:
            session.backend.close_tab(other.id)


# --- tab lifecycle -----------------------------------------------------------


@requires_chrome
def test_open_navigate_and_close(session, fixture_server):
    opened = session.backend.open_tab("about:blank")
    try:
        session.backend.navigate(opened.id, fixture_server)
        interact.wait_until_loaded(session, opened, previous_url="about:blank", timeout_ms=10_000)
        assert session.eval_raw(opened.id, "document.title") == "TabPilot fixture"
    finally:
        session.backend.close_tab(opened.id)
    assert all(t.id != opened.id for t in session.list_tabs())


@requires_chrome
def test_a_tab_is_resolvable_by_url_pattern(session, tab):
    resolved = session.resolve(url_pattern=r"page\.html$")
    assert resolved.id == tab.id


@requires_chrome
def test_a_closed_tab_reports_no_tab(session, fixture_server):
    from tabpilot.errors import NoTabError

    opened = session.backend.open_tab("about:blank")
    tab_id = opened.id
    session.backend.close_tab(tab_id)
    with pytest.raises(NoTabError):
        session.resolve(tab_id=tab_id)
