<p align="center">
  <img src="https://raw.githubusercontent.com/hunglp97/tabpilot-mcp/main/docs/assets/banner.png" alt="TabPilot Banner" width="100%" onerror="this.style.display='none'"/>
</p>

# 🧭 TabPilot

<p align="center">
  <strong>Drive the real Chrome you are already logged into — on macOS, Windows, and Headless Ubuntu 24/7.</strong>
</p>

<p align="center">
  <a href="https://github.com/hunglp97/tabpilot-mcp/actions/workflows/ci.yml"><img src="https://github.com/hunglp97/tabpilot-mcp/actions/workflows/ci.yml/badge.svg" alt="CI Status"></a>
  <a href="https://glama.ai/mcp/servers/hunglp97/tabpilot-mcp"><img src="https://glama.ai/mcp/servers/hunglp97/tabpilot-mcp/badges/score.svg" alt="tabpilot-mcp MCP server – quality and maintenance score on Glama"></a>
  <a href="https://modelcontextprotocol.io"><img src="https://img.shields.io/badge/MCP-Standard%20Compatible-00C49F.svg" alt="MCP Compatible"></a>
  <a href="https://www.python.org"><img src="https://img.shields.io/badge/Python-3.10%20%7C%203.11%20%7C%203.12%20%7C%203.13%20%7C%203.14-3776AB" alt="Python Versions"></a>
  <a href="https://github.com/hunglp97/tabpilot-mcp/blob/main/LICENSE"><img src="https://img.shields.io/badge/License-MIT-blue.svg" alt="License"></a>
  <a href="https://github.com/hunglp97/tabpilot-mcp/stargazers"><img src="https://img.shields.io/github/stars/hunglp97/tabpilot-mcp?style=social" alt="GitHub Stars"></a>
</p>

<p align="center">
  <a href="https://glama.ai/mcp/servers/hunglp97/tabpilot-mcp">
    <img src="https://glama.ai/mcp/servers/hunglp97/tabpilot-mcp/badges/card.svg" alt="tabpilot-mcp MCP server – quality and maintenance score on Glama">
  </a>
</p>

---

Most browser MCP servers launch a **fresh, blank browser instance**. That works for scraping static pages, but fails completely on tasks that matter:
- 🚫 **Cloudflare & Bot Shields** immediately flag fresh automation browsers.
- 🚫 **Corporate SSO, Okta, & 2FA** make authenticating from scratch painful or impossible.
- 🚫 **Read-only tab viewers** can only *look* at DOM text, not click or fill forms.

**TabPilot bridges this gap.** It attaches directly to your **existing, logged-in Google Chrome**. Your AI agents (Claude, Cursor, Antigravity, Cline) can read pages with up to **99% token savings**, click elements with authentic mouse events (`isTrusted: true`), defeat React state caching traps, solve multi-row survey grids, and capture background screenshots without stealing window focus.

```
┌───────────────────────────────────────────────────────────────────────────────────┐
│  YOUR WORKSPACE                     TABPILOT MCP ENGINE           REAL BROWSER    │
│                                                                                   │
│  ┌──────────────────────┐          ┌──────────────────────┐      ┌──────────────┐ │
│  │ AI Agents            │          │ TabPilot MCP Server  │      │ Real Chrome  │ │
│  │ - Claude Desktop     │  stdio / │ - Token Budget Slicer│ CDP  │ - Active SSO │ │
│  │ - Cursor IDE         │ ───────> │ - Native Form Setters│────> │ - Cookies    │ │
│  │ - Antigravity/Gemini │   SSH    │ - Matrix Grid Driver │<──── │ - 2FA Saved  │ │
│  │ - Cline / Windsurf   │          │ - Dual CDP/AppleScr. │      │ - Real Finger│ │
│  └──────────────────────┘          └──────────────────────┘      └──────────────┘ │
└───────────────────────────────────────────────────────────────────────────────────┘
```

---

## ⚡ Quick Start

### 1. Test your environment in one command
Run `tabpilot doctor` via `uvx` (no installation required):

```bash
uvx --from git+https://github.com/hunglp97/tabpilot-mcp tabpilot doctor
```

### 2. Configure your MCP Client
Add to `claude_desktop_config.json` or `.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "tabpilot": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/hunglp97/tabpilot-mcp", "tabpilot-mcp"]
    }
  }
}
```

### 3. Launch Chrome with Remote Debugging
Quit Chrome completely, then start it pointing to your persistent automation profile:

```bash
# macOS
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
  --remote-debugging-port=9222 --user-data-dir="$HOME/tabpilot-chrome"

# Linux
google-chrome --remote-debugging-port=9222 --user-data-dir="$HOME/tabpilot-chrome"

# Windows
taskkill /F /IM chrome.exe
"C:\Program Files\Google\Chrome\Application\chrome.exe" ^
  --remote-debugging-port=9222 --user-data-dir="%USERPROFILE%\tabpilot-chrome"
```

