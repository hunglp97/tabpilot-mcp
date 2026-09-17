"""Interaction semantics — especially the batching trap that fill_matrix exists for."""

from __future__ import annotations

import pytest

from conftest import FakeBackend, FakeSession
from tabpilot import interact
from tabpilot.backends.base import Capability
from tabpilot.config import Config
from tabpilot.errors import ElementNotFoundError, JSError, TimeoutError_


def make(responses, capabilities=None, **config_kwargs):
    backend = FakeBackend(responses=responses, capabilities=capabilities)
    session = FakeSession(backend, Config(matrix_delay_ms=0, timeout_ms=300, **config_kwargs))
    return backend, session, backend.list_tabs()[0]


LOCATED = {
    "ok": True, "matched": 1, "tag": "button", "text": "Submit", "disabled": False,
    "visible": True, "inViewport": True, "x": 100.4, "y": 200.6,
    "rect": {"x": 50, "y": 180, "w": 100, "h": 40}, "devicePixelRatio": 2,
    "scrollX": 0, "scrollY": 0,
}


class TestClick:
    def test_cdp_dispatches_a_trusted_event_at_the_centre(self):
        backend, session, tab = make({"locate": dict(LOCATED)})
        result = interact.click(session, tab, selector="button")
        assert ("click_at", (100, 201)) in backend.calls
        assert "trusted event" in result

    def test_without_trusted_input_it_falls_back_to_dom_events(self):
        backend, session, tab = make(
            {"synthetic_click": {"ok": True, "tag": "button", "text": "Submit", "matched": 1}},
            capabilities=frozenset({Capability.EVAL}),
        )
        result = interact.click(session, tab, selector="button")
        assert "synthetic events" in result
        assert backend.payload_calls("locate") == 0

    def test_a_disabled_button_is_reported_not_clicked(self):
        """Clicking a disabled control silently does nothing, which reads as a
        page bug rather than a caller mistake."""
        backend, session, tab = make({"locate": {**LOCATED, "disabled": True}})
        with pytest.raises(ElementNotFoundError, match="disabled"):
            interact.click(session, tab, selector="button")
        assert not any(call == "click_at" for call, _ in backend.calls)

    def test_offscreen_element_is_refused_rather_than_clicked_blind(self):
        backend, session, tab = make({"locate": {**LOCATED, "inViewport": False}})
        with pytest.raises(ElementNotFoundError, match="viewport"):
            interact.click(session, tab, selector="button")

    def test_needs_a_selector_or_text(self):
        _, session, tab = make({})
        with pytest.raises(ValueError, match="selector, text"):
            interact.click(session, tab)


class TestFill:
    def test_reports_what_landed_in_the_field(self):
        _, session, tab = make({"fill": {
            "ok": True, "tag": "input", "kind": "text", "value": "hello", "matchedValue": True}})
        assert "hello" in interact.fill(session, tab, selector="#q", value="hello")

    def test_warns_when_the_page_reformatted_the_value(self):
        """A date or phone mask silently rewrites input; without this warning the
        caller believes the field holds what they sent."""
        _, session, tab = make({"fill": {
            "ok": True, "tag": "input", "kind": "tel", "value": "(555) 000", "matchedValue": False}})
        assert "Warning" in interact.fill(session, tab, selector="#p", value="555000")

    def test_press_enter_uses_a_trusted_key_when_available(self):
        backend, session, tab = make({"fill": {
            "ok": True, "tag": "input", "kind": "text", "value": "x", "matchedValue": True}})
        interact.fill(session, tab, selector="#q", value="x", press_enter=True)
        assert ("press_key", "Enter") in backend.calls

    def test_press_enter_falls_back_to_key_events(self):
        backend, session, tab = make(
            {"fill": {"ok": True, "tag": "input", "kind": "text", "value": "x", "matchedValue": True},
             "raw": "sent"},
            capabilities=frozenset({Capability.EVAL}),
        )
        result = interact.fill(session, tab, selector="#q", value="x", press_enter=True)
        assert "synthetic key events" in result
        assert ("press_key", "Enter") not in backend.calls

    def test_a_disabled_field_surfaces_the_page_reason(self):
        _, session, tab = make({"fill": {"ok": False, "error": "That field is disabled."}})
        with pytest.raises(JSError, match="disabled"):
            interact.fill(session, tab, selector="#q", value="x")


