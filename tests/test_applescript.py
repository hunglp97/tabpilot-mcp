"""AppleScript escaping and the JSON envelope.

Escaping is the single largest source of breakage on this path: a payload crosses
the shell, then AppleScript, then JavaScript, each with its own rules.
"""

from __future__ import annotations

import json

import pytest

from tabpilot.backends.applescript import (
    AppleScriptBackend,
    _wrap_for_json,
    escape_applescript_string,
)
from tabpilot.errors import BridgeOffError, JSError, NoTabError

FIELD = ""
RECORD = ""


class TestEscaping:
    @pytest.mark.parametrize("raw,expected", [
        ('say "hi"', 'say \\"hi\\"'),
        ("back\\slash", "back\\\\slash"),
        ("line1\nline2", "line1\\nline2"),
        ("carriage\r", "carriage\\r"),
        ("tab\there", "tab\\there"),
        ("windows\r\nnewline", "windows\\nnewline"),
    ])
    def test_each_troublesome_character(self, raw, expected):
        assert escape_applescript_string(raw) == expected

    def test_backslashes_are_escaped_before_quotes(self):
        r"""Order matters: escaping quotes first would then double-escape the
        backslash that was just inserted, corrupting the payload."""
        assert escape_applescript_string('\\"') == '\\\\\\"'

    def test_non_ascii_passes_through_untouched(self):
        assert escape_applescript_string("Khảo sát 日本語") == "Khảo sát 日本語"

    def test_a_realistic_selector_survives(self):
        selector = "document.querySelector(\"input[data-x='a\\\\b']\")"
        escaped = escape_applescript_string(selector)
        assert '\\"' in escaped and "\n" not in escaped


class TestJsonEnvelope:
    """``execute javascript`` coerces return values into AppleScript types, which
    flattens objects. Serialising on the page and parsing here keeps them intact
    and carries page exceptions across as data."""

    def test_a_value_is_wrapped_as_ok(self):
        wrapped = _wrap_for_json("1 + 1")
        assert "JSON.stringify" in wrapped and "ok:true" in wrapped

    def test_exceptions_are_caught_into_the_envelope(self):
        assert "catch(e)" in _wrap_for_json("boom()")

    def test_promises_are_detected_rather_than_silently_dropped(self):
        assert "pending:true" in _wrap_for_json("fetch('/x')")


class FakeRun(AppleScriptBackend):
    """AppleScriptBackend with osascript replaced by a canned response."""

    def __init__(self, output: str = "", error: str | None = None) -> None:
        super().__init__()
        self._output = output
        self._error = error
        self.scripts: list[str] = []

    def _run(self, script: str, timeout_s: float | None = None) -> str:
        self.scripts.append(script)
        if self._error is not None:
            raise self._classify(self._error)
        return self._output


class TestTabHandles:
    def test_a_malformed_handle_is_rejected_with_the_expected_shape(self):
        backend = FakeRun()
        with pytest.raises(NoTabError, match=r"w<window>:t<tab>"):
            backend.close_tab("abc123")

    def test_a_cdp_style_id_is_rejected_rather_than_misapplied(self):
        """Handles are backend-specific. Accepting a CDP id here would address
        some arbitrary window and tab position."""
        backend = FakeRun()
        with pytest.raises(NoTabError):
            backend.navigate("4A7B9C2D1E", "https://example.com")

    def test_list_tabs_parses_the_delimited_records(self):
        backend = FakeRun(
            f"w1:t1{FIELD}1{FIELD}First{FIELD}https://a.test/x{RECORD}"
            f"w1:t2{FIELD}0{FIELD}Second{FIELD}https://b.test/y{RECORD}"
        )
        tabs = backend.list_tabs()
        assert [t.id for t in tabs] == ["w1:t1", "w1:t2"]
        assert tabs[0].active is True and tabs[1].active is False
        assert tabs[1].url == "https://b.test/y"

    def test_a_url_containing_the_field_separator_is_rejoined(self):
        backend = FakeRun(f"w1:t1{FIELD}1{FIELD}T{FIELD}https://a.test/?a=1{FIELD}b=2{RECORD}")
        assert backend.list_tabs()[0].url == f"https://a.test/?a=1{FIELD}b=2"

    def test_evaluation_unwraps_the_envelope(self):
        backend = FakeRun(json.dumps({"ok": True, "value": {"n": 2}}))
        assert backend.eval_js("w1:t1", "({n: 1 + 1})", 5) == {"n": 2}

    def test_a_page_exception_becomes_a_js_error(self):
        backend = FakeRun(json.dumps({"ok": False, "error": "x is not defined"}))
        with pytest.raises(JSError, match="x is not defined"):
            backend.eval_js("w1:t1", "x", 5)

    def test_a_promise_says_to_use_cdp(self):
        backend = FakeRun(json.dumps({"ok": True, "pending": True}))
        with pytest.raises(JSError) as caught:
            backend.eval_js("w1:t1", "fetch('/x')", 5)
        assert "--backend cdp" in caught.value.remedy


class TestErrorClassification:
    def test_denied_apple_events_points_at_system_settings(self):
        backend = FakeRun(error="execution error: Not authorized to send Apple events (-1743)")
        with pytest.raises(BridgeOffError) as caught:
            backend.list_tabs()
        assert "Automation" in caught.value.remedy

    def test_disabled_javascript_points_at_the_develop_menu(self):
        backend = FakeRun(error="Chrome got an error: cannot execute javascript in this tab")
        with pytest.raises(BridgeOffError) as caught:
            backend.list_tabs()
        assert "Allow JavaScript from Apple Events" in caught.value.remedy

    def test_a_closed_browser_is_named_as_such(self):
        backend = FakeRun(error="Application isn't running (-600)")
        with pytest.raises(BridgeOffError, match="not running"):
            backend.list_tabs()


def test_capabilities_exclude_what_applescript_cannot_do():
    """Advertising screenshots here would turn a known limitation into a runtime
    surprise on a path that used to skip capture silently."""
    backend = AppleScriptBackend()
    assert backend.supports("eval") and backend.supports("navigate")
    assert not backend.supports("screenshot")
    assert not backend.supports("trusted_input")
