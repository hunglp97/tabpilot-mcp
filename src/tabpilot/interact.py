"""Driving pages: clicking, filling, choosing, and waiting."""

from __future__ import annotations

import time
from typing import Any

from .backends.base import Capability, TabInfo
from .errors import ElementNotFoundError, TimeoutError_
from .session import Session


def wait_until_loaded(
    session: Session,
    tab: TabInfo,
    *,
    previous_url: str | None = None,
    timeout_ms: int | None = None,
) -> str:
    """Wait for a navigation to actually complete.

    Waiting on ``document.readyState === 'complete'`` alone does not work, and
    fails in the most misleading way possible. A newly created tab sits at
    ``about:blank``, whose readyState is *already* ``complete`` before the
    requested URL has begun loading — so the wait returns immediately, the next
    tool call runs against a blank page, and the error that surfaces is "no
    element matches your selector". The same race applies to navigating an
    existing tab, where the old document is complete while the new one loads.

    So this waits for the URL to leave where it was, then for the new document
    to finish. A URL that never changes (a reload, or a same-page anchor) is not
    an error: that phase simply expires and the readyState wait still runs.
    """
    total = timeout_ms or session.config.timeout_ms
    from_url = previous_url or "about:blank"

    change_budget = max(int(total * 0.5), 500)
    try:
        wait_for(
            session, tab,
            predicate=f"location.href !== {_js(from_url)}",
            timeout_ms=change_budget,
            poll_ms=50,
        )
    except TimeoutError_:
        pass  # a reload or same-page navigation; the document check still applies

    return wait_for(
        session, tab,
        predicate="document.readyState === 'complete'",
        timeout_ms=total,
    )


def click(
    session: Session,
    tab: TabInfo,
    *,
    selector: str | None = None,
    text: str | None = None,
    nth: int = 0,
    trusted: bool | None = None,
) -> str:
    """Click an element, by CSS selector and/or visible text.

    With the CDP backend this dispatches a real mouse event at the element's
    centre, which is what custom dropdowns and anti-automation checks expect.
    Without it, the full synthetic pointer sequence is dispatched instead.
    """
    if not selector and not text:
        raise ValueError("Pass selector, text, or both.")

    use_trusted = session.backend.supports(Capability.TRUSTED_INPUT) if trusted is None else trusted
    if use_trusted and not session.backend.supports(Capability.TRUSTED_INPUT):
        session.require(Capability.TRUSTED_INPUT)

    options = {"selector": selector, "text": text, "nth": nth}

    if not use_trusted:
        result = session.run_payload(tab.id, "synthetic_click", options)
        return (
            f"Clicked <{result.get('tag')}> \"{result.get('text')}\" "
            f"(synthetic events; {result.get('matched')} matched)"
        )

    located = session.run_payload(tab.id, "locate", options)
    if located.get("disabled"):
        raise ElementNotFoundError(
            f"<{located.get('tag')}> \"{located.get('text')}\" is disabled, so clicking it does nothing.",
            remedy="wait_for it with state='enabled' first.",
        )
    if not located.get("inViewport"):
        raise ElementNotFoundError(
            f"<{located.get('tag')}> could not be scrolled into the viewport, so there is nowhere to click.",
            remedy="It may sit inside a scrollable container or a closed accordion.",
        )

    session.backend.click_at(tab.id, located["x"], located["y"], session.config.timeout_s)
    return (
        f"Clicked <{located.get('tag')}> \"{located.get('text')}\" at "
        f"({located['x']:.0f}, {located['y']:.0f}) with a trusted event "
        f"({located.get('matched')} matched)"
    )


