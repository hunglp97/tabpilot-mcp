# Design

Why TabPilot is built the way it is. Read [README.md](../README.md) first for
what it does.

## The problem

An agent that needs to work in a browser has two unappealing options.

Launch a fresh browser, and it has none of your sessions — useless for the
dashboard behind SSO or the tracker that needed 2FA, which is most of the work
worth automating.

Attach to your real browser via a read-only tab reader, and you can see the page
but not act on it. You can read the bug list; you cannot file the bug.

TabPilot attaches to the browser you already trust *and* drives it.

## Transport: CDP first, AppleScript as a fallback

Two ways exist to reach a browser someone is already using.

**AppleScript** (`tell application "Google Chrome" to execute ... javascript`)
needs no relaunch and no flags. It is also the whole capability set: run
JavaScript in a tab, and nothing else. It cannot capture a specific tab's pixels,
cannot dispatch events that carry `isTrusted`, cannot await a promise, and does
not exist outside macOS.

**CDP** is a strict superset. `Page.captureScreenshot` captures the tab you name
whether or not it is frontmost. `Input.dispatchMouseEvent` produces events that
React and Vue cannot tell apart from a human. `Runtime.evaluate` awaits promises.
It works on every OS and inside Xvfb, which is what makes a headless server a
viable host for a logged-in browser. The cost is a relaunch with
`--remote-debugging-port` and a profile outside the default one.

So: CDP is tried first everywhere, and on macOS a missing debugging port falls
back to AppleScript rather than failing. The common case (Chrome already open, no
flags) keeps working; the capable case is preferred whenever available.

Each backend declares a capability set, and the tool layer refuses with the
capability named and the fix attached:

```
ERROR [UNSUPPORTED_BY_BACKEND] The applescript backend cannot do 'screenshot'.

How to fix:
Capturing a specific tab's pixels needs the CDP backend.
Relaunch Chrome with a debugging port, then retry:
  tabpilot doctor
```

The alternative — advertising a capability and degrading quietly — is what turned
a known limitation into invisible data loss in the code this replaces: a
screenshot helper built on macOS `screencapture` skipped the capture entirely on
Linux, so runs on a server reported success and produced no evidence at all.

## Layers

```
server.py        MCP tools and resources; turns errors into readable text
  ├── extract.py    read_tab, query_dom
  ├── interact.py   click, fill, select_option, fill_matrix, wait_for
  └── evidence.py   screenshot
        │
     session.py   one backend, lazily connected; unwraps payload envelopes
        │
     tabs.py      loose tab reference -> one concrete tab
        │
   backends/      base (contract) · cdp · applescript · registry (selection)
        │
   backends/_ws.py   RFC 6455 client, stdlib only
        │
   js/*.js        the payloads that actually run in the page
```

Two rules keep this honest.

**No layer reaches past the one below it.** `interact` knows about capabilities,
never about sockets. Swapping in a third backend — a WebDriver BiDi one, say —
means implementing `Backend` and nothing else.

**Payloads are files, not strings.** The JavaScript lives in
[`src/tabpilot/js/`](../src/tabpilot/js/) so it can be linted, diffed and
syntax-checked on its own. `payloads.py` loads a file, resolves its includes, and
appends JSON-encoded arguments:

```
/*tabpilot:fill*/(function (opts) { ... })({"selector":"#title","value":"..."})
```

Arguments are never interpolated into source. That is what makes a selector
containing quotes, backslashes and newlines a non-event, on a path where the
payload crosses the shell, AppleScript and JavaScript, each with its own escaping
rules. The leading marker names the payload in CDP traces and page errors, so a
failure says *which* payload threw.

## Zero dependencies beyond the MCP SDK

The CDP client is a hand-written RFC 6455 implementation over `socket`. That is
about 200 lines that a library would provide, in exchange for TabPilot
installing on a bare Ubuntu host where `pip install` may not be available and a
transitive dependency tree is a liability.

The layer with no safety net gets the most direct test:
[`tests/test_ws.py`](../tests/test_ws.py) runs a real loopback WebSocket server
and exercises masking, all three payload-length encodings, fragmentation, ping,
close and timeout. A masking or length-field mistake shows up as a hang or a
corrupted message, not as an exception, so mocking it would prove nothing.

## Addressing tabs

Tab ids are not stable. CDP mints a new target id on many navigations; the
AppleScript handle is a *position* that shifts whenever a tab opens or closes.
An agent that caches an id across a workflow eventually addresses a different
page — and the action still succeeds, somewhere you did not intend.

So `url_pattern` is a first-class way to name a tab, and the one the tool
descriptions push:

```python
read_tab(url_pattern=r"tester\.test\.io/tests/\w+/bugs")
```

Resolution order: explicit `tab_id`, then `url_pattern`, then the focused tab,
then — if exactly one tab is open — that tab. When several tabs match and none
has focus, `tabs.resolve` **refuses** and lists the candidates. Picking one
silently would act on the wrong page a good fraction of the time, and the caller
would have no way to know.

## Reading under a budget

An agent's default move is `eval_js("document.body.innerText")`. It costs
thousands of tokens and answers "is the submit button disabled" no better than
ten lines would.

Three tools, cheapest first:

- **`query_dom(selector=...)`** returns structured facts about specific
  elements: tag, text, geometry, visibility, and the attributes that carry state
  (`disabled`, `checked`, `selected`). This is the right tool for most questions
  and the one agents reach for least, so the server instructions name it.
- **`read_tab(selector=...)`** scopes the read to one subtree.
- **`read_tab()`** takes the whole page: a link-density heuristic picks the main
  content, boilerplate is stripped, and the result is converted to markdown.