class TestSelectOption:
    def test_reports_the_widget_kind(self):
        _, session, tab = make({"select_option": {
            "ok": True, "kind": "select", "selected": [{"value": "ios", "label": "iOS"}],
            "optionCount": 3}})
        assert "iOS" in interact.select_option(session, tab, selector="#os", values=["ios"])

    def test_select2_confirms_its_pills_repainted(self):
        _, session, tab = make({"select_option": {
            "ok": True, "kind": "select2", "selected": [{"value": "a", "label": "A"}],
            "select2Refreshed": True, "optionCount": 2}})
        assert "pills refreshed" in interact.select_option(session, tab, selector="#s", values=["a"])

    def test_a_missing_option_lists_what_is_actually_there(self):
        """Guessing the next value is what an agent does with a bare failure; the
        real option list ends that loop in one call."""
        _, session, tab = make({"select_option": {
            "ok": False, "error": "No option for: iOS 99",
            "available": [{"value": "17", "label": "iOS 17"}, {"value": "18", "label": "iOS 18"}],
            "optionCount": 2}})
        with pytest.raises(JSError) as caught:
            interact.select_option(session, tab, selector="#os", values=["iOS 99"])
        assert "iOS 17" in caught.value.remedy

    def test_react_select_is_routed_to_the_ui_path(self):
        _, session, tab = make({"select_option": {
            "ok": False, "kind": "react-select", "needsUiInteraction": True,
            "error": "no underlying <select>"}})
        with pytest.raises(JSError) as caught:
            interact.select_option(session, tab, selector=".picker", values=["iOS"])
        assert "click the option" in caught.value.remedy

    def test_needs_at_least_one_value(self):
        _, session, tab = make({})
        with pytest.raises(ValueError, match="at least one value"):
            interact.select_option(session, tab, selector="#os", values=[])


SCAN_28 = {
    "ok": True, "questionCount": 1,
    "questions": [{
        "index": 0, "id": "q1", "className": "matrix_question", "title": "Rate each",
        "rowCount": 28, "unansweredCount": 28, "unansweredRows": list(range(28)),
        "rows": [{"index": i, "label": f"row {i}", "cellCount": 5, "answered": False,
                  "answeredAt": -1} for i in range(28)],
    }],
}


def _scan_with_unanswered(rows):
    return {
        "ok": True, "questionCount": 1,
        "questions": [{**SCAN_28["questions"][0], "unansweredCount": len(rows),
                       "unansweredRows": rows}],
    }


class TestFillMatrix:
    """React coalesces state updates inside one task and commits only the last.
    A 28-row matrix answered in a loop leaves 27 rows blank, submit fails
    validation, and the page looks stuck. One click per call is the whole fix."""

    def test_one_click_call_per_row(self):
        backend, session, tab = make({
            "matrix_scan": [SCAN_28, _scan_with_unanswered([])],
            "matrix_click": [{"ok": True, "clicked": True, "rowIndex": i, "columnIndex": 1,
                              "label": f"row {i}", "cellCount": 5, "answeredNow": True}
                             for i in range(28)],
        })
        report = interact.fill_matrix(session, tab, column_index=1)
        assert backend.payload_calls("matrix_click") == 28
        assert "clicked: 28" in report
        assert "still unanswered after the pass: 0" in report

    def test_it_rescans_to_verify_rather_than_trusting_the_clicks(self):
        backend, session, tab = make({
            "matrix_scan": [SCAN_28, _scan_with_unanswered([])],
            "matrix_click": [{"ok": True, "clicked": True, "rowIndex": i, "columnIndex": 0,
                              "label": "", "cellCount": 5, "answeredNow": True} for i in range(28)],
        })
        interact.fill_matrix(session, tab)
        assert backend.payload_calls("matrix_scan") == 2  # before and after

    def test_rows_still_blank_afterwards_get_an_actionable_hint(self):
        backend, session, tab = make({
            "matrix_scan": [SCAN_28, _scan_with_unanswered([3, 7])],
            "matrix_click": [{"ok": True, "clicked": True, "rowIndex": i, "columnIndex": 0,
                              "label": "", "cellCount": 5, "answeredNow": True} for i in range(28)],
        })
        report = interact.fill_matrix(session, tab)
        assert "still unanswered after the pass: 2 [3, 7]" in report
        assert "delay_ms" in report

    def test_already_answered_rows_are_skipped_not_toggled_off(self):
        backend, session, tab = make({
            "matrix_scan": [_scan_with_unanswered([0, 1]), _scan_with_unanswered([])],
            "matrix_click": [
                {"ok": True, "alreadyAnswered": True, "rowIndex": 0, "columnIndex": 0,
                 "label": "", "cellCount": 5},
                {"ok": True, "clicked": True, "rowIndex": 1, "columnIndex": 0,
                 "label": "", "cellCount": 5, "answeredNow": True},
            ],
        })
        report = interact.fill_matrix(session, tab)
        assert "already answered: 1" in report
        assert "clicked: 1" in report

    def test_only_unanswered_rows_are_driven_by_default(self):
        backend, session, tab = make({
            "matrix_scan": [_scan_with_unanswered([5, 9]), _scan_with_unanswered([])],
            "matrix_click": [{"ok": True, "clicked": True, "rowIndex": i, "columnIndex": 0,
                              "label": "", "cellCount": 5, "answeredNow": True} for i in (5, 9)],
        })
        interact.fill_matrix(session, tab)
        assert backend.payload_calls("matrix_click") == 2

    def test_explicit_rows_override_the_scan(self):
        backend, session, tab = make({
            "matrix_scan": [SCAN_28, _scan_with_unanswered([])],
            "matrix_click": [{"ok": True, "clicked": True, "rowIndex": i, "columnIndex": 0,
                              "label": "", "cellCount": 5, "answeredNow": True} for i in (0, 2)],
        })
        interact.fill_matrix(session, tab, rows=[0, 2])
        assert backend.payload_calls("matrix_click") == 2

    def test_one_failing_row_does_not_abandon_the_rest(self):
        backend, session, tab = make({
            "matrix_scan": [_scan_with_unanswered([0, 1, 2]), _scan_with_unanswered([1])],
            "matrix_click": [
                {"ok": True, "clicked": True, "rowIndex": 0, "columnIndex": 0,
                 "label": "", "cellCount": 5, "answeredNow": True},
                {"ok": False, "error": "Row 1 has no answerable cells."},
                {"ok": True, "clicked": True, "rowIndex": 2, "columnIndex": 0,
                 "label": "", "cellCount": 5, "answeredNow": True},
            ],
        })
        report = interact.fill_matrix(session, tab)
        assert backend.payload_calls("matrix_click") == 3
        assert "failed: 1" in report and "clicked: 2" in report

    def test_a_fully_answered_matrix_does_nothing(self):
        backend, session, tab = make({"matrix_scan": _scan_with_unanswered([])})
        report = interact.fill_matrix(session, tab)
        assert "already fully answered" in report
        assert backend.payload_calls("matrix_click") == 0

    def test_no_matrix_on_the_page_explains_the_selectors(self):
        _, session, tab = make({"matrix_scan": {"ok": True, "questionCount": 0, "questions": []}})
        with pytest.raises(ElementNotFoundError) as caught:
            interact.fill_matrix(session, tab)
        assert "matrix_question" in caught.value.remedy
        assert "choice_question" in caught.value.remedy

    def test_out_of_range_question_index(self):
        _, session, tab = make({"matrix_scan": SCAN_28})
        with pytest.raises(ElementNotFoundError, match="only 1 matrix"):
            interact.fill_matrix(session, tab, question_index=4)

    def test_max_rows_caps_a_runaway_page(self):
        backend, session, tab = make({
            "matrix_scan": [_scan_with_unanswered(list(range(500))), _scan_with_unanswered([])],
            "matrix_click": [{"ok": True, "clicked": True, "rowIndex": i, "columnIndex": 0,
                              "label": "", "cellCount": 5, "answeredNow": True} for i in range(500)],
        })
        interact.fill_matrix(session, tab, max_rows=10)
        assert backend.payload_calls("matrix_click") == 10