def fill(
    session: Session,
    tab: TabInfo,
    *,
    selector: str,
    value: str,
    clear: bool = True,
    nth: int = 0,
    press_enter: bool = False,
) -> str:
    """Set a field's value, firing both ``input`` and ``change``."""
    result = session.run_payload(
        tab.id, "fill", {"selector": selector, "value": value, "clear": clear, "nth": nth}
    )

    lines = []
    kind = result.get("kind")
    if kind in ("checkbox", "radio"):
        lines.append(f"<{result.get('tag')}> {kind} is now checked={result.get('checked')}")
    else:
        lines.append(f"Filled <{result.get('tag')}> ({kind}) with {result.get('value')!r}")
        if result.get("matchedValue") is False:
            lines.append(
                "**Warning:** the field's value differs from what was sent. The page probably "
                "reformats or masks input (a date or phone mask, or a maxlength)."
            )

    if press_enter:
        if session.backend.supports(Capability.TRUSTED_INPUT):
            session.backend.press_key(tab.id, "Enter", session.config.timeout_s)
            lines.append("Pressed Enter (trusted).")
        else:
            session.eval_raw(
                tab.id,
                f"(()=>{{const e=document.querySelectorAll({_js(selector)})[{nth}];"
                "if(!e)return 'gone';"
                "const k={key:'Enter',code:'Enter',keyCode:13,which:13,bubbles:true,cancelable:true};"
                "e.dispatchEvent(new KeyboardEvent('keydown',k));"
                "e.dispatchEvent(new KeyboardEvent('keypress',k));"
                "e.dispatchEvent(new KeyboardEvent('keyup',k));"
                "if(e.form&&e.form.requestSubmit)e.form.requestSubmit();"
                "return 'sent';})()",
            )
            lines.append("Pressed Enter (synthetic key events).")
    return "\n".join(lines)


def select_option(
    session: Session,
    tab: TabInfo,
    *,
    selector: str,
    values: list[str],
    by: str = "auto",
    nth: int = 0,
) -> str:
    """Choose option(s) in a native ``<select>`` or a Select2 widget."""
    if not values:
        raise ValueError("Pass at least one value.")

    result = session.run_payload(
        tab.id, "select_option", {"selector": selector, "values": values, "by": by, "nth": nth}
    )
    chosen = ", ".join(
        f"{item.get('label') or item.get('value')!r}" for item in result.get("selected", [])
    )
    note = ""
    if result.get("kind") == "select2":
        note = " (Select2 pills refreshed)" if result.get("select2Refreshed") else (
            " (Select2 detected but jQuery is absent, so the visible pills may be stale)"
        )
    return f"Selected {chosen} in a {result.get('kind')}{note}"


def select_option_via_ui(
    session: Session,
    tab: TabInfo,
    *,
    control_selector: str,
    option_text: str,
    option_selector: str | None = None,
    timeout_ms: int | None = None,
) -> str:
    """Choose an option in a React-Select style widget by driving its UI.

    These widgets render no ``<select>``, so there is no value to set: the menu
    must be opened, the list must be given time to mount, and the option must be
    clicked. Doing it in one JavaScript task never works, because the menu does
    not exist yet when the click would be dispatched.
    """
    timeout = timeout_ms or session.config.timeout_ms
    menu_selector = option_selector or (
        '[class*="-option"], [class*="__option"], [role="option"], li[role="presentation"]'
    )

    click(session, tab, selector=control_selector)
    wait_for(session, tab, selector=menu_selector, state="visible", timeout_ms=timeout)
    result = click(session, tab, selector=menu_selector, text=option_text)
    return f"Opened `{control_selector}` and chose {option_text!r}.\n{result}"


