"""Orchestrator and MCP tools for CAPTCHA detection and solving."""

from __future__ import annotations

import hashlib
import json
import threading
import time
import uuid
from typing import Any

from ._sdk import Image
from .backends.base import Capability, TabInfo
from .captcha_solvers import get_solver_adapter
from .captcha_state import (
    Action,
    ActionKind,
    CandidateState,
    CaptchaCandidate,
    ChallengeKind,
    DetectionCoverage,
    DetectionResult,
    DetectionStatus,
    Provider,
    SolveResult,
    SolveSession,
    SolveStatus,
    VerificationLevel,
)
from .captcha_verify import hash_token
from .config import Config
from .errors import (
    ActionOutcomeUnknownError,
    CaptchaBusyError,
    DisabledError,
    ImageOutputUnavailableError,
    SolveExpiredError,
    StrategyIncompatibleError,
    TabPilotError,
)
from .session import Session


def detect_captcha(
    session: Session,
    tab_id: str | None = None,
    url_pattern: str | None = None,
) -> str:
    """Scan the target tab for CAPTCHA challenges and return a DetectionResult JSON."""
    if not session.config.captcha_enabled:
        raise DisabledError(
            "CAPTCHA handling is disabled.",
            remedy="Enable CAPTCHA handling via TABPILOT_CAPTCHA=1 or omit --no-captcha.",
        )

    tab = session.resolve(tab_id, url_pattern)
    try:
        doc_id_expr = """(function() {
            if (!window.__tabpilot_doc_id) {
                window.__tabpilot_doc_id = 'doc_' + Math.random().toString(36).slice(2) + '_' + Date.now();
            }
            return window.__tabpilot_doc_id;
        })()"""
        doc_generation = str(session.backend.eval_js(tab.id, doc_id_expr, timeout_s=2.0) or "")
    except Exception:
        doc_generation = ""
    if not doc_generation:
        doc_generation = hashlib.sha256(f"{tab.id}:{tab.url}".encode("utf-8")).hexdigest()[:12]

    try:
        raw_res = session.run_payload(tab.id, "captcha_detect")
    except TabPilotError as exc:
        res = DetectionResult(
            status=DetectionStatus.INSPECTION_FAILED.value,
            tab_id=tab.id,
            document_generation=doc_generation,
            coverage=DetectionCoverage.PARTIAL.value,
            limitations=[f"Payload execution failed: {exc}"],
        )
        return res.to_json()

    if isinstance(raw_res, dict) and not raw_res.get("ok", True):
        res = DetectionResult(
            status=DetectionStatus.INSPECTION_FAILED.value,
            tab_id=tab.id,
            document_generation=doc_generation,
            coverage=DetectionCoverage.PARTIAL.value,
            limitations=[f"Detection payload reported failure: {raw_res.get('error', 'unknown error')}"],
        )
        return res.to_json()

    raw_candidates = raw_res.get("candidates", []) if isinstance(raw_res, dict) else []
    candidates: list[CaptchaCandidate] = []
    selected_id: str | None = None

    for item in raw_candidates:
        c = CaptchaCandidate.from_dict(item)
        candidates.append(c)

    if not candidates:
        status = DetectionStatus.ABSENT.value
    else:
        status = DetectionStatus.PRESENT.value
        # Select first blocking candidate or first high-confidence candidate
        for c in candidates:
            if c.blocking or c.confidence == "high":
                selected_id = c.candidate_id
                break
        if not selected_id and candidates:
            selected_id = candidates[0].candidate_id

    result = DetectionResult(
        status=status,
        tab_id=tab.id,
        document_generation=doc_generation,
        coverage=DetectionCoverage.TOP_DOCUMENT.value,
        candidates=candidates,
        selected_candidate_id=selected_id,
        limitations=[],
    )
    return result.to_json()


