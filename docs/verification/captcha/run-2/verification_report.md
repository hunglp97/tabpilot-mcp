# CAPTCHA Verification Report — Run 2 (Post-Fix Validation)
**Date:** 2026-09-27  
**Environment:** macOS, Python 3.14.3, Google Chrome 154.0.8037.57 (Headless CDP)  
**Evidence Artifacts:**
- `results.json`: Real Chrome fixture & concurrency executions
- `fault_results.json`: Transport fault injection & hard deadline budget enforcement
- `mcp_result.json`: MCP stdio client-server roundtrip
- `source-sha256.json`: Fingerprints of all 67 repository source files

---

## 1. Executive Summary

All 11 findings (F01–F11) identified during the initial review have been resolved, covered by dedicated regression tests in `tests/test_captcha_regressions.py`, and verified against real headless Chrome and fault injections.

- **Non-live test suite**: 207 passed, 53 deselected in 9.87s (`rtk proxy .venv/bin/python -m pytest -q -m 'not live'`)
- **Live CAPTCHA test suite**: 6 passed in 14.76s (`rtk proxy .venv/bin/python -m pytest tests/test_live_captcha.py`)
- **Regression test suite**: 11 passed in 0.80s (`rtk proxy .venv/bin/python -m pytest tests/test_captcha_regressions.py`)
- **Total tool count**: Exactly 19 tools registered and validated over MCP stdio protocol.

---

## 2. Findings Resolution Matrix (F01 – F11)

| Finding ID | Priority | Description | Resolution in Code | Regression Test & Evidence |
|---|---|---|---|---|
| **F01** | P1 | Verify button clicks generic form submit / double submits | Scoped Verify strictly to challenge verify buttons (`#recaptcha-verify-button`, `#captcha-verify-btn`, `button[id*="verify"]`). Removed document-level submit fallback and synthetic `btn.click()`. | `test_f01_verify_does_not_submit_business_form`<br>`verify_submits_business_form.submit_count = 0` |
| **F02** | P1 | Unrelated checked checkboxes on page cause false widget pass | Scoped UI pass checks strictly to `candidate.widget_ref` and its immediate challenge container. Added validation for error/expired markers (`.recaptcha-checkbox-expired`, `.rc-anchor-error`, `.cf-turnstile-error`). | `test_f02_unrelated_checkbox_does_not_cause_false_pass`<br>`unrelated_checkbox_false_pass.status = "waiting"` |
| **F03** | P1 | Actions from stale observation executed after navigation | Validated DOM lifecycle identity (`window.__tabpilot_doc_id`), widget existence, tile ID whitelist, and `image_id` matching before dispatching input events. Returns `stale_observation` with 0 side effects. | `test_f03_mismatched_image_id_returns_stale_observation`<br>`stale_document_action.unrelated_clicks = 0` |
| **F04** | P1 | `needs_agent` returned only text under default `return_images=auto` | Updated `_finish_step` in `src/tabpilot/captcha.py` to always return `[result.to_json(), Image(...)]` whenever image bytes exist, irrespective of `return_images` setting. | `test_f04_needs_agent_guarantees_inline_image_content`<br>`mcp_result.content_types = ["text", "image"]` |
| **F05** | P1 | OOPIF targets advertised but frames not attached or routed | Added `Target.setAutoAttach(flatten=True)`, handled `Target.attachedToTarget`/`Target.detachedFromTarget`, populated `FrameRef.session_id`, and routed commands through attached target sessions. | `test_f05_oopif_frame_discovery_and_routing`<br>`oopif_frame_discovery.child_refs = 2`, `session_ids` populated |
| **F06** | P1 | Crop used client viewport coordinates instead of page coordinates | Separated `crop_rect` (page coordinates with `window.scrollX`/`window.scrollY` for CDP screenshot clip) from `viewport_rect` (client coordinates for mouse/drag dispatch). | `test_f06_scrolled_crop_rect_uses_page_coordinates`<br>`scrolled_capture_mapping.crop_y_sent_to_page_capture = 1008` (vs viewport y 281) |
| **F07** | P1 | Action deduplication was not atomic under concurrent callers | Added `threading.Lock()` and `in_flight_actions` tracking to `SolveSession`. Synchronized callers with identical `action_id` so only 1 thread executes input, while concurrent callers wait and receive the identical receipt. | `test_f07_concurrent_duplicate_action_id_executes_once`<br>`concurrent_duplicate_action.click_count = 1` |
| **F08** | P2 | Repeated `start` returned orphaned solve ID | In `_handle_start`, when `acquire_solve` returns an active solve, immediately returns that active solve's state/observation without re-clicking anchors or generating a conflicting solve ID. | `test_f08_repeated_start_returns_active_solve`<br>`repeated_start.second_is_resumable = true` |
| **F09** | P2 | Optional expected fields triggered `ReferenceError: None is not defined` | Serialized options with `json.dumps()` in `verify_access` and read live URL via `window.location.href`. | `test_f09_optional_expected_fields_json_encoding`<br>`optional_expected_fields.verified = true` |
| **F10** | P2 | Transport event flood indefinitely extended command timeouts | Enforced hard monotonic deadline checks `if time.monotonic() >= deadline: raise TimeoutError_` in `CDPBackend._command`. | `test_f10_hard_deadline_enforced_under_event_flood`<br>`event_flood_timeout.elapsed_ms = 10`, `raised_timeout_error = true` |
| **F11** | P2 | Inspection failure converted to `no_captcha` | In `_handle_start`, branched on `DetectionStatus.INSPECTION_FAILED`, returning `SolveStatus.UNVERIFIED` with failure evidence instead of falsely claiming absence of CAPTCHA. | `test_f11_inspection_failure_preserved_in_solve_start`<br>`inspection_error_start.status = "unverified"` |