> [!IMPORTANT]
> **🍎 macOS Zero-Config Fallback:**  
> On macOS, if you do not launch Chrome with debugging flags, TabPilot automatically falls back to AppleScript to drive the Chrome you already have open.  
> **Prerequisite:** In Google Chrome, go to **View → Developer → check "Allow JavaScript from Apple Events"**.

---

## 💰 How TabPilot Saves 90%+ Tokens

Naive browser automation dumps `document.body.innerText` or raw HTML, wasting 15,000–45,000 tokens on navigation bars, cookies banners, and tracking scripts.

TabPilot provides **Progressive Token Slicing**:

```
Raw Page Dump (eval_js body)  ██████████████████████████████████████ 18,000+ tokens
read_tab() (Link-Density)     ███░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░ ~1,200 tokens (93% saved)
read_tab(selector)            █░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░ ~350 tokens (98% saved)
query_dom(selector)           ▏                                      ~18 tokens (99.9% saved)
```

| Method | Tokens | Savings | Best For |
| :--- | :---: | :---: | :--- |
| **`query_dom(selector)`** | **~15–30** | **99.9%** | Checking button states (`disabled`), inputs, badges, or alerts |
| **`read_tab(selector)`** | **~300–600** | **98.0%** | Reading isolated articles, ticket cards, or form sections |
| **`read_tab()`** *(Link-Density)* | **~1,200–1,800** | **93.0%** | Full-page reads with nav, ads, headers, and footers stripped |
| ❌ *Naive `eval_js` innerText* | 15,000–35,000 | 0% | *Context budget incinerator* |

> 💡 **Enforced Character Budget:** Reads default to `--max-chars 20000`. If truncated, TabPilot alerts the model with exact cut sizes, prompting it to narrow down with `selector` instead of hallucinating.

---

## 🥊 Driving Forms That Fight Back

Modern Single-Page Applications (React, Vue, Svelte) defeat standard automation scripts. TabPilot solves the 3 most infamous SPA traps:

```
┌───────────────────────────────────────────────────────────────────────────────────┐
│ TRAP 1: React Synthetic Cache                                                     │
│ Standard `.value = 'x'` is ignored by React's internal tracker.                   │
│ ✅ TabPilot calls native property setters + fires both `input` and `change`.      │
├───────────────────────────────────────────────────────────────────────────────────┤
│ TRAP 2: Multi-Row Survey Grid Batching                                            │
│ Clicking 25 matrix rows in one JS tick causes React to commit only the last row.  │
│ ✅ `fill_matrix` clicks row-by-row across microtasks with delays, then confirms.  │
├───────────────────────────────────────────────────────────────────────────────────┤
│ TRAP 3: Virtual Portals (React-Select, Headless UI)                               │
│ No `<select>` exists; options render only after clicking the trigger.             │
│ ✅ `select_option_ui` clicks trigger, awaits portal mount, and selects option.    │
└───────────────────────────────────────────────────────────────────────────────────┘
```

---

## ⚖️ Comparison

| Capability | Read-Only Tab Readers | Headless Bots (Puppeteer/Playwright) | 🧭 **TabPilot** |
| :--- | :---: | :---: | :---: |
| **Uses Existing Sessions & Cookies** | ✅ | ❌ Fresh empty profile | ✅ **Real Chrome (No re-login)** |
| **Bypasses Cloudflare & Bot Shields** | ✅ Human | ❌ Bot fingerprint | ✅ **Human browser fingerprint** |
| **Form Driving & Clicking** | ❌ Read-only | ✅ | ✅ **Full Bidirectional Driving** |
| **React Synthetic Event Fix** | ❌ | ⚠️ Often missed | ✅ **Native prototype setters** |
| **Survey Matrix Handler** | ❌ | ❌ Batched drops | ✅ **`fill_matrix` task scheduling** |
| **Token-Optimized Extraction** | ⚠️ Basic text | ❌ Raw DOM / costly vision | ✅ **`query_dom` (18 tokens)** |
| **Background Tab Screenshots** | ❌ Must be active | ✅ | ✅ **Off-screen CDP captures** |
| **Headless Ubuntu 24/7 Daemon** | ❌ macOS only | ⚠️ Complex Docker | ✅ **Native systemd stack** |
| **External Dependencies** | Minimal | ❌ Heavy Node/Playwright binaries | ✅ **Zero dependencies beyond MCP** |

---

## 🐧 24/7 Headless Ubuntu Server Stack

TabPilot is engineered to run permanently on cloud VPS servers (AWS, Hetzner, DigitalOcean) with zero exposed ports:

```
[ Laptop / Client ] ──( Encrypted SSH Pipe )──> [ Remote Ubuntu Server ]
                                                        │
                                                        ▼
                                             [ TabPilot MCP Server ]
                                                        │ (CDP 127.0.0.1:9222)
                                                        ▼
┌───────────────────────────────────────────────────────────────────────────────────┐
│ Managed Systemd User Stack (Restart=always, Linger Enabled)                       │
│   tabpilot-xvfb.service   ──>   tabpilot-wm.service   ──>   tabpilot-chrome.service
│   (Virtual Framebuffer)         (Openbox Window Mgr)        (Real Chrome + SSO)   │
└───────────────────────────────────────────────────────────────────────────────────┘
```

1. **One-Command Setup:** `bash deploy/ubuntu/install.sh` configures Xvfb, Openbox, Chrome, and systemd services with `Restart=always`.
2. **Font Protection (Anti-Tofu):** Headless servers often render Vietnamese and CJK characters as empty square boxes. `install.sh` installs Noto and Liberation fonts; `doctor` validates text rendering.
3. **Cgroup Memory Caps:** Prevent Chrome from exhausting host RAM:
   ```bash
   tabpilot install-stack --memory-high 4G
   ```
4. **Zero Remote Attack Surface:** Chrome CDP stays locked to `127.0.0.1`. Remote MCP connections run securely through SSH pipes (`ssh you@server "tabpilot serve"`).

---

## 🧰 Tools & Resources Reference

TabPilot exposes **17 tools** and **2 live resources**:

### 🔍 Reading & DOM Inspection
- **`query_dom(selector, attrs, limit, visible_only)`**: Atomic element inspection (~18 tokens). Checks disabled, checked, values.
- **`read_tab(selector, mode, max_chars, url_pattern)`**: Link-density markdown extraction with token caps.
- **`list_tabs(url_pattern)`**: Lists open tabs with IDs, titles, and URLs (regex filterable).
- **`eval_js(expression, timeout_ms)`**: Runs arbitrary JS in tab context; automatically awaits Promises in CDP.

### 🧭 Navigation & Tabs
- **`open_tab(url, activate, wait_for_load)`**: Opens a URL in a new tab, awaiting page completion.
- **`close_tab(url_pattern, tab_id)`**: Closes tab and polls until process confirms destruction.
- **`navigate(url, wait_for_load)`**: Points tab to a new URL and awaits document load.
- **`activate_tab(url_pattern, tab_id)`**: Brings target tab to foreground focus.

### 🖱️ Interaction & Complex Forms
- **`click(selector, text, nth)`**: Scrolls element into view and emits trusted mouse event (`isTrusted: true`).
- **`fill(selector, value, clear, press_enter)`**: Sets form inputs via native prototype setters to trigger React/Vue.
- **`select_option(selector, values, by)`**: Selects options in native `<select>` or Select2 dropdowns.
- **`select_option_ui(control_selector, option_text)`**: Clicks trigger, waits for popup portal, and selects item.
- **`wait_for(selector, state, text, timeout_ms)`**: Polls until condition holds (`visible`, `hidden`, `text`, `enabled`).

### 📋 Matrix Surveys & Evidence
- **`scan_matrix(selector)`**: Discovers multi-row grid questions and flags unanswered rows.
- **`fill_matrix(column_index, rows, delay_ms)`**: Answers matrix rows sequentially with task delays.
- **`screenshot(full_page, selector, label)`**: Offscreen capture of any tab (local path or inline base64).
- **`browser_status()`**: Reports active backend, capabilities, CDP endpoint, and open tab count.

### 📦 Live MCP Resources
- `tab://active` — Markdown stream of the currently focused tab.
- `tab://{tab_id}` — Markdown stream of any specific tab by ID.

---

## 🛠️ CLI Reference

```bash
tabpilot doctor                # Run diagnostic suite (Chrome, CDP, fonts, permissions)
tabpilot tabs                  # List open browser tabs in terminal
tabpilot serve                 # Start MCP server on stdio

# Linux Managed Stack Commands
tabpilot install-stack         # Install systemd user services
tabpilot up / tabpilot down    # Start or stop the headless stack
tabpilot status                # Check systemd stack status
tabpilot logs chrome -f        # Follow live Chrome logs
```

---

## 🧪 Testing

```bash
git clone https://github.com/hunglp97/tabpilot-mcp.git
cd tabpilot-mcp
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# Unit tests (Zero browser needed)
pytest -v

# Live integration tests (against Chrome on port 9222)
pytest -v -m live
```

---

## 📄 License

Distributed under the **MIT License**. See [LICENSE](LICENSE) for details.

---

<p align="center">
  Built with ❤️ for AI Engineers, QA Automators, and Power Users who demand real browser agency.
  <br>
  <strong>Star ⭐ TabPilot on GitHub if it saved your agents from bot shields and token burn!</strong>
</p>