def solve_captcha(
    session: Session,
    tab_id: str | None = None,
    url_pattern: str | None = None,
    operation: str = "start",
    candidate_id: str | None = None,
    solve_id: str | None = None,
    observation_id: str | None = None,
    action_id: str | None = None,
    action: dict | None = None,
    strategy: str | None = None,
    agent_vision: bool | None = None,
    timeout_ms: int | None = None,
    max_attempts: int | None = None,
    max_rounds: int | None = None,
    expected: dict | None = None,
    activate_on_fail: bool | None = None,
) -> Any:
    """Solve an identified CAPTCHA challenge via start/observe/act/cancel operations."""
    if not session.config.captcha_enabled:
        raise DisabledError(
            "CAPTCHA handling is disabled.",
            remedy="Enable CAPTCHA handling via TABPILOT_CAPTCHA=1 or omit --no-captcha.",
        )

    op = (operation or "start").strip().lower()
    if op not in {"start", "observe", "act", "cancel"}:
        raise ValueError(f"Unknown operation {operation!r}. Must be start, observe, act, or cancel.")

    tab = session.resolve(tab_id, url_pattern)

    # Validate immutable start-only parameters on resume operations
    if op in {"observe", "act", "cancel"}:
        start_only = {
            "strategy": strategy,
            "expected": expected,
            "timeout_ms": timeout_ms,
            "max_attempts": max_attempts,
            "max_rounds": max_rounds,
            "agent_vision": agent_vision,
            "activate_on_fail": activate_on_fail,
        }
        for param_name, param_val in start_only.items():
            if param_val is not None:
                raise ValueError(
                    f"Parameter {param_name!r} is start-only and immutable; cannot be specified on operation {op!r}."
                )

    if op == "start":
        if timeout_ms is not None:
            if not isinstance(timeout_ms, int) or isinstance(timeout_ms, bool) or timeout_ms <= 0 or timeout_ms > 300000:
                raise ValueError(f"timeout_ms must be an integer between 1 and 300000, got {timeout_ms!r}")
        if max_attempts is not None:
            if not isinstance(max_attempts, int) or isinstance(max_attempts, bool) or max_attempts <= 0 or max_attempts > 10:
                raise ValueError(f"max_attempts must be an integer between 1 and 10, got {max_attempts!r}")
        if max_rounds is not None:
            if not isinstance(max_rounds, int) or isinstance(max_rounds, bool) or max_rounds <= 0 or max_rounds > 30:
                raise ValueError(f"max_rounds must be an integer between 1 and 30, got {max_rounds!r}")

        return _handle_start(
            session=session,
            tab=tab,
            candidate_id=candidate_id,
            strategy=strategy,
            agent_vision=agent_vision,
            timeout_ms=timeout_ms,
            max_attempts=max_attempts,
            max_rounds=max_rounds,
            expected=expected,
            activate_on_fail=activate_on_fail,
        )
    elif op == "observe":
        return _handle_observe(
            session=session,
            tab=tab,
            solve_id=solve_id,
            candidate_id=candidate_id,
        )
    elif op == "act":
        return _handle_act(
            session=session,
            tab=tab,
            solve_id=solve_id,
            observation_id=observation_id,
            action_id=action_id,
            action=action,
        )
    else:  # cancel
        return _handle_cancel(
            session=session,
            tab=tab,
            solve_id=solve_id,
        )


