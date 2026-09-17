# Contributing to TabPilot

Thank you for your interest in contributing to **TabPilot**! We welcome contributions of all kinds: bug fixes, performance improvements, new tools, documentation enhancements, and testing additions.

---

## 🛠️ Development Setup

1. **Clone the repository:**
   ```bash
   git clone https://github.com/hunglp97/tabpilot-mcp.git
   cd tabpilot-mcp
   ```

2. **Create a virtual environment (Python >= 3.10):**
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install --upgrade pip
   pip install -e ".[dev]"
   ```

3. **Verify the installation:**
   ```bash
   tabpilot doctor
   ```

---

## 🧪 Running Tests

TabPilot features a comprehensive test suite designed to run quickly without requiring live browser dependencies for core units:

* **Run unit tests (Pure Python & mock backends, no browser needed):**
  ```bash
  pytest -v
  ```

* **Run live integration tests (Needs a running Chrome on port 9222):**
  ```bash
  pytest -v -m live
  ```

---

## 📐 Architecture & Principles

Before submitting code, please keep our core architectural rules in mind:

1. **Zero External Dependencies Beyond MCP SDK:**
   The CDP client is built on a custom, RFC 6455-compliant WebSocket client written purely with Python standard library `socket`. Do not add heavy external client dependencies (e.g. `websockets`, `aiohttp`, `playwright`).
2. **Layer Isolation:**
   - Tools in `server.py` catch `TabPilotError` and return human/model-readable remedy text (`ERROR [CODE] ... \n How to fix: ...`).
   - JavaScript payloads live as standalone files in `src/tabpilot/js/` so they can be linted and diffed independently.
   - Arguments are always passed as JSON parameters, never interpolated as raw strings into script bodies.
3. **Impersonal, Budget-Aware Extraction:**
   Extraction tools must respect token budgets (`--max-chars`) and inform callers if truncation occurred.

---

## 🚀 Submitting a Pull Request (PR)

1. Create a descriptive feature branch:
   ```bash
   git checkout -b feat/my-improvement
   ```
2. Make your changes with clear, concise commit messages following [Conventional Commits](https://www.conventionalcommits.org/):
   - `feat: add support for ...`
   - `fix: resolve race condition in ...`
   - `docs: update troubleshooting guide ...`
3. Ensure all tests pass:
   ```bash
   pytest
   ```
4. Push your branch to GitHub and open a Pull Request against the `main` branch.
5. Provide a summary of what was changed and how it was tested.

Thank you for making TabPilot better for everyone!