def fill_matrix(
    session: Session,
    tab: TabInfo,
    *,
    selector: str | None = None,
    question_index: int = 0,
    column_index: int = 0,
    rows: list[int] | None = None,
    only_unanswered: bool = True,
    delay_ms: int | None = None,
    max_rows: int = 200,
) -> str:
    """Answer a matrix/grid question row by row.

    Rows are clicked one per JavaScript task with a delay between them. Batching
    them is the classic failure: React coalesces the updates, commits only the
    last, and every earlier row stays blank — so validation fails and the page
    looks stuck in a submit loop.
    """
    delay = (session.config.matrix_delay_ms if delay_ms is None else delay_ms) / 1000.0
    scan_options = {"selector": selector} if selector else {}

    before = session.run_payload(tab.id, "matrix_scan", scan_options)
    questions: list[dict[str, Any]] = before.get("questions", [])
    if not questions:
        raise ElementNotFoundError(
            "No matrix/grid question found on this page.",
            remedy=(
                "Pass `selector` for the question container. Matrix questions carry classes like\n"
                "`.matrix_question`, `.display_table` or `[class*=\"matrix\"]` — never `.choice_question`."
            ),
        )
    if question_index >= len(questions):
        raise ElementNotFoundError(
            f"question_index={question_index} but only {len(questions)} matrix question(s) found."
        )

    question = questions[question_index]
    targets = rows if rows is not None else (
        question["unansweredRows"] if only_unanswered else list(range(question["rowCount"]))
    )
    targets = targets[:max_rows]

    if not targets:
        return (
            f"Matrix {question_index} (\"{question.get('title', '')[:60]}\") is already fully answered "
            f"— {question['rowCount']} row(s), nothing to do."
        )

    clicked, skipped, failed = [], [], []
    for row_index in targets:
        try:
            outcome = session.run_payload(
                tab.id,
                "matrix_click",
                {
                    "selector": selector,
                    "questionIndex": question_index,
                    "rowIndex": row_index,
                    "columnIndex": column_index,
                },
            )
        except Exception as exc:  # one bad row must not abandon the rest
            failed.append((row_index, str(exc)))
            continue
        if outcome.get("alreadyAnswered"):
            skipped.append(row_index)
        else:
            clicked.append(row_index)
        time.sleep(delay)

    after = session.run_payload(tab.id, "matrix_scan", scan_options)
    remaining = after.get("questions", [])
    still_unanswered = (
        remaining[question_index]["unansweredRows"] if question_index < len(remaining) else []
    )

    lines = [
        f"Matrix {question_index}: {question['rowCount']} row(s), column {column_index}, "
        f"{int(delay * 1000)}ms between rows",
        f"  clicked: {len(clicked)}" + (f" {clicked}" if len(clicked) <= 30 else ""),
    ]
    if skipped:
        lines.append(f"  already answered: {len(skipped)}")
    if failed:
        lines.append(f"  failed: {len(failed)}")
        for row_index, message in failed[:5]:
            lines.append(f"    row {row_index}: {message}")
    lines.append(
        f"  still unanswered after the pass: {len(still_unanswered)}"
        + (f" {still_unanswered}" if 0 < len(still_unanswered) <= 30 else "")
    )
    if still_unanswered:
        lines.append(
            "  Rows remain blank. Raise `delay_ms` (try 150-250), or the visible control may live "
            "in a nested wrapper — inspect one row with query_dom."
        )
    return "\n".join(lines)


def scan_matrix(session: Session, tab: TabInfo, *, selector: str | None = None) -> str:
    """Report the matrix questions on a page and which rows are unanswered."""
    result = session.run_payload(tab.id, "matrix_scan", {"selector": selector} if selector else {})
    questions = result.get("questions", [])
    if not questions:
        return "No matrix/grid questions found on this page."

    lines = [f"{len(questions)} matrix question(s):"]
    for question in questions:
        lines.append(
            f"\n[{question['index']}] {question.get('title', '')[:100]}\n"
            f"    rows: {question['rowCount']}, unanswered: {question['unansweredCount']}"
            + (f" {question['unansweredRows']}" if 0 < question["unansweredCount"] <= 40 else "")
        )
        if question.get("id"):
            lines.append(f"    id: {question['id']}")
    return "\n".join(lines)


def wait_for(
    session: Session,
    tab: TabInfo,
    *,
    selector: str | None = None,
    state: str = "visible",
    text: str | None = None,
    predicate: str | None = None,
    timeout_ms: int | None = None,
    poll_ms: int = 150,
) -> str:
    """Poll until a condition holds, or fail with what was actually observed."""
    if not selector and not predicate:
        raise ValueError("Pass selector or predicate.")

    timeout = (timeout_ms or session.config.timeout_ms) / 1000.0
    poll = max(poll_ms, 50) / 1000.0
    options = {"selector": selector, "state": state, "text": text, "predicate": predicate}

    deadline = time.monotonic() + timeout
    attempts = 0
    detail = "never evaluated"

    while True:
        attempts += 1
        result = session.run_payload(tab.id, "wait_for", options, timeout_s=min(timeout, 10.0))
        detail = result.get("detail", "")
        if result.get("satisfied"):
            elapsed = timeout - max(deadline - time.monotonic(), 0)
            target = predicate or f"`{selector}` {state}"
            return f"Condition met after {elapsed:.1f}s ({attempts} check(s)): {target} — {detail}"
        if time.monotonic() >= deadline:
            break
        time.sleep(poll)

    target = predicate or f"`{selector}` to be {state}"
    raise TimeoutError_(
        f"Waited {timeout:.1f}s for {target} — last observation: {detail}",
        remedy="Confirm the selector with query_dom, or raise timeout_ms.",
    )


def _js(value: str) -> str:
    """JSON-encode a value for safe embedding in a JS expression."""
    import json

    return json.dumps(value, ensure_ascii=False)
