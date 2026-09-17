# TabPilot

**An MCP server that reads *and drives* the Chrome you are already logged into — on macOS, Windows, and headless Ubuntu.**

Most browser MCP servers launch a fresh, empty browser. That is fine for scraping public pages and useless for the work that actually needs automating: the dashboard behind SSO, the bug tracker with your session cookie, the survey platform that took four minutes of 2FA to get into. TabPilot attaches to a *real* browser with real sessions — and unlike read-only tab readers, it can fill the form and click the button too.

```
list_tabs · read_tab · query_dom            see what is there, cheaply
open_tab · close_tab · navigate · activate  move around
click · fill · select_option · wait_for     drive the page
fill_matrix · select_option_ui              drive the pages that fight back
screenshot                                  prove it happened
```

## Why another one

| | Read-only tab readers | Fresh-browser drivers | **TabPilot** |
|:--|:--|:--|:--|
| Your logged-in sessions | ✅ | ❌ new profile | ✅ |
| Fill forms, click buttons | ❌ | ✅ | ✅ |
| Linux / Windows | ❌ macOS only | ✅ | ✅ |
| Headless server, 24/7 | ❌ | partly | ✅ managed units |
| Screenshot a background tab | ❌ | ✅ | ✅ |
| Token-aware reading | partly | ❌ | ✅ `query_dom`, `selector`, budgets |

## Install

```bash
uvx --from git+https://github.com/lephuochung/tabpilot-mcp tabpilot doctor
```

`doctor` tells you exactly what is missing and the command to fix it. Then register the server:

```json
{
  "mcpServers": {
    "tabpilot": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/lephuochung/tabpilot-mcp", "tabpilot-mcp"]
    }
  }
}
```

TabPilot needs a browser it can talk to. Quit Chrome completely, then:

```bash
# macOS
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
  --remote-debugging-port=9222 --user-data-dir="$HOME/tabpilot-chrome"

# Linux
google-chrome --remote-debugging-port=9222 --user-data-dir="$HOME/tabpilot-chrome"
```

```bat
:: Windows
taskkill /F /IM chrome.exe
"C:\Program Files\Google\Chrome\Application\chrome.exe" ^
  --remote-debugging-port=9222 --user-data-dir="%USERPROFILE%\tabpilot-chrome"
```

Two things trip everyone up here, so they are worth stating plainly:

- **`--user-data-dir` must point outside your default profile.** Since Chrome 136 the debugging flag is *silently ignored* on the default profile. This is a deliberate security boundary, not a bug.
- **Chrome must be fully quit first.** A running Chrome swallows the new command as "open a tab" and drops every flag.

That separate profile starts empty, so sign in to your sites once inside it. Keep it at a stable path — not under `/tmp` or `%TEMP%`, which get cleaned and take your sessions with them.

**On macOS you can skip all of that.** With no debugging port open, TabPilot falls back to AppleScript and drives the Chrome you already have running. Enable *View → Developer → Allow JavaScript from Apple Events*. The trade: no screenshots and no trusted input events. `doctor` says so when it happens.

## Headless Ubuntu, 24/7

The case TabPilot is really built for: a browser on a server, logged in, always up, driven from your laptop with no port exposed to the network.

```bash
git clone https://github.com/lephuochung/tabpilot-mcp && cd tabpilot-mcp
bash deploy/ubuntu/install.sh
```

That installs Xvfb, Openbox, x11vnc, Chrome, **and fonts**, then four systemd user units with `Restart=always` and lingering enabled — so the stack survives crashes, logout, and reboot.

```bash
tabpilot status          # what is running
tabpilot logs chrome -f  # follow Chrome's log
tabpilot up --restart    # after editing ~/.config/tabpilot/stack.env
tabpilot doctor          # diagnose anything that broke
```

Sharing the host with something that must not be starved? Cap Chrome at install
time with `tabpilot install-stack --memory-high 4G` — reclaim pressure, not the
OOM killer, so a runaway browser slows down instead of dying.

Then point your MCP client at it over SSH — no port opened, screenshots streamed back inline:

```json
{
  "mcpServers": {
    "tabpilot": {
      "command": "ssh",
      "args": ["you@your-server", "TABPILOT_REMOTE=1 ~/.local/bin/tabpilot serve"]
    }
  }
}
```

MCP speaks over stdio, and SSH is a pipe, so this needs nothing else. See [docs/UBUNTU.md](docs/UBUNTU.md) for signing in by hand over a tunnelled VNC, and for why the CDP port stays on loopback.

## Reading pages without burning your context

The default move — `eval_js("document.body.innerText")` — spends thousands of tokens to answer questions that cost twenty. TabPilot gives you three cheaper tools, roughly in order of preference:

```python
query_dom(selector="button[type=submit]")       # is it disabled? ~10 lines
read_tab(selector="#bug-list")                  # just that section, as markdown
read_tab()                                      # whole page, boilerplate stripped, budgeted
```

