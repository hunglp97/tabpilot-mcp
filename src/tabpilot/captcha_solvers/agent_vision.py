"""Agent vision solver for image grid, image select, text, and slider challenges."""

from __future__ import annotations

import json
import time
from typing import Any

from ..backends.base import Capability, TabInfo
from ..captcha_state import (
    Action,
    ActionKind,
    ChallengeKind,
    Observation,
    SolveResult,
    SolveSession,
    SolveStatus,
    VerificationLevel,
)
from ..captcha_verify import verify_access, verify_widget_passed
from .base import CaptchaSolverAdapter
from ..session import Session


class AgentVisionSolver(CaptchaSolverAdapter):
    name = "agent_vision"
    requires = frozenset({Capability.EVAL, Capability.SCREENSHOT, Capability.TRUSTED_INPUT})

    def can_handle(self, candidate) -> bool:
        return candidate.challenge_kind in {
            ChallengeKind.IMAGE_GRID.value,
            ChallengeKind.IMAGE_SELECT.value,
            ChallengeKind.IMAGE_TEXT.value,
            ChallengeKind.SLIDER.value,
            ChallengeKind.CHECKBOX.value,
            ChallengeKind.UNKNOWN.value,
        }

    def solve_step(
        self,
        session: Session,
        tab: TabInfo,
        solve_session: SolveSession,
    ) -> SolveResult:
        # Check if already passed
        passed, detail, ev = verify_widget_passed(
            session, tab, solve_session.candidate, solve_session.baseline_token_fingerprint
        )
        if passed:
            solve_session.evidence.append(ev)
            solve_session.status = SolveStatus.WIDGET_PASSED.value
            solve_session.verification_level = VerificationLevel.WIDGET.value
            return SolveResult(
                status=solve_session.status,
                verification_level=solve_session.verification_level,
                solve_id=solve_session.solve_id,
                candidate_id=solve_session.candidate.candidate_id,
                provider=solve_session.candidate.provider,
                challenge_kind=solve_session.candidate.challenge_kind,
                solver=self.name,
                attempts=solve_session.attempts,
                rounds=solve_session.rounds,
                actions_used=solve_session.actions_used,
                elapsed_ms=solve_session.elapsed_ms,
                remaining_ms=solve_session.remaining_ms,
                evidence=solve_session.evidence,
                detail="Widget is already verified / passed",
            )

        budget_status = solve_session.check_budgets()
        if budget_status:
            solve_session.status = budget_status.value
            return SolveResult(
                status=solve_session.status,
                solve_id=solve_session.solve_id,
                candidate_id=solve_session.candidate.candidate_id,
                solver=self.name,
                attempts=solve_session.attempts,
                rounds=solve_session.rounds,
                actions_used=solve_session.actions_used,
                elapsed_ms=solve_session.elapsed_ms,
                remaining_ms=solve_session.remaining_ms,
                evidence=solve_session.evidence,
                detail=f"Budget limit reached: {budget_status.value}",
            )

        solve_session.rounds += 1

        # Query challenge layout from DOM
        layout = self._inspect_challenge_layout(session, tab, solve_session)
        crop_rect = layout.get("crop_rect")

        # Capture screenshot of challenge area
        screenshot_timeout = max(3.0, min(15.0, solve_session.remaining_ms / 1000.0))
        try:
            image_bytes = session.backend.screenshot(
                tab.id,
                clip=crop_rect,
                image_format="png",
                timeout_s=screenshot_timeout,
            )
        except Exception as exc:
            # Fall back to full viewport screenshot
            image_bytes = session.backend.screenshot(
                tab.id,
                image_format="png",
                timeout_s=screenshot_timeout,
            )
            crop_rect = {"x": 0, "y": 0, "width": 800, "height": 600}

        obs_id = f"obs_{solve_session.solve_id}_{solve_session.rounds}_{int(time.time() * 1000)}"
        img_id = f"img_{solve_session.rounds}"

        allowed_actions = [
            {"kind": ActionKind.SELECT_TILE.value, "description": "Select a tile by tile_id (e.g. 'tile-0')"},
            {"kind": ActionKind.CLICK_POINT.value, "description": "Click at normalized point [x, y] in [0, 1]"},
            {"kind": ActionKind.TYPE_ANSWER.value, "description": "Enter text answer for CAPTCHA"},
            {"kind": ActionKind.DRAG.value, "description": "Drag from normalized point to drag_to point"},
            {"kind": ActionKind.VERIFY.value, "description": "Click the Verify/Submit button"},
            {"kind": ActionKind.REFRESH.value, "description": "Click the Reload/Skip button"},
        ]

        obs = Observation(
            observation_id=obs_id,
            solve_id=solve_session.solve_id,
            image_id=img_id,
            prompt=layout.get("prompt") or "Solve the challenge shown in the image",
            prompt_source="dom" if layout.get("prompt") else "image",
            image_width=int(crop_rect.get("width", 400)),
            image_height=int(crop_rect.get("height", 400)),
            crop_rect=crop_rect,
            viewport_rect=layout.get("viewport_rect") or crop_rect,
            allowed_actions=allowed_actions,
            tiles=layout.get("tiles", []),
            controls=layout.get("controls", []),
            dynamic_grid=layout.get("dynamic_grid", False),
        )

        solve_session.last_observation = obs
        solve_session.last_observation_image_bytes = image_bytes
        solve_session.last_observation_image_format = "png"
        solve_session.status = SolveStatus.NEEDS_AGENT.value

        return SolveResult(
            status=solve_session.status,
            solve_id=solve_session.solve_id,
            candidate_id=solve_session.candidate.candidate_id,
            observation_id=obs_id,
            provider=solve_session.candidate.provider,
            challenge_kind=solve_session.candidate.challenge_kind,
            solver=self.name,
            attempts=solve_session.attempts,
            rounds=solve_session.rounds,
            actions_used=solve_session.actions_used,
            elapsed_ms=solve_session.elapsed_ms,
            remaining_ms=solve_session.remaining_ms,
            evidence=solve_session.evidence,
            detail=f"Challenge prompt: {obs.prompt}. Provide action in solve_captcha(operation='act').",
            next_action="act",
            observation=obs.to_dict(),
        )

    def handle_action(
        self,
        session: Session,
        tab: TabInfo,
        solve_session: SolveSession,
        action: Action,
    ) -> SolveResult:
        obs = solve_session.last_observation
        if not obs:
            solve_session.status = SolveStatus.STALE_OBSERVATION.value
            return self.solve_step(session, tab, solve_session)

        budget_status = solve_session.check_budgets()
        if budget_status:
            solve_session.status = budget_status.value
            return SolveResult(
                status=solve_session.status,
                solve_id=solve_session.solve_id,
                candidate_id=solve_session.candidate.candidate_id,
                solver=self.name,
                attempts=solve_session.attempts,
                rounds=solve_session.rounds,
                actions_used=solve_session.actions_used,
                elapsed_ms=solve_session.elapsed_ms,
                remaining_ms=solve_session.remaining_ms,
                evidence=solve_session.evidence,
                detail=f"Budget limit reached: {budget_status.value}",
            )

        solve_session.actions_used += 1
        v_rect = obs.viewport_rect or obs.crop_rect or {"x": 0, "y": 0, "width": 800, "height": 600}
        crop_x = float(v_rect.get("x", 0))
        crop_y = float(v_rect.get("y", 0))
        crop_w = float(v_rect.get("width", 800))
        crop_h = float(v_rect.get("height", 600))

        # --- Pre-action staleness & document lifecycle validation ---
        kind = action.kind
        if kind in (ActionKind.CLICK_POINT.value, ActionKind.DRAG.value):
            if action.image_id and action.image_id != obs.image_id:
                return SolveResult(
                    status=SolveStatus.STALE_OBSERVATION.value,
                    solve_id=solve_session.solve_id,
                    candidate_id=solve_session.candidate.candidate_id,
                    solver=self.name,
                    detail=f"Image ID mismatch (expected {obs.image_id!r}, got {action.image_id!r}). New observation required.",
                )

        # Check if document has changed or candidate widget is missing before dispatching input
        doc_check_expr = f"""(function() {{
            var wRef = {json.dumps(solve_session.candidate.widget_ref)};
            var el = wRef ? document.querySelector(wRef) : null;
            if (!el) el = document.querySelector('.recaptcha-challenge, .g-recaptcha, .cf-turnstile, .captcha-box, .captcha-slider, #grid');
            return {{
                doc_id: window.__tabpilot_doc_id || null,
                widget_found: Boolean(el)
            }};
        }})()"""
        try:
            doc_state = session.backend.eval_js(tab.id, doc_check_expr, timeout_s=3.0)
            if isinstance(doc_state, dict):
                if not doc_state.get("widget_found"):
                    return SolveResult(
                        status=SolveStatus.STALE_OBSERVATION.value,
                        solve_id=solve_session.solve_id,
                        candidate_id=solve_session.candidate.candidate_id,
                        solver=self.name,
                        detail="Challenge widget is no longer present in DOM. New observation required.",
                    )
                current_doc_id = doc_state.get("doc_id")
                if current_doc_id and solve_session.document_generation and current_doc_id != solve_session.document_generation:
                    return SolveResult(
                        status=SolveStatus.STALE_OBSERVATION.value,
                        solve_id=solve_session.solve_id,
                        candidate_id=solve_session.candidate.candidate_id,
                        solver=self.name,
                        detail="Document navigated to a new page. Previous observation is stale.",
                    )
        except Exception:
            pass

        # --- Dispatch action ---
        if kind == ActionKind.SELECT_TILE.value:
            target_id = action.target_id
            if obs.tiles:
                allowed_tile_ids = {t.get("tile_id") for t in obs.tiles if t.get("tile_id")}
                if target_id not in allowed_tile_ids:
                    raise ValueError(f"Tile target_id {target_id!r} is not in observation tiles {list(allowed_tile_ids)}")
            expr = f"""(function() {{
                var t = document.getElementById({json.dumps(target_id)});
                if (!t && {json.dumps(target_id or '')}.startsWith('tile-')) {{
                    var idx = parseInt({json.dumps(target_id or '')}.split('-')[1]);
                    var all = document.querySelectorAll('.rc-image-tile-target, .captcha-tile, .grid-tile');
                    if (all[idx]) t = all[idx];
                }}
                if (t) {{
                    t.scrollIntoView({{ block: 'center', behavior: 'instant' }});
                    var r = t.getBoundingClientRect();
                    return {{ x: Math.round(r.left + r.width / 2), y: Math.round(r.top + r.height / 2) }};
                }}
                return null;
            }})()"""
            pos = session.backend.eval_js(tab.id, expr, timeout_s=3.0)
            if isinstance(pos, dict) and "x" in pos:
                session.backend.click_at(tab.id, pos["x"], pos["y"], timeout_s=5.0)
            else:
                tile = next((t for t in obs.tiles if t.get("tile_id") == target_id), None)
                if tile and "rect" in tile:
                    tr = tile["rect"]
                    click_x = crop_x + tr["x"] + tr["width"] / 2.0
                    click_y = crop_y + tr["y"] + tr["height"] / 2.0
                elif target_id and target_id.startswith("tile-"):
                    idx = int(target_id.split("-")[1])
                    cols = 3
                    row = idx // cols
                    col = idx % cols
                    tile_w = crop_w / cols
                    tile_h = crop_h / cols
                    click_x = crop_x + col * tile_w + tile_w / 2.0
                    click_y = crop_y + row * tile_h + tile_h / 2.0
                else:
                    raise ValueError(f"Unknown tile target_id: {target_id!r}")
                session.backend.click_at(tab.id, click_x, click_y, timeout_s=5.0)
            time.sleep(0.3)

        elif kind == ActionKind.CLICK_POINT.value:
            if not action.point:
                raise ValueError("click_point requires 'point': {'x': ..., 'y': ...}")
            px = float(action.point["x"])
            py = float(action.point["y"])
            if not (0.0 <= px <= 1.0 and 0.0 <= py <= 1.0):
                raise ValueError(f"point coordinates must be normalized in [0, 1], got ({px}, {py})")

            # Resolve live viewport position
            live_expr = f"""(function() {{
                var wRef = {json.dumps(solve_session.candidate.widget_ref)};
                var el = wRef ? document.querySelector(wRef) : null;
                if (!el) el = document.querySelector('.recaptcha-challenge, .captcha-box, #grid, .captcha-container');
                if (el) {{
                    var r = el.getBoundingClientRect();
                    return {{ x: Math.round(r.left), y: Math.round(r.top), width: Math.round(r.width), height: Math.round(r.height) }};
                }}
                return null;
            }})()"""
            live_rect = session.backend.eval_js(tab.id, live_expr, timeout_s=3.0)
            if isinstance(live_rect, dict) and "x" in live_rect:
                vp_x = float(live_rect["x"])
                vp_y = float(live_rect["y"])
                vp_w = float(live_rect["width"])
                vp_h = float(live_rect["height"])
            else:
                vp_x, vp_y, vp_w, vp_h = crop_x, crop_y, crop_w, crop_h

            click_x = vp_x + px * vp_w
            click_y = vp_y + py * vp_h
            session.backend.click_at(tab.id, click_x, click_y, timeout_s=5.0)
            time.sleep(0.3)

        elif kind == ActionKind.TYPE_ANSWER.value:
            if action.text is None:
                raise ValueError("type_answer requires 'text'")
            field_ref = solve_session.candidate.response_field_ref
            expr = f"""(function() {{
                var ref = {json.dumps(field_ref)};
                var input = ref ? document.querySelector(ref) : null;
                if (!input) {{
                    var wRef = {json.dumps(solve_session.candidate.widget_ref)};
                    var root = wRef ? document.querySelector(wRef) : null;
                    var container = root ? (root.closest('.captcha-box, .challenge-section, .captcha-container') || root) : null;
                    if (container) input = container.querySelector('input[type="text"], input:not([type="hidden"])');
                }}
                if (!input) input = document.querySelector('.captcha-box input, #captcha-input, #answer');
                if (input) {{
                    input.scrollIntoView({{ block: 'center', behavior: 'instant' }});
                    input.focus();
                    input.value = {json.dumps(action.text)};
                    input.dispatchEvent(new Event('input', {{ bubbles: true }}));
                    input.dispatchEvent(new Event('change', {{ bubbles: true }}));
                    return true;
                }}
                return false;
            }})()"""
            found = session.backend.eval_js(tab.id, expr, timeout_s=3.0)
            if not found:
                return SolveResult(
                    status=SolveStatus.UNSUPPORTED.value,
                    solve_id=solve_session.solve_id,
                    candidate_id=solve_session.candidate.candidate_id,
                    solver=self.name,
                    detail="Could not find input element belonging to this CAPTCHA challenge.",
                )
            time.sleep(0.3)

        elif kind == ActionKind.DRAG.value:
            if not action.point or not action.drag_to:
                raise ValueError("drag requires 'point' and 'drag_to'")
            fx = float(action.point["x"])
            fy = float(action.point["y"])
            tx = float(action.drag_to["x"])
            ty = float(action.drag_to["y"])
            if not (0.0 <= fx <= 1.0 and 0.0 <= fy <= 1.0 and 0.0 <= tx <= 1.0 and 0.0 <= ty <= 1.0):
                raise ValueError("drag coordinates must be normalized in [0, 1]")

            # Scroll candidate into view and get fresh live viewport coordinates
            expr = f"""(function() {{
                var wRef = {json.dumps(solve_session.candidate.widget_ref)};
                var el = wRef ? document.querySelector(wRef) : null;
                if (!el) el = document.querySelector('.captcha-slider, .puzzle-slider, #slider-track, #drag-handle');
                if (el) {{
                    el.scrollIntoView({{ block: 'center', behavior: 'instant' }});
                    var r = el.getBoundingClientRect();
                    return {{ x: Math.round(r.left), y: Math.round(r.top), width: Math.round(r.width), height: Math.round(r.height) }};
                }}
                return null;
            }})()"""
            fresh_crop = session.backend.eval_js(tab.id, expr, timeout_s=3.0)
            if isinstance(fresh_crop, dict) and "x" in fresh_crop:
                crop_x = float(fresh_crop["x"])
                crop_y = float(fresh_crop["y"])
                crop_w = float(fresh_crop["width"])
                crop_h = float(fresh_crop["height"])

            from_x = crop_x + fx * crop_w
            from_y = crop_y + fy * crop_h
            to_x = crop_x + tx * crop_w
            to_y = crop_y + ty * crop_h
            session.backend.drag(tab.id, from_x, from_y, to_x, to_y, steps=20, duration_s=0.5, timeout_s=10.0)
            time.sleep(0.5)

        elif kind == ActionKind.VERIFY.value:
            expr = f"""(function() {{
                var wRef = {json.dumps(solve_session.candidate.widget_ref)};
                var root = wRef ? document.querySelector(wRef) : null;
                var container = root ? (root.closest('.challenge-section, .captcha-container, .recaptcha-challenge, .captcha-box') || root.parentElement || root) : null;
                // Specifically look for challenge verify buttons, NEVER generic form submit buttons
                var btn = (container ? container.querySelector('#recaptcha-verify-button, #captcha-verify-btn, button[id*="verify"], input[id*="verify"], .verify-btn, .btn-verify, button[aria-label*="Verify"], button[aria-label*="Xác minh"]') : null) ||
                          document.querySelector('#recaptcha-verify-button, #captcha-verify-btn, .recaptcha-challenge button[id*="verify"], .captcha-box button[id*="verify"]');
                if (btn) {{
                    btn.scrollIntoView({{ block: 'center', behavior: 'instant' }});
                    var r = btn.getBoundingClientRect();
                    return {{ x: Math.round(r.left + r.width / 2), y: Math.round(r.top + r.height / 2) }};
                }}
                return null;
            }})()"""
            pos = session.backend.eval_js(tab.id, expr, timeout_s=3.0)
            if isinstance(pos, dict) and "x" in pos:
                session.backend.click_at(tab.id, pos["x"], pos["y"], timeout_s=5.0)
            else:
                ctrl = next((c for c in obs.controls if c.get("kind") == "verify"), None)
                if ctrl and "rect" in ctrl:
                    cr = ctrl["rect"]
                    click_x = crop_x + cr["x"] + cr["width"] / 2.0
                    click_y = crop_y + cr["y"] + cr["height"] / 2.0
                    session.backend.click_at(tab.id, click_x, click_y, timeout_s=5.0)
                else:
                    return SolveResult(
                        status=SolveStatus.UNSUPPORTED.value,
                        solve_id=solve_session.solve_id,
                        candidate_id=solve_session.candidate.candidate_id,
                        solver=self.name,
                        detail="Could not locate Verify control for this challenge widget.",
                    )
            solve_session.attempts += 1
            time.sleep(1.0)

        elif kind == ActionKind.REFRESH.value:
            expr = """(function() {
                var btn = document.querySelector('#recaptcha-reload-button, .reload-btn, .refresh-btn');
                if (btn) {
                    btn.click();
                    return true;
                }
                return false;
            })()"""
            session.backend.eval_js(tab.id, expr, timeout_s=3.0)
            time.sleep(1.0)

        else:
            raise ValueError(f"Unsupported action kind: {kind!r}")

        # --- After action verification ---
        passed, detail, ev = verify_widget_passed(
            session, tab, solve_session.candidate, solve_session.baseline_token_fingerprint
        )
        solve_session.evidence.append(ev)
        if passed:
            if solve_session.expected:
                ok, acc_detail, acc_ev = verify_access(
                    session, tab, solve_session.expected, solve_session.deadline_monotonic
                )
                solve_session.evidence.append(acc_ev)
                if ok:
                    solve_session.status = SolveStatus.ACCESS_VERIFIED.value
                    solve_session.verification_level = VerificationLevel.ACCESS.value
                    return SolveResult(
                        status=solve_session.status,
                        verification_level=solve_session.verification_level,
                        solve_id=solve_session.solve_id,
                        candidate_id=solve_session.candidate.candidate_id,
                        provider=solve_session.candidate.provider,
                        challenge_kind=solve_session.candidate.challenge_kind,
                        solver=self.name,
                        attempts=solve_session.attempts,
                        rounds=solve_session.rounds,
                        actions_used=solve_session.actions_used,
                        elapsed_ms=solve_session.elapsed_ms,
                        remaining_ms=solve_session.remaining_ms,
                        evidence=solve_session.evidence,
                        detail="Access postconditions verified",
                    )

            solve_session.status = SolveStatus.WIDGET_PASSED.value
            solve_session.verification_level = VerificationLevel.WIDGET.value
            return SolveResult(
                status=solve_session.status,
                verification_level=solve_session.verification_level,
                solve_id=solve_session.solve_id,
                candidate_id=solve_session.candidate.candidate_id,
                provider=solve_session.candidate.provider,
                challenge_kind=solve_session.candidate.challenge_kind,
                solver=self.name,
                attempts=solve_session.attempts,
                rounds=solve_session.rounds,
                actions_used=solve_session.actions_used,
                elapsed_ms=solve_session.elapsed_ms,
                remaining_ms=solve_session.remaining_ms,
                evidence=solve_session.evidence,
                detail="Challenge verified successfully",
            )

        # Still needs agent (another round of observation/action)
        return self.solve_step(session, tab, solve_session)

    def _inspect_challenge_layout(
        self, session: Session, tab: TabInfo, solve_session: SolveSession
    ) -> dict[str, Any]:
        """Inspect the challenge element to extract prompt, bounding box, tiles, and controls."""
        try:
            expr = f"""(function() {{
                var candidatePageRect = null;
                var candidateViewportRect = null;
                var scrollX = window.scrollX || window.pageXOffset || 0;
                var scrollY = window.scrollY || window.pageYOffset || 0;
                var widgetRef = {json.dumps(solve_session.candidate.widget_ref)};
                var specificEl = widgetRef ? document.querySelector(widgetRef) : null;
                var container = specificEl ? (specificEl.closest('.challenge-section, form, .captcha-container, .recaptcha-challenge, .captcha-box, .captcha-slider') || specificEl) : null;
                var bframe = document.querySelector('iframe[src*="bframe"], iframe[title*="challenge"], .recaptcha-challenge, .h-captcha-challenge, .captcha-modal, .captcha-container');
                var el = container || bframe || document.querySelector('.captcha-box, #captcha, [class*="captcha"]');
                if (el) {{
                    var r = el.getBoundingClientRect();
                    candidateViewportRect = {{ x: Math.round(r.left), y: Math.round(r.top), width: Math.round(r.width), height: Math.round(r.height) }};
                    candidatePageRect = {{ x: Math.round(r.left + scrollX), y: Math.round(r.top + scrollY), width: Math.round(r.width), height: Math.round(r.height) }};
                }} else {{
                    candidateViewportRect = {{ x: 0, y: 0, width: window.innerWidth || 800, height: window.innerHeight || 600 }};
                    candidatePageRect = {{ x: Math.round(scrollX), y: Math.round(scrollY), width: window.innerWidth || 800, height: window.innerHeight || 600 }};
                }}

                // Prompt text
                var promptEl = (el ? el.querySelector(
                    '.rc-imageselect-desc-no-canonical, .rc-imageselect-instructions, .prompt-text, .challenge-instructions, .captcha-prompt, label[for*="captcha"], h3, strong'
                ) : null) || document.querySelector(
                    '.rc-imageselect-desc-no-canonical, .rc-imageselect-instructions, .prompt-text, .challenge-instructions, .captcha-prompt, label[for*="captcha"], h3, strong'
                );
                var prompt = promptEl ? (promptEl.innerText || promptEl.textContent || '').trim() : '';

                // Tiles if standard grid
                var tiles = [];
                var tileEls = el ? el.querySelectorAll('.rc-image-tile-target, .captcha-tile, .grid-tile') : [];
                if (tileEls.length === 0) {{
                    tileEls = document.querySelectorAll('.rc-image-tile-target, .captcha-tile, .grid-tile');
                }}
                if (tileEls.length > 0) {{
                    for (var i = 0; i < tileEls.length; i++) {{
                        var tr = tileEls[i].getBoundingClientRect();
                        tiles.push({{
                            tile_id: 'tile-' + i,
                            rect: {{ x: Math.round(tr.left - candidateViewportRect.x),
                                     y: Math.round(tr.top - candidateViewportRect.y),
                                     width: Math.round(tr.width),
                                     height: Math.round(tr.height) }}
                        }});
                    }}
                }} else if (candidateViewportRect && candidateViewportRect.width > 150) {{
                    // Generate synthetic 3x3 tiles relative to container
                    var cols = 3;
                    var rows = 3;
                    var tw = candidateViewportRect.width / cols;
                    var th = candidateViewportRect.height / rows;
                    for (var r = 0; r < rows; r++) {{
                        for (var c = 0; c < cols; c++) {{
                            tiles.push({{
                                tile_id: 'tile-' + (r * cols + c),
                                rect: {{ x: Math.round(c * tw), y: Math.round(r * th), width: Math.round(tw), height: Math.round(th) }}
                            }});
                        }}
                    }}
                }}

                // Controls: specifically challenge verify controls only
                var controls = [];
                var verifyBtn = (el ? el.querySelector('#recaptcha-verify-button, #captcha-verify-btn, button[id*="verify"], input[id*="verify"], .btn-verify, .verify-btn, button[aria-label*="Verify"], button[aria-label*="Xác minh"]') : null) ||
                                document.querySelector('#recaptcha-verify-button, #captcha-verify-btn, .recaptcha-challenge button[id*="verify"], .captcha-box button[id*="verify"]');
                if (verifyBtn && candidateViewportRect) {{
                    var vr = verifyBtn.getBoundingClientRect();
                    controls.push({{
                        kind: 'verify',
                        rect: {{ x: Math.round(vr.left - candidateViewportRect.x), y: Math.round(vr.top - candidateViewportRect.y), width: Math.round(vr.width), height: Math.round(vr.height) }}
                    }});
                }}

                return {{
                    crop_rect: candidatePageRect,
                    viewport_rect: candidateViewportRect,
                    prompt: prompt,
                    tiles: tiles,
                    controls: controls,
                    dynamic_grid: true
                }};
            }})()"""
            res = session.backend.eval_js(tab.id, expr, timeout_s=3.0)
            if isinstance(res, dict) and res.get("crop_rect"):
                return res
        except Exception:
            pass

        # Fall back to candidate.rect_css
        rect = solve_session.candidate.rect_css or {"x": 0, "y": 0, "width": 800, "height": 600}
        tiles = []
        if solve_session.candidate.challenge_kind in {
            ChallengeKind.IMAGE_GRID.value,
            ChallengeKind.IMAGE_SELECT.value,
        }:
            cols = 3
            rows = 3
            tw = rect.get("width", 300) / cols
            th = rect.get("height", 300) / rows
            for r in range(rows):
                for c in range(cols):
                    tiles.append({
                        "tile_id": f"tile-{r * cols + c}",
                        "rect": {
                            "x": int(c * tw),
                            "y": int(r * th),
                            "width": int(tw),
                            "height": int(th),
                        },
                    })

        return {
            "crop_rect": rect,
            "prompt": "Solve the CAPTCHA challenge",
            "tiles": tiles,
            "controls": [],
            "dynamic_grid": False,
        }
