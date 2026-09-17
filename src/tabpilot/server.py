"""The MCP server: 17 tools and 2 resources over a live browser."""

from __future__ import annotations

import functools
from typing import Any, Callable

from . import __version__
from ._sdk import SDK_MAJOR, Image, Server
from . import evidence, extract, interact, tabs as tabs_module
from .config import Config
from .errors import BridgeOffError, TabPilotError
from .session import Session

INSTRUCTIONS = """\
TabPilot drives a real, already-logged-in Chrome. Nothing is sandboxed: every
action lands in the user's actual browser session.

Addressing a tab: prefer `url_pattern`, a regex matched against tab URLs
(`tester\\.test\\.io/tests`). Tab ids change on navigation, so a cached id
eventually points at the wrong page. With neither argument, the focused tab is
used.

Reading cheaply: `read_tab` with a `selector` beats reading the whole page, and
`query_dom` beats both when the question is about specific elements ("is the
submit button disabled", "what options does this select have"). Reaching for
`eval_js` with `document.body.innerText` will blow your context budget for an
answer the other two tools give in a few lines.

Filling forms: `fill` fires both `input` and `change` through the native setter,
so React sees it. For matrix/grid questions use `fill_matrix`, never a loop of
clicks — batched clicks make React commit only the last row.
"""