def _handle_start(
    session: Session,
    tab: TabInfo,
    candidate_id: str | None,
    strategy: str | None,
    agent_vision: bool | None,
    timeout_ms: int | None,
    max_attempts: int | None,
    max_rounds: int | None,
    expected: dict | None,
    activate_on_fail: bool | None,
) -> Any:
    cfg = session.config
    timeout = timeout_ms if timeout_ms is not None else cfg.captcha_timeout_ms
    attempts_limit = max_attempts if max_attempts is not None else cfg.captcha_max_attempts
    rounds_limit = max_rounds if max_rounds is not None else cfg.captcha_max_rounds
    strat = strategy if strategy is not None else cfg.captcha_strategy
    vision = agent_vision if agent_vision is not None else True
    act_fail = activate_on_fail if activate_on_fail is not None else cfg.captcha_activate_on_fail

    start_monotonic = time.monotonic()
    deadline_monotonic = start_monotonic + (timeout / 1000.0)

    # Find candidate
    det_json = detect_captcha(session, tab_id=tab.id)
    det_data = json.loads(det_json)

    # Check if detection payload failed: retain inspection failure
    if det_data.get("status") == DetectionStatus.INSPECTION_FAILED.value:
        res = SolveResult(
            status=SolveStatus.UNVERIFIED.value,
            solver="none",
            elapsed_ms=int((time.monotonic() - start_monotonic) * 1000),
            remaining_ms=max(0, int((deadline_monotonic - time.monotonic()) * 1000)),
            detail=f"Detection payload execution failed: {'; '.join(det_data.get('limitations', []))}",
            evidence=[{"kind": "inspection_failed", "limitations": det_data.get("limitations", [])}],
        )
        return res.to_json()

    raw_candidates = det_data.get("candidates", [])

    if not raw_candidates:
        res = SolveResult(
            status=SolveStatus.NO_CAPTCHA.value,
            solver="none",
            elapsed_ms=int((time.monotonic() - start_monotonic) * 1000),
            remaining_ms=max(0, int((deadline_monotonic - time.monotonic()) * 1000)),
            detail="No CAPTCHA detected on page",
        )
        return res.to_json()

    chosen: CaptchaCandidate | None = None
    if candidate_id:
        for item in raw_candidates:
            if item.get("candidate_id") == candidate_id:
                chosen = CaptchaCandidate.from_dict(item)
                break
        if not chosen:
            raise ValueError(f"Candidate {candidate_id!r} not found in current document.")
    else:
        # Pick selected or first
        sel_id = det_data.get("selected_candidate_id")
        for item in raw_candidates:
            if item.get("candidate_id") == sel_id:
                chosen = CaptchaCandidate.from_dict(item)
                break
        if not chosen:
            chosen = CaptchaCandidate.from_dict(raw_candidates[0])

    # Record baseline token fingerprint
    baseline_fp: str | None = None
    if chosen.response_field_ref:
        try:
            expr = f"document.querySelector({json.dumps(chosen.response_field_ref)}) ? document.querySelector({json.dumps(chosen.response_field_ref)}).value : null"
            token_val = session.backend.eval_js(tab.id, expr, timeout_s=2.0)
            baseline_fp = hash_token(str(token_val) if token_val else None)
        except Exception:
            pass

    solve_id = f"solve_{uuid.uuid4().hex[:10]}"
    solve_session = SolveSession(
        solve_id=solve_id,
        tab_id=tab.id,
        candidate=chosen,
        document_generation=det_data.get("document_generation", ""),
        deadline_monotonic=deadline_monotonic,
        start_time_monotonic=start_monotonic,
        max_attempts=attempts_limit,
        max_rounds=rounds_limit,
        max_actions=cfg.captcha_max_actions,
        strategy=strat,
        agent_vision=vision,
        expected=expected,
        activate_on_fail=act_fail,
        baseline_token_fingerprint=baseline_fp,
    )

    # Acquire lease
    active_solve = session.acquire_solve(tab.id, solve_session)
    if active_solve is not solve_session:
        # An existing solve session is already active for this candidate.
        # Return its current observation/state without re-running clicks!
        res = SolveResult(
            status=active_solve.status,
            solve_id=active_solve.solve_id,
            candidate_id=active_solve.candidate.candidate_id,
            observation_id=active_solve.last_observation.observation_id if active_solve.last_observation else None,
            provider=active_solve.candidate.provider,
            challenge_kind=active_solve.candidate.challenge_kind,
            solver=active_solve.solver_name,
            attempts=active_solve.attempts,
            rounds=active_solve.rounds,
            actions_used=active_solve.actions_used,
            elapsed_ms=active_solve.elapsed_ms,
            remaining_ms=active_solve.remaining_ms,
            evidence=active_solve.evidence,
            detail="Returning existing active solve session for this candidate.",
            observation=active_solve.last_observation.to_dict() if active_solve.last_observation else None,
        )
        return _finish_step(session, tab, active_solve, res)

    # Select solver and validate requirements
    solver = get_solver_adapter(strat, chosen, agent_vision=vision)
    solve_session.solver_name = solver.name
    for cap in solver.requires:
        session.require(cap)

    # Execute step with solve context
    session._solve_local.is_internal_solve = True
    try:
        result = solver.solve_step(session, tab, solve_session)
    except Exception:
        session.release_solve(tab.id, solve_id)
        raise
    finally:
        session._solve_local.is_internal_solve = False

    return _finish_step(session, tab, solve_session, result)


def _handle_observe(
    session: Session,
    tab: TabInfo,
    solve_id: str | None,
    candidate_id: str | None,
) -> Any:
    if not solve_id:
        raise ValueError("operation='observe' requires solve_id.")

    solve_session: SolveSession | None = session.get_active_solve(tab.id)
    if not solve_session or solve_session.solve_id != solve_id:
        raise SolveExpiredError(
            f"Solve session {solve_id!r} is expired or does not match active solve.",
            remedy="Start a new solve with operation='start'.",
        )

    if candidate_id and solve_session.candidate.candidate_id != candidate_id:
        raise ValueError(f"Candidate mismatch: active is {solve_session.candidate.candidate_id}, got {candidate_id}")

    solver = get_solver_adapter(solve_session.strategy, solve_session.candidate, solve_session.agent_vision)

    session._solve_local.is_internal_solve = True
    try:
        result = solver.solve_step(session, tab, solve_session)
    finally:
        session._solve_local.is_internal_solve = False

    return _finish_step(session, tab, solve_session, result)


