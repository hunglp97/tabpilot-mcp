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

        # Capture screenshot of challenge area within remaining deadline budget
        rem_s = solve_session.remaining_ms / 1000.0
        screenshot_timeout = min(15.0, max(0.001, rem_s))
        try:
            image_bytes = session.backend.screenshot(
                tab.id,
                clip=crop_rect,
                image_format="png",
                timeout_s=screenshot_timeout,
            )
        except Exception:
            if solve_session.is_expired:
                solve_session.status = SolveStatus.TIMEOUT.value
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
                    detail="Screenshot capture timed out: deadline exceeded",
                )
            # Fall back to full viewport screenshot
            fallback_timeout = min(15.0, max(0.001, solve_session.remaining_ms / 1000.0))
            image_bytes = session.backend.screenshot(
                tab.id,
                image_format="png",
                timeout_s=fallback_timeout,
            )
            crop_rect = {"x": 0, "y": 0, "width": 800, "height": 600}

        # Check budget again after screenshot / layout I/O
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
            challenge_fingerprint=layout.get("fingerprint", ""),
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
            if not action.image_id or action.image_id != obs.image_id:
                return SolveResult(
                    status=SolveStatus.STALE_OBSERVATION.value,
                    solve_id=solve_session.solve_id,
                    candidate_id=solve_session.candidate.candidate_id,
                    solver=self.name,
                    detail=f"Image ID mismatch or missing (expected {obs.image_id!r}, got {action.image_id!r}). New observation required.",
                )

        # Check if document has changed, prompt changed, content changed, or candidate widget is missing before dispatching input
        doc_check_expr = f"""(function() {{
            var wRef = {json.dumps(solve_session.candidate.widget_ref)};
            var specificEl = wRef ? document.querySelector(wRef) : null;
            var container = specificEl ? (specificEl.closest('.challenge-section, form, .captcha-container, .recaptcha-challenge, .captcha-box, .captcha-slider') || specificEl) : null;
            var el = container || document.querySelector('.recaptcha-challenge, .g-recaptcha, .cf-turnstile, .captcha-box, .captcha-slider, #grid') || specificEl;

            var promptEl = (el ? el.querySelector('.rc-imageselect-desc-no-canonical, .rc-imageselect-instructions, .prompt-text, .challenge-instructions, .captcha-prompt, label[for*="captcha"], h3, strong') : null);
            if (!promptEl && !el) {{
                promptEl = document.querySelector('.rc-imageselect-desc-no-canonical, .rc-imageselect-instructions, .prompt-text, .challenge-instructions, .captcha-prompt, label[for*="captcha"], h3, strong');
            }}
            var prompt = promptEl ? (promptEl.innerText || promptEl.textContent || '').trim() : '';

            function computeFingerprint(node) {{
                if (!node) return '';
                var parts = [];
                try {{
                    var canvases = node.querySelectorAll('canvas');
                    for (var i = 0; i < canvases.length; i++) {{
                        parts.push('c:' + canvases[i].toDataURL());
                    }}
                }} catch(e) {{}}
                try {{
                    var imgs = node.querySelectorAll('img');
                    for (var i = 0; i < imgs.length; i++) {{
                        parts.push('i:' + (imgs[i].src || '') + ':' + imgs[i].naturalWidth + 'x' + imgs[i].naturalHeight);
                    }}
                }} catch(e) {{}}
                var items = node.querySelectorAll('.rc-image-tile-target, .captcha-tile, .grid-tile, [class*="tile"], [role="button"], button, canvas, img');
                if (items.length === 0) items = node.children;
                for (var i = 0; i < items.length; i++) {{
                    var it = items[i];
                    var style = it.getAttribute('style') || '';
                    var cls = it.className || '';
                    var txt = (it.innerText || it.textContent || '').trim();
                    var bg = '';
                    try {{
                        bg = window.getComputedStyle(it).backgroundColor || '';
                    }} catch(e) {{}}
                    parts.push('it:' + (it.id || '') + '|' + cls + '|' + style + '|' + bg + '|' + txt);
                }}
                var str = parts.join(';');
                var hash = 5381;
                for (var j = 0; j < str.length; j++) {{
                    hash = ((hash << 5) + hash) + str.charCodeAt(j);
                    hash |= 0;
                }}
                return String(hash);
            }}

            return {{
                doc_id: window.__tabpilot_doc_id || null,
                widget_found: Boolean(el),
                prompt: prompt,
                fingerprint: computeFingerprint(el)
            }};
        }})()"""
        rem_s = solve_session.remaining_s
        precheck_timeout = min(3.0, max(0.001, rem_s))
        try:
            doc_state = self._eval_in_target(
                session, tab, solve_session.candidate.frame_ref, doc_check_expr, timeout_s=precheck_timeout
            )
            if not isinstance(doc_state, dict):
                return SolveResult(
                    status=SolveStatus.STALE_OBSERVATION.value,
                    solve_id=solve_session.solve_id,
                    candidate_id=solve_session.candidate.candidate_id,
                    solver=self.name,
                    detail="Failed to query document state. Observation considered stale.",
                )
            if not doc_state.get("widget_found"):
                return SolveResult(
                    status=SolveStatus.STALE_OBSERVATION.value,
                    solve_id=solve_session.solve_id,
                    candidate_id=solve_session.candidate.candidate_id,
                    solver=self.name,
                    detail="Challenge widget is no longer present in DOM. New observation required.",
                )
            current_doc_id = doc_state.get("doc_id")
            if solve_session.document_generation and current_doc_id != solve_session.document_generation:
                return SolveResult(
                    status=SolveStatus.STALE_OBSERVATION.value,
                    solve_id=solve_session.solve_id,
                    candidate_id=solve_session.candidate.candidate_id,
                    solver=self.name,
                    detail="Document navigated to a new page or lifecycle identity changed. Previous observation is stale.",
                )
            current_prompt = doc_state.get("prompt", "")
            if obs.prompt and current_prompt and current_prompt != obs.prompt:
                return SolveResult(
                    status=SolveStatus.STALE_OBSERVATION.value,
                    solve_id=solve_session.solve_id,
                    candidate_id=solve_session.candidate.candidate_id,
                    solver=self.name,
                    detail="Challenge prompt changed since observation. New observation required.",
                )
            current_fp = doc_state.get("fingerprint", "")
            if obs.challenge_fingerprint and current_fp and current_fp != obs.challenge_fingerprint:
                return SolveResult(
                    status=SolveStatus.STALE_OBSERVATION.value,
                    solve_id=solve_session.solve_id,
                    candidate_id=solve_session.candidate.candidate_id,
                    solver=self.name,
                    detail="Challenge visual content or tile layout changed since observation. New observation required.",
                )
        except Exception as exc:
            return SolveResult(
                status=SolveStatus.STALE_OBSERVATION.value,
                solve_id=solve_session.solve_id,
                candidate_id=solve_session.candidate.candidate_id,
                solver=self.name,
                detail=f"Document check failed ({exc}). Fail-closed: observation considered stale.",
            )

        # Budget check immediately before dispatching any browser input
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
                detail=f"Budget limit reached before dispatching input: {budget_status.value}",
            )

        off_x, off_y = self._get_frame_offset(session, tab, solve_session.candidate.frame_ref)

        # --- Dispatch action ---
        if kind == ActionKind.SELECT_TILE.value:
            target_id = action.target_id
            if obs.tiles:
                allowed_tile_ids = {t.get("tile_id") for t in obs.tiles if t.get("tile_id")}
                if target_id not in allowed_tile_ids:
                    raise ValueError(f"Tile target_id {target_id!r} is not in observation tiles {list(allowed_tile_ids)}")
            expr = f"""(function() {{
                var wRef = {json.dumps(solve_session.candidate.widget_ref)};
                var specificEl = wRef ? document.querySelector(wRef) : null;
                var container = specificEl ? (specificEl.closest('.challenge-section, form, .captcha-container, .recaptcha-challenge, .captcha-box, .captcha-slider') || specificEl) : null;
                var el = container || document.querySelector('.recaptcha-challenge, .g-recaptcha, .cf-turnstile, .captcha-box, .captcha-slider, #grid') || specificEl;
                if (!el) return null;

                var tid = {json.dumps(target_id or '')};
                var t = null;
                if (tid) {{
                    try {{
                        t = el.querySelector('#' + CSS.escape(tid));
                    }} catch(e) {{}}
                    if (!t) {{
                        var allWithId = el.querySelectorAll('[id]');
                        for (var i = 0; i < allWithId.length; i++) {{
                            if (allWithId[i].id === tid) {{
                                t = allWithId[i];
                                break;
                            }}
                        }}
                    }}
                }}
                if (!t && tid.startsWith('tile-')) {{
                    var idx = parseInt(tid.split('-')[1]);
                    var all = el.querySelectorAll('.rc-image-tile-target, .captcha-tile, .grid-tile');
                    if (all[idx]) t = all[idx];
                }}
                if (t) {{
                    t.scrollIntoView({{ block: 'center', behavior: 'instant' }});
                    var r = t.getBoundingClientRect();
                    return {{ x: Math.round(r.left + r.width / 2), y: Math.round(r.top + r.height / 2) }};
                }}
                return null;
            }})()"""
            pos = self._eval_in_target(
                session, tab, solve_session.candidate.frame_ref, expr, timeout_s=min(3.0, max(0.001, solve_session.remaining_s))
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
                    detail=f"Budget limit reached before dispatching input: {budget_status.value}",
                )
            if isinstance(pos, dict) and "x" in pos:
                session.backend.click_at(
                    tab.id,
                    pos["x"] + off_x,
                    pos["y"] + off_y,
                    timeout_s=min(5.0, max(0.001, solve_session.remaining_s)),
                )
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
                session.backend.click_at(
                    tab.id,
                    click_x,
                    click_y,
                    timeout_s=min(5.0, max(0.001, solve_session.remaining_s)),
                )
            sleep_s = min(0.3, max(0.0, solve_session.remaining_s))
            if sleep_s > 0:
                time.sleep(sleep_s)

        elif kind == ActionKind.CLICK_POINT.value:
            if not action.point:
                raise ValueError("click_point requires 'point': {'x': ..., 'y': ...}")
            px = float(action.point["x"])
            py = float(action.point["y"])
            if not (0.0 <= px <= 1.0 and 0.0 <= py <= 1.0):
                raise ValueError(f"point coordinates must be normalized in [0, 1], got ({px}, {py})")

            # Resolve live viewport position using the same container hierarchy as layout inspection
            live_expr = f"""(function() {{
                var wRef = {json.dumps(solve_session.candidate.widget_ref)};
                var specificEl = wRef ? document.querySelector(wRef) : null;
                var container = specificEl ? (specificEl.closest('.challenge-section, form, .captcha-container, .recaptcha-challenge, .captcha-box, .captcha-slider') || specificEl) : null;
                var bframe = document.querySelector('iframe[src*="bframe"], iframe[title*="challenge"], .recaptcha-challenge, .h-captcha-challenge, .captcha-modal, .captcha-container');
                var el = container || bframe || document.querySelector('.captcha-box, #captcha, [class*="captcha"]') || specificEl;
                if (el) {{
                    var r = el.getBoundingClientRect();
                    return {{ x: Math.round(r.left), y: Math.round(r.top), width: Math.round(r.width), height: Math.round(r.height) }};
                }}
                return null;
            }})()"""
            live_rect = self._eval_in_target(
                session, tab, solve_session.candidate.frame_ref, live_expr, timeout_s=min(3.0, max(0.001, solve_session.remaining_s))
            )
            if isinstance(live_rect, dict) and "x" in live_rect:
                vp_x = float(live_rect["x"]) + off_x
                vp_y = float(live_rect["y"]) + off_y
                vp_w = float(live_rect["width"])
                vp_h = float(live_rect["height"])
            else:
                vp_x, vp_y, vp_w, vp_h = crop_x, crop_y, crop_w, crop_h

            click_x = vp_x + px * vp_w
            click_y = vp_y + py * vp_h
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
                    detail=f"Budget limit reached before dispatching input: {budget_status.value}",
                )
            session.backend.click_at(
                tab.id,
                click_x,
                click_y,
                timeout_s=min(5.0, max(0.001, solve_session.remaining_s)),
            )
            sleep_s = min(0.3, max(0.0, solve_session.remaining_s))
            if sleep_s > 0:
                time.sleep(sleep_s)

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
                if (!input && container) input = container.querySelector('.captcha-box input, #captcha-input, #answer, input[type="text"]');
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
                    detail=f"Budget limit reached before dispatching input: {budget_status.value}",
                )
            found = self._eval_in_target(
                session, tab, solve_session.candidate.frame_ref, expr, timeout_s=min(3.0, max(0.001, solve_session.remaining_s))
            )
            if not found:
                return SolveResult(
                    status=SolveStatus.UNSUPPORTED.value,
                    solve_id=solve_session.solve_id,
                    candidate_id=solve_session.candidate.candidate_id,
                    solver=self.name,
                    detail="Could not find input element belonging to this CAPTCHA challenge.",
                )
            sleep_s = min(0.3, max(0.0, solve_session.remaining_s))
            if sleep_s > 0:
                time.sleep(sleep_s)

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
                var specificEl = wRef ? document.querySelector(wRef) : null;
                var container = specificEl ? (specificEl.closest('.challenge-section, form, .captcha-container, .recaptcha-challenge, .captcha-box, .captcha-slider, .puzzle-slider') || specificEl) : null;
                var el = container || document.querySelector('.captcha-slider, .puzzle-slider, #slider-track, #drag-handle') || specificEl;
                if (el) {{
                    el.scrollIntoView({{ block: 'center', behavior: 'instant' }});
                    var r = el.getBoundingClientRect();
                    return {{ x: Math.round(r.left), y: Math.round(r.top), width: Math.round(r.width), height: Math.round(r.height) }};
                }}
                return null;
            }})()"""
            fresh_crop = self._eval_in_target(
                session, tab, solve_session.candidate.frame_ref, expr, timeout_s=min(3.0, max(0.001, solve_session.remaining_s))
            )
            if isinstance(fresh_crop, dict) and "x" in fresh_crop:
                crop_x = float(fresh_crop["x"]) + off_x
                crop_y = float(fresh_crop["y"]) + off_y
                crop_w = float(fresh_crop["width"])
                crop_h = float(fresh_crop["height"])

            from_x = crop_x + fx * crop_w
            from_y = crop_y + fy * crop_h
            to_x = crop_x + tx * crop_w
            to_y = crop_y + ty * crop_h
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
                    detail=f"Budget limit reached before dispatching input: {budget_status.value}",
                )
            session.backend.drag(
                tab.id,
                from_x,
                from_y,
                to_x,
                to_y,
                steps=20,
                duration_s=min(0.5, max(0.05, solve_session.remaining_s)),
                timeout_s=min(10.0, max(0.001, solve_session.remaining_s)),
            )
            sleep_s = min(0.5, max(0.0, solve_session.remaining_s))
            if sleep_s > 0:
                time.sleep(sleep_s)

        elif kind == ActionKind.VERIFY.value:
            expr = f"""(function() {{
                var wRef = {json.dumps(solve_session.candidate.widget_ref)};
                var root = wRef ? document.querySelector(wRef) : null;
                var container = root ? (root.closest('.challenge-section, .captcha-container, .recaptcha-challenge, .captcha-box') || root.parentElement || root) : null;
                // Specifically look for challenge verify buttons strictly inside widget container, NEVER document-wide
                var btn = container ? container.querySelector('#recaptcha-verify-button, #captcha-verify-btn, button[id*="verify"], input[id*="verify"], .verify-btn, .btn-verify, button[aria-label*="Verify"], button[aria-label*="Xác minh"]') : null;
                if (btn) {{
                    btn.scrollIntoView({{ block: 'center', behavior: 'instant' }});
                    var r = btn.getBoundingClientRect();
                    return {{ x: Math.round(r.left + r.width / 2), y: Math.round(r.top + r.height / 2) }};
                }}
                return null;
            }})()"""
            pos = self._eval_in_target(
                session, tab, solve_session.candidate.frame_ref, expr, timeout_s=min(3.0, max(0.001, solve_session.remaining_s))
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
                    detail=f"Budget limit reached before dispatching input: {budget_status.value}",
                )
            if isinstance(pos, dict) and "x" in pos:
                session.backend.click_at(
                    tab.id,
                    pos["x"] + off_x,
                    pos["y"] + off_y,
                    timeout_s=min(5.0, max(0.001, solve_session.remaining_s)),
                )
            else:
                ctrl = next((c for c in obs.controls if c.get("kind") == "verify"), None)
                if ctrl and "rect" in ctrl:
                    cr = ctrl["rect"]
                    click_x = crop_x + cr["x"] + cr["width"] / 2.0
                    click_y = crop_y + cr["y"] + cr["height"] / 2.0
                    session.backend.click_at(
                        tab.id,
                        click_x,
                        click_y,
                        timeout_s=min(5.0, max(0.001, solve_session.remaining_s)),
                    )
                else:
                    return SolveResult(
                        status=SolveStatus.UNSUPPORTED.value,
                        solve_id=solve_session.solve_id,
                        candidate_id=solve_session.candidate.candidate_id,
                        solver=self.name,
                        detail="Could not locate Verify control for this challenge widget.",
                    )
            solve_session.attempts += 1
            sleep_s = min(1.0, max(0.0, solve_session.remaining_s))
            if sleep_s > 0:
                time.sleep(sleep_s)

        elif kind == ActionKind.REFRESH.value:
            expr = f"""(function() {{
                var wRef = {json.dumps(solve_session.candidate.widget_ref)};
                var specificEl = wRef ? document.querySelector(wRef) : null;
                var container = specificEl ? (specificEl.closest('.challenge-section, form, .captcha-container, .recaptcha-challenge, .captcha-box, .captcha-slider') || specificEl) : null;
                var el = container || document.querySelector('.recaptcha-challenge, .g-recaptcha, .cf-turnstile, .captcha-box, .captcha-slider, #grid') || specificEl;
                if (!el) return null;

                var btn = el.querySelector('#recaptcha-reload-button, .reload-btn, .refresh-btn, button[aria-label*="reload" i], button[aria-label*="refresh" i]');
                if (btn) {{
                    btn.scrollIntoView({{ block: 'center', behavior: 'instant' }});
                    var r = btn.getBoundingClientRect();
                    return {{ x: Math.round(r.left + r.width / 2), y: Math.round(r.top + r.height / 2) }};
                }}
                return null;
            }})()"""
            pos = self._eval_in_target(
                session, tab, solve_session.candidate.frame_ref, expr, timeout_s=min(3.0, max(0.001, solve_session.remaining_s))
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
                    detail=f"Budget limit reached before dispatching input: {budget_status.value}",
                )
            if isinstance(pos, dict) and "x" in pos:
                session.backend.click_at(
                    tab.id,
                    pos["x"] + off_x,
                    pos["y"] + off_y,
                    timeout_s=min(5.0, max(0.001, solve_session.remaining_s)),
                )
            else:
                ctrl = next((c for c in obs.controls if c.get("kind") in ("refresh", "reload")), None)
                if ctrl and "rect" in ctrl:
                    cr = ctrl["rect"]
                    click_x = crop_x + cr["x"] + cr["width"] / 2.0
                    click_y = crop_y + cr["y"] + cr["height"] / 2.0
                    session.backend.click_at(
                        tab.id,
                        click_x,
                        click_y,
                        timeout_s=min(5.0, max(0.001, solve_session.remaining_s)),
                    )
                else:
                    return SolveResult(
                        status=SolveStatus.UNSUPPORTED.value,
                        solve_id=solve_session.solve_id,
                        candidate_id=solve_session.candidate.candidate_id,
                        solver=self.name,
                        detail="Could not locate Reload/Refresh control for this challenge widget.",
                    )
            sleep_s = min(1.0, max(0.0, solve_session.remaining_s))
            if sleep_s > 0:
                time.sleep(sleep_s)

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

    def _get_frame_offset(
        self, session: Session, tab: TabInfo, frame_ref: Any
    ) -> tuple[float, float]:
        """Get the (x, y) offset of an iframe element within the top-level document."""
        if not frame_ref:
            return 0.0, 0.0
        url = frame_ref.get("url", "") if isinstance(frame_ref, dict) else getattr(frame_ref, "url", "")
        name = frame_ref.get("name", "") if isinstance(frame_ref, dict) else getattr(frame_ref, "name", "")
        expr = f"""(function() {{
            var url = {json.dumps(url)};
            var name = {json.dumps(name)};
            var iframes = document.querySelectorAll('iframe');
            for (var i = 0; i < iframes.length; i++) {{
                var ifr = iframes[i];
                var match = false;
                if (name && (ifr.name === name || ifr.id === name)) match = true;
                if (!match && url) {{
                    try {{
                        var u = new URL(url);
                        if (ifr.src && (ifr.src === url || ifr.src.indexOf(u.pathname) !== -1)) match = true;
                    }} catch(e) {{
                        if (ifr.src && ifr.src.indexOf(url) !== -1) match = true;
                    }}
                }}
                if (match) {{
                    var r = ifr.getBoundingClientRect();
                    var sx = window.scrollX || window.pageXOffset || 0;
                    var sy = window.scrollY || window.pageYOffset || 0;
                    return {{ x: Math.round(r.left + sx), y: Math.round(r.top + sy) }};
                }}
            }}
            if (iframes.length === 1) {{
                var r = iframes[0].getBoundingClientRect();
                var sx = window.scrollX || window.pageXOffset || 0;
                var sy = window.scrollY || window.pageYOffset || 0;
                return {{ x: Math.round(r.left + sx), y: Math.round(r.top + sy) }};
            }}
            return {{ x: 0, y: 0 }};
        }})()"""
        try:
            res = session.backend.eval_js(tab.id, expr, timeout_s=2.0)
            if isinstance(res, dict) and "x" in res:
                return float(res["x"]), float(res["y"])
        except Exception:
            pass
        return 0.0, 0.0

    def _eval_in_target(
        self,
        session: Session,
        tab: TabInfo,
        frame_ref: Any,
        expr: str,
        timeout_s: float = 3.0,
    ) -> Any:
        if frame_ref:
            return session.backend.evaluate_in_frame(tab.id, frame_ref, expr, timeout_s=timeout_s)
        return session.backend.eval_js(tab.id, expr, timeout_s=timeout_s)

    def _inspect_challenge_layout(
        self, session: Session, tab: TabInfo, solve_session: SolveSession
    ) -> dict[str, Any]:
        """Inspect the challenge element to extract prompt, bounding box, tiles, controls, and content fingerprint."""
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
                var el = container || bframe || document.querySelector('.captcha-box, #captcha, [class*="captcha"]') || specificEl;
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
                ) : null);
                if (!promptEl && !el) {{
                    promptEl = document.querySelector(
                        '.rc-imageselect-desc-no-canonical, .rc-imageselect-instructions, .prompt-text, .challenge-instructions, .captcha-prompt, label[for*="captcha"], h3, strong'
                    );
                }}
                var prompt = promptEl ? (promptEl.innerText || promptEl.textContent || '').trim() : '';

                // Tiles if standard grid - strictly scoped to el
                var tiles = [];
                var tileEls = el ? el.querySelectorAll('.rc-image-tile-target, .captcha-tile, .grid-tile') : [];
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

                // Controls: specifically challenge verify controls only strictly within widget container
                var controls = [];
                var verifyBtn = el ? el.querySelector('#recaptcha-verify-button, #captcha-verify-btn, button[id*="verify"], input[id*="verify"], .btn-verify, .verify-btn, button[aria-label*="Verify"], button[aria-label*="Xác minh"]') : null;
                if (verifyBtn && candidateViewportRect) {{
                    var vr = verifyBtn.getBoundingClientRect();
                    controls.push({{
                        kind: 'verify',
                        rect: {{ x: Math.round(vr.left - candidateViewportRect.x), y: Math.round(vr.top - candidateViewportRect.y), width: Math.round(vr.width), height: Math.round(vr.height) }}
                    }});
                }}
                var refreshBtn = el ? el.querySelector('#recaptcha-reload-button, .reload-btn, .refresh-btn, button[aria-label*="reload" i], button[aria-label*="refresh" i]') : null;
                if (refreshBtn && candidateViewportRect) {{
                    var rr = refreshBtn.getBoundingClientRect();
                    controls.push({{
                        kind: 'refresh',
                        rect: {{ x: Math.round(rr.left - candidateViewportRect.x), y: Math.round(rr.top - candidateViewportRect.y), width: Math.round(rr.width), height: Math.round(rr.height) }}
                    }});
                }}

                // Challenge content fingerprint (canvas, images, tiles, styles)
                function computeFingerprint(node) {{
                    if (!node) return '';
                    var parts = [];
                    try {{
                        var canvases = node.querySelectorAll('canvas');
                        for (var i = 0; i < canvases.length; i++) {{
                            parts.push('c:' + canvases[i].toDataURL());
                        }}
                    }} catch(e) {{}}
                    try {{
                        var imgs = node.querySelectorAll('img');
                        for (var i = 0; i < imgs.length; i++) {{
                            parts.push('i:' + (imgs[i].src || '') + ':' + imgs[i].naturalWidth + 'x' + imgs[i].naturalHeight);
                        }}
                    }} catch(e) {{}}
                    var items = node.querySelectorAll('.rc-image-tile-target, .captcha-tile, .grid-tile, [class*="tile"], [role="button"], button, canvas, img');
                    if (items.length === 0) items = node.children;
                    for (var i = 0; i < items.length; i++) {{
                        var it = items[i];
                        var style = it.getAttribute('style') || '';
                        var cls = it.className || '';
                        var txt = (it.innerText || it.textContent || '').trim();
                        var bg = '';
                        try {{
                            bg = window.getComputedStyle(it).backgroundColor || '';
                        }} catch(e) {{}}
                        parts.push('it:' + (it.id || '') + '|' + cls + '|' + style + '|' + bg + '|' + txt);
                    }}
                    var str = parts.join(';');
                    var hash = 5381;
                    for (var j = 0; j < str.length; j++) {{
                        hash = ((hash << 5) + hash) + str.charCodeAt(j);
                        hash |= 0;
                    }}
                    return String(hash);
                }}

                return {{
                    crop_rect: candidatePageRect,
                    viewport_rect: candidateViewportRect,
                    prompt: prompt,
                    tiles: tiles,
                    controls: controls,
                    dynamic_grid: true,
                    fingerprint: computeFingerprint(el)
                }};
            }})()"""
            timeout = min(3.0, max(0.001, solve_session.remaining_s))
            if solve_session.candidate.frame_ref:
                res = session.backend.evaluate_in_frame(
                    tab.id, solve_session.candidate.frame_ref, expr, timeout_s=timeout
                )
                off_x, off_y = self._get_frame_offset(session, tab, solve_session.candidate.frame_ref)
                if isinstance(res, dict) and res.get("crop_rect"):
                    res["crop_rect"]["x"] += off_x
                    res["crop_rect"]["y"] += off_y
                    if res.get("viewport_rect"):
                        res["viewport_rect"]["x"] += off_x
                        res["viewport_rect"]["y"] += off_y
                    return res
            else:
                res = session.backend.eval_js(tab.id, expr, timeout_s=timeout)
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
            "fingerprint": "",
        }
