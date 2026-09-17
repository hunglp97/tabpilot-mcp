"""Payload loading, include splicing, and argument encoding."""

from __future__ import annotations

import json

from tabpilot import payloads

ALL_PAYLOADS = [
    "readable", "query_dom", "locate", "synthetic_click", "fill",
    "select_option", "matrix_scan", "matrix_click", "wait_for",
]


def test_every_payload_loads_and_is_a_function_expression():
    for name in ALL_PAYLOADS:
        source = payloads.load(name).strip()
        assert source.startswith("(function") or source.startswith("/**"), name
        assert source.endswith(")"), name


def test_matrix_includes_are_spliced():
    for name in ("matrix_scan", "matrix_click"):
        source = payloads.load(name)
        assert "/* MATRIX_COMMON */" not in source
        assert "MATRIX_SELECTORS" in source and "function isAnswered" in source


def test_locate_is_spliced_into_synthetic_click():
    source = payloads.load("synthetic_click")
    assert "(LOCATE)" not in source
    assert "devicePixelRatio" in source  # locate's own body arrived


def test_options_are_json_encoded_not_interpolated():
    """A selector containing quotes and backslashes must survive intact."""
    nasty = "input[data-x=\"a'b\\\"c\"]"
    expression = payloads.call("fill", {"selector": nasty, "value": 'say "hi"\nnewline'})
    encoded = expression[expression.rindex("("):]
    assert json.loads(encoded[1:-1])["selector"] == nasty
    assert json.loads(encoded[1:-1])["value"] == 'say "hi"\nnewline'


def test_non_ascii_is_preserved():
    expression = payloads.call("fill", {"selector": "#x", "value": "Khảo sát 日本語"})
    assert "Khảo sát 日本語" in expression


def test_expression_carries_the_payload_name():
    expression = payloads.call("wait_for", {"selector": "#x"})
    assert expression.startswith("/*tabpilot:wait_for*/")
    assert payloads.payload_name(expression) == "wait_for"


def test_payload_name_of_a_hand_written_expression_is_none():
    assert payloads.payload_name("document.title") is None