---

## 3. Test Suites & Commands Executed

### Unit & Non-Live Suite
```sh
rtk proxy .venv/bin/python -m pytest -q -m 'not live'
```
**Output:** `207 passed, 53 deselected in 9.87s`

### Regression Suite (F01–F11)
```sh
rtk proxy .venv/bin/python -m pytest tests/test_captcha_regressions.py
```
**Output:** `11 passed in 0.80s`

### Live CAPTCHA Fixture Suite (Chrome 154 Headless)
```sh
rtk proxy .venv/bin/python -m pytest tests/test_live_captcha.py
```
**Output:** `6 passed in 14.76s`
- `test_live_detect_fixtures`: Passed
- `test_live_solve_turnstile_checkbox`: Passed
- `test_live_solve_turnstile_interstitial_wait`: Passed
- `test_live_solve_recaptcha_checkbox`: Passed
- `test_live_solve_text_captcha`: Passed
- `test_live_solve_slider_puzzle`: Passed

### Real MCP Client-Server Stdio Roundtrip
```sh
rtk proxy .venv/bin/python docs/verification/captcha/run-2/mcp_roundtrip.py
```
**Output:**
```json
{
  "tool_count": 19,
  "content_types": [
    "text",
    "image"
  ],
  "is_error": false,
  "status": "needs_agent",
  "has_image_content": true
}
```

### Transport Fault & Concurrency Verification
```sh
rtk proxy .venv/bin/python docs/verification/captcha/run-2/verify_fixes.py
rtk proxy .venv/bin/python docs/verification/captcha/run-2/transport_faults.py
```
**Outputs:**
- Deduplication: `{"click_count": 1, "identical_results": true}`
- Event flood deadline: `{"requested_ms": 10, "elapsed_ms": 10, "raised_timeout_error": true}`
- Inspection error preservation: `{"status": "unverified", "preserved_failure": true}`

---

## 4. Qualification Scope & Boundaries

1. **Qualified Solvers**:
   - Cloudflare Turnstile & Interstitial (Checkbox & Passive Wait)
   - reCAPTCHA v2 (Checkbox & Image Grid via Agent-Vision Loop)
   - Alphanumeric Text CAPTCHA (Agent-Vision Loop: `type_answer` + `verify`)
   - Slider Puzzle (Agent-Vision Loop: `drag`)
2. **Safety Gates**:
   - Zero side-effects on stale observations
   - Zero business form submissions from `verify` actions
   - No false positives from unrelated page inputs or expired states
3. **P4 Placeholder Boundaries**:
   - Base package remains pure Python + MCP SDK (zero ML dependencies).
   - Audio transcription, OCR, and OpenCV template matching remain optional future extensions and explicitly raise `NotImplementedError` if requested without extras.