All of them cap the result and report `truncated` with the full length. Silent
truncation is worse than either extreme — the model reasons from half a page
without knowing it. Saying "cut at 20000 of 48000 chars, narrow it with
`selector`" turns that into one more call.

Extraction runs *in the page*, so only the finished, budgeted string crosses the
wire. Extracting locally would mean shipping the whole DOM first, which defeats
the purpose.

## Three failure modes that justify their own tools

These cost more debugging time than everything else combined, and none of them
announces itself.

### React ignores a value you assigned

React caches the last value it wrote on a DOM node. Assigning `.value` directly
leaves that cache unchanged, so React sees no difference and drops the event. The
field *looks* filled while component state stays empty; submit fails validation
for no visible reason.

`fill` calls the prototype's native setter, which defeats the cache, and fires
both `input` and `change` — some libraries listen for one, some for the other.

### A matrix question submits half-empty

Click twenty-eight rows inside one JavaScript task and React batches the state
updates, committing only the last. Twenty-seven rows stay blank, submit fails,
and the page appears stuck in a loop — the classic symptom being an agent that
re-answers the same question forever.

`fill_matrix` drives **one row per call** with a delay between them, so each
update commits on its own, then re-scans to report what is still blank rather
than trusting its own clicks. It also does not assume a native radio: survey
platforms ship styled `<div>`s whose "on" state lives in a class name or an
`aria-checked` attribute, so `isAnswered` tries several signals in order of
trustworthiness. Checking only `input.checked` reports every row as unanswered on
those pages, which is what starts the loop.

### React-Select has no `<select>` to set

There is no value to assign. The menu must be opened, given time to mount, and
the option clicked — which cannot happen in one JavaScript call, because the menu
does not exist yet when the click would fire.

`select_option` detects the case and says so; `select_option_ui` drives the
sequence through the tool layer, where waiting is possible.

## Two races found by driving a real browser

Both were caught only by [`tests/test_live_cdp.py`](../tests/test_live_cdp.py),
and both produced *misleading* failures — which is what makes them worth
recording.

**`readyState` lies on a fresh tab.** A newly created tab sits at `about:blank`,
whose `readyState` is already `complete` before the requested URL begins loading.
Waiting on `readyState` alone returns instantly; the next tool call runs against
a blank page; and the error that surfaces is "no element matches your selector" —
pointing at the caller's selector instead of at the navigation.
`wait_until_loaded` waits for the URL to leave where it was, *then* for the
document to finish. A URL that never changes (a reload, a same-page anchor) is
not an error: that phase simply expires and the document check still runs.

**`close_tab` returned before the tab closed.** `/json/close` only *requests* the
close; the target lingers in `/json/list` afterwards. A caller that closed a tab
and listed tabs saw a ghost, and a `url_pattern` could resolve onto the dying
tab. `close_tab` now polls until the target is gone, and if it never goes,
says so — a page showing a `beforeunload` dialog is a real outcome, not a
successful close.

## Errors are returned, not raised

A raised exception reaches the model as a stack trace with the remedy stripped
out. Every tool catches `TabPilotError` and returns its text:

```
ERROR [BRIDGE_OFF] No Chrome answering on http://127.0.0.1:9222 — Connection refused

How to fix:
Quit Chrome completely (Cmd+Q), then:
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
    --remote-debugging-port=9222 \
    --user-data-dir="$HOME/tabpilot-chrome"
```

The `[CODE]` prefix keeps the failure unmistakable; the remedy is the part that
lets an agent recover without the user stepping in. `cdp_launch_hint` builds that
command for the OS it is actually running on, including the two things that trip
everyone up — `--user-data-dir` outside the default profile, and quitting Chrome
fully first.

`BRIDGE_OFF` additionally resets the session, so a Chrome restart, a slept
laptop, or a dropped SSH tunnel recovers on the next call instead of wedging
until the client reconnects.

## Testing

| Layer | How | Why that way |
|:--|:--|:--|
| Config, tabs, payloads, escaping | pure unit | no I/O to speak of |
| extract, interact, evidence | `FakeBackend` | asserts *call sequences* — that `fill_matrix` makes one click call per row, that `select_option_ui` clicks, waits, then clicks |
| WebSocket client | real loopback server | framing bugs hang rather than raise; a mock would prove nothing |
| systemd units | rendered and inspected | catches an unset variable a unit references, which would expand to an empty string and silently launch Chrome with no profile |
| Everything, end to end | real Chrome, real CDP, `-m live` | the only way to know the JavaScript payloads work; it is what found both races above |

The live suite serves its own fixture page, built to contain the traps:
boilerplate that readable mode must drop, a matrix whose radios are styled `<i>`
elements with class-based state, a disabled button, a late-appearing element, and
a handler recording `event.isTrusted` so the tests can prove CDP clicks are
trusted and DOM-dispatched ones are not. It skips rather than fails when no CDP
port answers.

## Deliberate omissions

- **No browser launching**, outside the Ubuntu stack. The point is attaching to
  the browser you already trust. Managing its lifecycle invites the fresh-profile
  problem back in.
- **No anti-detection.** Trusted input is a consequence of using CDP correctly,
  not a feature aimed at evading anything.
- **No remote CDP binding.** Reaching Chrome from elsewhere goes through SSH.
  The DevTools protocol has no authentication; exposing it hands over every
  cookie in the profile.
- **No console or network capture** yet. Both are natural next steps
  (`Log.entryAdded`, `Network.responseReceived`) and both need a persistent event
  subscription, which the current one-command-at-a-time socket model does not
  have. Adding it is a real change, not a tool.