class TestWaitFor:
    def test_returns_as_soon_as_the_condition_holds(self):
        backend, session, tab = make({"wait_for": {"ok": True, "satisfied": True, "detail": "visible"}})
        assert "Condition met" in interact.wait_for(session, tab, selector="#x")
        assert backend.payload_calls("wait_for") == 1

    def test_polls_until_satisfied(self):
        backend, session, tab = make({"wait_for": [
            {"ok": True, "satisfied": False, "detail": "not in the DOM"},
            {"ok": True, "satisfied": False, "detail": "present but not visible"},
            {"ok": True, "satisfied": True, "detail": "visible"},
        ]})
        interact.wait_for(session, tab, selector="#x", poll_ms=50)
        assert backend.payload_calls("wait_for") == 3

    def test_timeout_reports_the_last_thing_it_actually_saw(self):
        """"Timed out" alone gives nothing to act on; the last observation
        distinguishes a wrong selector from a slow page."""
        backend, session, tab = make({"wait_for": [
            {"ok": True, "satisfied": False, "detail": "present but not visible"}
        ] * 50})
        with pytest.raises(TimeoutError_) as caught:
            interact.wait_for(session, tab, selector="#x", timeout_ms=150, poll_ms=50)
        assert "present but not visible" in str(caught.value)
        assert "query_dom" in caught.value.remedy

    def test_needs_a_selector_or_predicate(self):
        _, session, tab = make({})
        with pytest.raises(ValueError, match="selector or predicate"):
            interact.wait_for(session, tab)


class TestSelectOptionViaUi:
    def test_opens_waits_then_clicks(self):
        """A single JS call cannot do this: the menu does not exist yet when the
        click would fire."""
        backend, session, tab = make({
            "locate": [dict(LOCATED), {**LOCATED, "text": "iOS 17"}],
            "wait_for": {"ok": True, "satisfied": True, "detail": "visible"},
        })
        result = interact.select_option_via_ui(
            session, tab, control_selector=".picker", option_text="iOS 17"
        )
        sequence = [call for call, _ in backend.calls if call in ("locate", "wait_for", "click_at")]
        assert sequence == ["locate", "click_at", "wait_for", "locate", "click_at"]
        assert "iOS 17" in result