def _handle_act(
    session: Session,
    tab: TabInfo,
    solve_id: str | None,
    observation_id: str | None,
    action_id: str | None,
    action: dict | None,
) -> Any:
    if not solve_id:
        raise ValueError("operation='act' requires solve_id.")
    if not observation_id:
        raise ValueError("operation='act' requires observation_id.")
    if not action_id:
        raise ValueError("operation='act' requires action_id.")
    if not action or not isinstance(action, dict):
        raise ValueError("operation='act' requires an action dictionary.")

    solve_session: SolveSession | None = session.get_active_solve(tab.id)
    if not solve_session or solve_session.solve_id != solve_id:
        raise SolveExpiredError(
            f"Solve session {solve_id!r} is expired or does not match active solve.",
            remedy="Start a new solve with operation='start'.",
        )

    # Idempotency check via action_id receipt with atomic lock
    action_payload_str = json.dumps(action, sort_keys=True)
    payload_hash = hashlib.sha256(f"{action_id}:{action_payload_str}".encode("utf-8")).hexdigest()

    is_owner = False
    in_flight_event = None

    with solve_session.lock:
        if action_id in solve_session.action_receipts:
            prev_hash, prev_result = solve_session.action_receipts[action_id]
            if prev_hash == payload_hash:
                return prev_result
            raise ValueError(f"Conflicting payload for already executed action_id {action_id!r}.")

        if action_id in solve_session.in_flight_actions:
            in_flight_event = solve_session.in_flight_actions[action_id]
        else:
            in_flight_event = threading.Event()
            solve_session.in_flight_actions[action_id] = in_flight_event
            is_owner = True

    if not is_owner:
        in_flight_event.wait(timeout=30.0)
        with solve_session.lock:
            if action_id in solve_session.action_receipts:
                prev_hash, prev_result = solve_session.action_receipts[action_id]
                if prev_hash == payload_hash:
                    return prev_result
                raise ValueError(f"Conflicting payload for already executed action_id {action_id!r}.")
            raise ActionOutcomeUnknownError(f"Action {action_id!r} completed with unknown outcome.")

    try:
        # Check for observation staleness
        if not solve_session.last_observation or solve_session.last_observation.observation_id != observation_id:
            solve_session.status = SolveStatus.STALE_OBSERVATION.value
            solver = get_solver_adapter(solve_session.strategy, solve_session.candidate, solve_session.agent_vision)
            session._solve_local.is_internal_solve = True
            try:
                res = solver.solve_step(session, tab, solve_session)
                res.detail = f"Observation {observation_id!r} was stale; new observation generated."
                formatted = _finish_step(session, tab, solve_session, res)
            finally:
                session._solve_local.is_internal_solve = False
        else:
            parsed_action = Action.from_dict(action)
            solver = get_solver_adapter(solve_session.strategy, solve_session.candidate, solve_session.agent_vision)

            session._solve_local.is_internal_solve = True
            try:
                result = solver.handle_action(session, tab, solve_session, parsed_action)
            finally:
                session._solve_local.is_internal_solve = False

            formatted = _finish_step(session, tab, solve_session, result)

        with solve_session.lock:
            solve_session.action_receipts[action_id] = (payload_hash, formatted)
        return formatted

    finally:
        with solve_session.lock:
            solve_session.in_flight_actions.pop(action_id, None)
            in_flight_event.set()


def _handle_cancel(
    session: Session,
    tab: TabInfo,
    solve_id: str | None,
) -> str:
    session.release_solve(tab.id, solve_id)
    res = SolveResult(
        status=SolveStatus.CANCELLED.value,
        terminal=True,
        success=False,
        solve_id=solve_id,
        detail="Solve cancelled by caller.",
    )
    return res.to_json()


def _finish_step(
    session: Session,
    tab: TabInfo,
    solve_session: SolveSession,
    result: SolveResult,
) -> Any:
    # If terminal, release lease and handle activate_on_fail
    if result.terminal:
        session.release_solve(tab.id, solve_session.solve_id)
        if not result.success and solve_session.activate_on_fail:
            if session.backend.supports(Capability.ACTIVATE):
                try:
                    session.backend.activate_tab(tab.id)
                except Exception:
                    pass

    # Check whether to return inline Image: needs_agent MUST include image content
    if result.status == SolveStatus.NEEDS_AGENT.value and solve_session.last_observation_image_bytes:
        if Image is None:
            raise ImageOutputUnavailableError(
                "MCP Image support is unavailable in this environment.",
                remedy="Check MCP SDK installation or set agent_vision=False to use local non-vision strategies.",
            )
        return [
            result.to_json(),
            Image(
                data=solve_session.last_observation_image_bytes,
                format=solve_session.last_observation_image_format,
            ),
        ]

    return result.to_json()