`read_tab` strips nav, headers, footers and scripts, converts what is left to markdown, and caps the result — telling you when it truncated and how much it cut, so you can narrow the `selector` instead of guessing.

## Addressing tabs

Prefer `url_pattern`, a regex matched against tab URLs:

```python
read_tab(url_pattern=r"tester\.test\.io/tests/\w+/bugs")
```

Tab ids change on navigation. A cached id eventually points at a different page, and the action lands somewhere you did not intend. A regex keeps working for a whole session. With neither argument, TabPilot uses the focused tab — and if several tabs match and none has focus, it refuses rather than guessing.

## Forms that fight back

Three failure modes cost more debugging time than everything else combined. Each has a tool.

**React ignores your typing.** Assigning `.value` is invisible to React: it caches the last value it wrote, sees no change, and drops the event. The field looks filled while the component state stays empty, and submit fails validation for no visible reason. `fill` goes through the prototype's native setter and fires both `input` and `change`.

```python
fill(selector="#title", value="Checkout fails on Safari 17")
```

**Matrix questions submit half-empty.** Click twenty-eight rows in one JavaScript task and React batches the updates, committing only the last one. Twenty-seven rows stay blank, submit fails, and the page appears stuck in a loop. `fill_matrix` drives one row per task with a delay, then re-scans to confirm.

```python
scan_matrix()                                   # 28 rows, 28 unanswered
fill_matrix(column_index=1)                     # clicked: 28 | still unanswered: 0
```

**React-Select has no `<select>` to set.** There is no value to assign — the menu must be opened, given time to mount, and the option clicked. `select_option` detects this case and says so; `select_option_ui` does it.

```python
select_option(selector="#os", values=["iOS", "Android"])   # native, or Select2
select_option_ui(control_selector=".os-picker", option_text="iOS 17")
```

## Evidence

```python
screenshot(full_page=True, label="checkout-error")
screenshot(selector=".error-banner")
```

Captures the tab you asked for whether or not it is frontmost, works inside Xvfb, and saves to `~/.tabpilot/screenshots`. When the client is on another machine the image comes back inline, because a path on the server means nothing to it.

A warning worth repeating: on a bare server **Chrome renders missing glyphs as empty boxes and reports no error**. Screenshots look fine to the process producing them and turn out to be worthless as evidence. `install.sh` installs Noto and Liberation; `doctor` checks Vietnamese and CJK coverage specifically.

## Configuration

Every flag has an environment variable, which is what you use in an MCP config.

| Variable | Flag | Default | |
|:--|:--|:--|:--|
| `TABPILOT_BACKEND` | `--backend` | `auto` | `auto`, `cdp`, `applescript` |
| `TABPILOT_CDP_HOST` | `--cdp-host` | `127.0.0.1` | |
| `TABPILOT_CDP_PORT` | `--cdp-port` | `9222` | |
| `TABPILOT_MAX_CHARS` | `--max-chars` | `20000` | default read budget |
| `TABPILOT_TIMEOUT_MS` | `--timeout-ms` | `20000` | |
| `TABPILOT_MATRIX_DELAY_MS` | `--matrix-delay-ms` | `80` | raise if matrix rows stay blank |
| `TABPILOT_SCREENSHOT_DIR` | `--screenshot-dir` | `~/.tabpilot/screenshots` | |
| `TABPILOT_RETURN_IMAGES` | `--return-images` | `auto` | `auto`, `always`, `never` |
| `TABPILOT_REMOTE` | — | unset | set it over SSH: flips images to inline |
| `TABPILOT_APPLICATION_NAME` | `--application-name` | `Google Chrome` | macOS: try `Arc`, `Chromium` |

## What this is not

TabPilot drives **your** browser, with **your** sessions. There is no sandbox. A `click` lands in your real account.

- It will not launch or manage a browser for you outside the Ubuntu stack. That is deliberate — the whole point is attaching to the browser you already trust.
- It has no anti-detection features. Trusted input comes from using CDP properly, not from evading anything.
- Reaching Chrome from another machine goes through an SSH tunnel, never by binding CDP to `0.0.0.0`. The debugging port is unauthenticated: anyone who can route to it can read your cookies and act as you.

## Development

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/pytest                    # unit tests, no browser needed
.venv/bin/pytest -m live            # smoke tests against a real Chrome on :9222
```

The JavaScript payloads live as real `.js` files in [`src/tabpilot/js/`](src/tabpilot/js/) so they can be linted and diffed on their own; [`payloads.py`](src/tabpilot/payloads.py) loads them and passes arguments as JSON, which is why no caller ever has to think about escaping. Architecture and design rationale: [docs/DESIGN.md](docs/DESIGN.md).

## License

MIT — see [LICENSE](LICENSE).