def create_server(config: Config | None = None) -> Server:
    config = config or Config.from_env()
    session = Session(config)
    # `version` only exists on SDK 2; on 1.x the client reads it from elsewhere.
    kwargs = {"name": "tabpilot", "instructions": INSTRUCTIONS}
    if SDK_MAJOR >= 2:
        kwargs["version"] = __version__
    mcp = Server(**kwargs)

    def tool(fn: Callable[..., Any]) -> Callable[..., Any]:
        """Register ``fn`` as a tool, turning known failures into readable text.

        A raised exception reaches the model as a stack trace with the remedy
        stripped out. Returning the message keeps the "how to fix" text — which
        is the part that lets an agent recover without the user intervening.
        """

        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return fn(*args, **kwargs)
            except BridgeOffError as exc:
                session.reset()  # reconnect on the next call
                return "ERROR " + exc.to_text()
            except TabPilotError as exc:
                return "ERROR " + exc.to_text()
            except ValueError as exc:
                return f"ERROR [BAD_ARGUMENT] {exc}"

        return mcp.tool()(wrapper)

    # --- status --------------------------------------------------------------

    @tool
    def browser_status() -> str:
        """Report which backend is connected, what it can do, and how many tabs are open."""
        backend = session.backend
        open_tabs = backend.list_tabs()
        lines = [
            f"backend: {backend.name}",
            f"capabilities: {', '.join(sorted(backend.capabilities))}",
            f"open tabs: {len(open_tabs)}",
        ]
        if backend.name == "cdp":
            lines.append(f"endpoint: {config.cdp_base_url}")
            version = getattr(backend, "browser_version", lambda: {})()
            if version.get("Browser"):
                lines.append(f"browser: {version['Browser']}")
        if backend.name == "applescript":
            lines.append(
                "note: no screenshots and no trusted input on this backend. "
                "Run `tabpilot doctor` to see how to switch to CDP."
            )
        lines.append(f"screenshots: {config.screenshot_dir}")
        return "\n".join(lines)

    # --- reading -------------------------------------------------------------

    @tool
    def list_tabs(url_pattern: str | None = None) -> str:
        """List open tabs with their ids, titles and URLs.

        Args:
            url_pattern: Optional regex; only tabs whose URL matches are listed.
        """
        return tabs_module.render_list(session.list_tabs(), url_pattern)

    @tool
    def read_tab(
        tab_id: str | None = None,
        url_pattern: str | None = None,
        mode: str = "readable",
        selector: str | None = None,
        max_chars: int | None = None,
        include_links: bool = True,
    ) -> str:
        """Read a tab's content, converted to markdown and capped in size.

        Args:
            tab_id: Exact tab id from list_tabs. Prefer url_pattern.
            url_pattern: Regex matched against tab URLs.
            mode: 'readable' (markdown, boilerplate stripped), 'text', or 'html'.
            selector: Read only this element's subtree. Use it — it is the
                cheapest way to keep a read small and on-topic.
            max_chars: Character budget. Defaults to the server's --max-chars.
            include_links: Keep links as markdown in 'readable' mode.
        """
        tab = session.resolve(tab_id, url_pattern)
        return extract.read_tab(
            session, tab, mode=mode, selector=selector,
            max_chars=max_chars, include_links=include_links,
        )

    @tool
    def query_dom(
        selector: str,
        tab_id: str | None = None,
        url_pattern: str | None = None,
        limit: int = 30,
        attrs: list[str] | None = None,
        text_max: int = 200,
        visible_only: bool = False,
    ) -> str:
        """Inspect specific elements: tag, text, attributes, size, visibility.

        Use this instead of reading the page when the question is about
        particular elements — form fields, buttons, error messages, option lists.

        Args:
            selector: CSS selector.
            tab_id: Exact tab id. Prefer url_pattern.
            url_pattern: Regex matched against tab URLs.
            limit: Maximum elements to return.
            attrs: Only report these attributes. Defaults to a useful set
                (id, name, type, href, value, placeholder, role, aria-label,
                data-testid, class) plus disabled/checked/selected state.
            text_max: Truncate each element's text at this many characters.
            visible_only: Skip elements that are not rendered.
        """
        tab = session.resolve(tab_id, url_pattern)
        return extract.query_dom(
            session, tab, selector=selector, limit=limit, attrs=attrs,
            text_max=text_max, visible_only=visible_only,
        )

    # --- navigation ----------------------------------------------------------

    @tool
    def open_tab(url: str, activate: bool = True, wait_for_load: bool = True) -> str:
        """Open a URL in a new tab and return its id.

        Args:
            url: Absolute URL to open.
            activate: Bring the new tab to the front.
            wait_for_load: Wait until the document finishes loading.
        """
        tab = session.backend.open_tab(url, activate=activate)
        note = ""
        if wait_for_load:
            try:
                interact.wait_until_loaded(session, tab, previous_url="about:blank")
            except TabPilotError:
                note = "\n(The page had not finished loading when the wait expired.)"
        return f"Opened `{tab.id}` -> {url}{note}"

    @tool
    def close_tab(tab_id: str | None = None, url_pattern: str | None = None) -> str:
        """Close a tab.

        Args:
            tab_id: Exact tab id.
            url_pattern: Regex matched against tab URLs. Must identify one tab.
        """
        tab = session.resolve(tab_id, url_pattern)
        session.backend.close_tab(tab.id)
        return f"Closed `{tab.id}` ({tab.title or tab.url})"

    @tool
    def navigate(
        url: str,
        tab_id: str | None = None,
        url_pattern: str | None = None,
        wait_for_load: bool = True,
    ) -> str:
        """Point an existing tab at a new URL.

        Args:
            url: Absolute URL to load.
            tab_id: Exact tab id.
            url_pattern: Regex matched against the tab's *current* URL.
            wait_for_load: Wait until the document finishes loading.
        """
        tab = session.resolve(tab_id, url_pattern)
        previous_url = tab.url
        session.backend.navigate(tab.id, url)
        note = ""
        if wait_for_load:
            try:
                interact.wait_until_loaded(session, tab, previous_url=previous_url)
            except TabPilotError:
                note = "\n(Still loading, or the site redirected elsewhere.)"
        return f"`{tab.id}` navigated to {url}{note}"

    @tool
    def activate_tab(tab_id: str | None = None, url_pattern: str | None = None) -> str:
        """Bring a target browser tab to the front and focus its window.

        Side effects: Changes system window focus and switches the user's active
        tab viewport. Does not reload the page or alter DOM state.

        Usage guidelines:
        - When to use: Use when a human user needs to observe the active page,
          or before capturing desktop-wide OS screenshots and video screencasts.
        - When NOT to use: Do NOT call this before reading or interacting with tabs.
          TabPilot tools (`read_tab`, `query_dom`, `click`, `fill`, `eval_js`,
          `screenshot`) work off-screen in background tabs without stealing focus.

        Args:
            tab_id: Exact tab identifier (e.g. from `list_tabs`). If omitted,
                uses the frontmost tab or matches by `url_pattern`.
            url_pattern: Optional regex pattern matched against tab URLs
                (e.g. 'github\\.com').

        Returns:
            Confirmation message containing the activated tab ID and title/URL.
            Returns an error message if no matching tab is found.
        """
        tab = session.resolve(tab_id, url_pattern)
        session.backend.activate_tab(tab.id)
        return f"Activated `{tab.id}` ({tab.title or tab.url})"

    # --- interaction ---------------------------------------------------------

    @tool
    def eval_js(
        expression: str,
        tab_id: str | None = None,
        url_pattern: str | None = None,
        timeout_ms: int | None = None,
    ) -> str:
        """Run a JavaScript expression in a tab and return its value as JSON.

        The expression's value is returned, so write `document.title`, not
        `return document.title`. Promises are awaited on the CDP backend.

        Do not use this to dump `document.body.innerText` — read_tab and
        query_dom answer those questions for a fraction of the tokens.

        Args:
            expression: A JavaScript expression.
            tab_id: Exact tab id.
            url_pattern: Regex matched against tab URLs.
            timeout_ms: Per-call timeout.
        """
        tab = session.resolve(tab_id, url_pattern)
        value = session.eval_raw(tab.id, expression, (timeout_ms / 1000.0) if timeout_ms else None)
        return _json(value, pretty=True)

    @tool
    def click(
        selector: str | None = None,
        text: str | None = None,
        tab_id: str | None = None,
        url_pattern: str | None = None,
        nth: int = 0,
    ) -> str:
        """Click an element, scrolling it into view first.

        On the CDP backend this is a real mouse event at the element's centre, so
        widgets that only react to genuine mousedown behave correctly.

        Args:
            selector: CSS selector.
            text: Visible text to match. Combined with selector it narrows within
                those matches; alone it searches clickable elements. Exact matches
                win over partial ones, and the innermost match wins.
            tab_id: Exact tab id.
            url_pattern: Regex matched against tab URLs.
            nth: Which match to click, 0-based.
        """
        tab = session.resolve(tab_id, url_pattern)
        return interact.click(session, tab, selector=selector, text=text, nth=nth)

    @tool
    def fill(
        selector: str,
        value: str,
        tab_id: str | None = None,
        url_pattern: str | None = None,
        clear: bool = True,
        nth: int = 0,
        press_enter: bool = False,
    ) -> str:
        """Set a form field's value so React, Vue and jQuery all notice.

        Works on text inputs, textareas, contenteditable, checkboxes and radios
        (pass 'true'/'false' for those).

        Args:
            selector: CSS selector for the field.
            value: Value to set.
            tab_id: Exact tab id.
            url_pattern: Regex matched against tab URLs.
            clear: Empty the field first.
            nth: Which match to fill, 0-based.
            press_enter: Press Enter afterwards, for search boxes.
        """
        tab = session.resolve(tab_id, url_pattern)
        return interact.fill(
            session, tab, selector=selector, value=value,
            clear=clear, nth=nth, press_enter=press_enter,
        )

    @tool
    def select_option(
        selector: str,
        values: list[str],
        tab_id: str | None = None,
        url_pattern: str | None = None,
        by: str = "auto",
        nth: int = 0,
    ) -> str:
        """Choose option(s) in a native <select> or a Select2 widget.

        Pass several values for a multi-select. If the element turns out to be a
        React-Select style widget with no underlying <select>, this says so and
        you should use select_option_ui instead.

        Args:
            selector: CSS selector for the <select>.
            values: Option values or labels to select.
            tab_id: Exact tab id.
            url_pattern: Regex matched against tab URLs.
            by: Match options by 'value', 'label', or 'auto' (either).
            nth: Which match to use, 0-based.
        """
        tab = session.resolve(tab_id, url_pattern)
        return interact.select_option(session, tab, selector=selector, values=values, by=by, nth=nth)

    @tool
    def select_option_ui(
        control_selector: str,
        option_text: str,
        tab_id: str | None = None,
        url_pattern: str | None = None,
        option_selector: str | None = None,
        timeout_ms: int | None = None,
    ) -> str:
        """Choose an option in a React-Select / Headless UI dropdown by driving its UI.

        These have no <select> to set, so the menu is opened, awaited, and the
        option clicked — which cannot be done in a single JavaScript call because
        the menu does not exist yet when the click would fire.

        Args:
            control_selector: CSS selector for the control that opens the menu.
            option_text: Visible text of the option to choose.
            tab_id: Exact tab id.
            url_pattern: Regex matched against tab URLs.
            option_selector: Override the option selector if the defaults miss.
            timeout_ms: How long to wait for the menu to appear.
        """
        tab = session.resolve(tab_id, url_pattern)
        return interact.select_option_via_ui(
            session, tab, control_selector=control_selector, option_text=option_text,
            option_selector=option_selector, timeout_ms=timeout_ms,
        )

    @tool
    def scan_matrix(
        tab_id: str | None = None,
        url_pattern: str | None = None,
        selector: str | None = None,
    ) -> str:
        """List the matrix/grid questions on a page and which rows are unanswered.

        Call this before fill_matrix to see the shape of the question and to
        confirm the row indices.

        Args:
            tab_id: Exact tab id.
            url_pattern: Regex matched against tab URLs.
            selector: Override the matrix container selector.
        """
        tab = session.resolve(tab_id, url_pattern)
        return interact.scan_matrix(session, tab, selector=selector)

    @tool
    def fill_matrix(
        tab_id: str | None = None,
        url_pattern: str | None = None,
        selector: str | None = None,
        question_index: int = 0,
        column_index: int = 0,
        rows: list[int] | None = None,
        only_unanswered: bool = True,
        delay_ms: int | None = None,
    ) -> str:
        """Answer a matrix/grid question one row at a time, then verify the result.

        Rows are clicked in separate JavaScript tasks with a delay between them.
        Clicking them in a loop instead makes React batch the updates and commit
        only the last row, leaving the rest blank and the page stuck failing
        validation.

        Args:
            tab_id: Exact tab id.
            url_pattern: Regex matched against tab URLs.
            selector: Override the matrix container selector.
            question_index: Which matrix question, as numbered by scan_matrix.
            column_index: Which answer column to pick. Negative counts from the
                right, so -1 is the last column.
            rows: Specific row indices. Defaults to whichever rows need answering.
            only_unanswered: Skip rows that already have an answer.
            delay_ms: Delay between rows. Raise it to 150-250 if rows stay blank.
        """
        tab = session.resolve(tab_id, url_pattern)
        return interact.fill_matrix(
            session, tab, selector=selector, question_index=question_index,
            column_index=column_index, rows=rows, only_unanswered=only_unanswered,
            delay_ms=delay_ms,
        )

    @tool
    def wait_for(
        selector: str | None = None,
        state: str = "visible",
        text: str | None = None,
        predicate: str | None = None,
        tab_id: str | None = None,
        url_pattern: str | None = None,
        timeout_ms: int | None = None,
        poll_ms: int = 150,
    ) -> str:
        """Poll until a condition holds, then return. Fails with what it last saw.

        Args:
            selector: CSS selector to watch.
            state: 'visible', 'hidden', 'present', 'absent', 'enabled', or
                'text' (with the text argument).
            text: Substring to look for when state is 'text'.
            predicate: A JavaScript expression to poll instead of a selector,
                e.g. "document.readyState === 'complete'".
            tab_id: Exact tab id.
            url_pattern: Regex matched against tab URLs.
            timeout_ms: How long to keep polling.
            poll_ms: Interval between checks, minimum 50.
        """
        tab = session.resolve(tab_id, url_pattern)
        return interact.wait_for(
            session, tab, selector=selector, state=state, text=text,
            predicate=predicate, timeout_ms=timeout_ms, poll_ms=poll_ms,
        )

    # --- evidence ------------------------------------------------------------

    @tool
    def screenshot(
        tab_id: str | None = None,
        url_pattern: str | None = None,
        full_page: bool = False,
        selector: str | None = None,
        image_format: str = "png",
        quality: int = 80,
        label: str | None = None,
        return_image: bool | None = None,
    ) -> Any:
        """Capture a tab and save it to disk.

        Captures the tab that was asked for even if it is not frontmost, and
        works headless — so this is also how evidence gets captured on a server.
        Needs the CDP backend.

        Args:
            tab_id: Exact tab id.
            url_pattern: Regex matched against tab URLs.
            full_page: Capture the whole scrollable page, not just the viewport.
            selector: Capture only this element.
            image_format: 'png' or 'jpeg'.
            quality: JPEG quality, 1-100. Ignored for PNG.
            label: Included in the filename, to make the file findable later.
            return_image: Inline the image in the result. Defaults on when the
                client is on another machine (TABPILOT_REMOTE / --return-images),
                since a saved path means nothing there.
        """
        tab = session.resolve(tab_id, url_pattern)
        summary, _path, data = evidence.screenshot(
            session, tab, full_page=full_page, selector=selector,
            image_format=image_format, quality=quality, label=label,
        )
        if config.wants_inline_image(return_image) and Image is not None:
            return [summary, Image(data=data, format=image_format)]
        return summary

    # --- resources -----------------------------------------------------------

    @mcp.resource("tab://active", name="Active tab", mime_type="text/markdown")
    def active_tab_resource() -> str:
        """Markdown content of whichever tab currently has focus."""
        try:
            tab = session.resolve()
            return extract.read_tab(session, tab)
        except TabPilotError as exc:
            return exc.to_text()

    @mcp.resource("tab://{tab_id}", name="Tab content", mime_type="text/markdown")
    def tab_resource(tab_id: str) -> str:
        """Markdown content of a specific tab."""
        try:
            tab = session.resolve(tab_id=tab_id)
            return extract.read_tab(session, tab)
        except TabPilotError as exc:
            return exc.to_text()

    mcp._tabpilot_session = session  # type: ignore[attr-defined]  # for tests and teardown
    return mcp


def _json(value: Any, pretty: bool = False) -> str:
    import json

    if pretty:
        return json.dumps(value, ensure_ascii=False, indent=2, default=str)
    return json.dumps(value, ensure_ascii=False, default=str)
