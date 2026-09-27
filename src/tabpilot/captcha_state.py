"""Data contracts and state representation for CAPTCHA handling."""

from __future__ import annotations

import enum
import json
import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Any


class Provider(str, enum.Enum):
    RECAPTCHA = "recaptcha"
    HCAPTCHA = "hcaptcha"
    CLOUDFLARE = "cloudflare"
    CUSTOM = "custom"
    UNKNOWN = "unknown"


class ChallengeKind(str, enum.Enum):
    CHECKBOX = "checkbox"
    INTERSTITIAL = "interstitial"
    IMAGE_GRID = "image_grid"
    IMAGE_SELECT = "image_select"
    IMAGE_TEXT = "image_text"
    AUDIO = "audio"
    TEXT = "text"
    SLIDER = "slider"
    SCORE_ONLY = "score_only"
    UNKNOWN = "unknown"


class CandidateState(str, enum.Enum):
    LOADING = "loading"
    ACTIONABLE = "actionable"
    PASSED = "passed"
    EXPIRED = "expired"
    BLOCKED = "blocked"
    UNKNOWN = "unknown"


class Confidence(str, enum.Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class DetectionStatus(str, enum.Enum):
    ABSENT = "absent"
    PRESENT = "present"
    UNCERTAIN = "uncertain"
    INSPECTION_FAILED = "inspection_failed"


class DetectionCoverage(str, enum.Enum):
    TOP_DOCUMENT = "top_document"
    FRAMES = "frames"
    PARTIAL = "partial"


class SolveStatus(str, enum.Enum):
    # Non-terminal
    WAITING = "waiting"
    NEEDS_AGENT = "needs_agent"
    STALE_OBSERVATION = "stale_observation"

    # Terminal
    WIDGET_PASSED = "widget_passed"
    ACCESS_VERIFIED = "access_verified"
    NO_CAPTCHA = "no_captcha"
    UNSUPPORTED = "unsupported"
    BLOCKED = "blocked"
    EXHAUSTED = "exhausted"
    TIMEOUT = "timeout"
    UNVERIFIED = "unverified"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


class VerificationLevel(str, enum.Enum):
    NONE = "none"
    WIDGET = "widget"
    ACCESS = "access"


class ActionKind(str, enum.Enum):
    CLICK_CONTROL = "click_control"
    SELECT_TILE = "select_tile"
    CLICK_POINT = "click_point"
    TYPE_ANSWER = "type_answer"
    DRAG = "drag"
    VERIFY = "verify"
    REFRESH = "refresh"


TERMINAL_STATUSES = frozenset({
    SolveStatus.WIDGET_PASSED.value,
    SolveStatus.ACCESS_VERIFIED.value,
    SolveStatus.NO_CAPTCHA.value,
    SolveStatus.UNSUPPORTED.value,
    SolveStatus.BLOCKED.value,
    SolveStatus.EXHAUSTED.value,
    SolveStatus.TIMEOUT.value,
    SolveStatus.UNVERIFIED.value,
    SolveStatus.CANCELLED.value,
    SolveStatus.INTERRUPTED.value,
})

SUCCESS_STATUSES = frozenset({
    SolveStatus.WIDGET_PASSED.value,
    SolveStatus.ACCESS_VERIFIED.value,
})


@dataclass
class CaptchaCandidate:
    candidate_id: str
    provider: str
    challenge_kind: str
    state: str
    confidence: str
    signals: list[str] = field(default_factory=list)
    visible: bool = True
    blocking: bool | None = None
    frame_ref: dict[str, Any] | None = None
    widget_ref: str | None = None
    rect_css: dict[str, float] | None = None
    sitekey: str | None = None
    response_field_ref: str | None = None
    available_strategies: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CaptchaCandidate":
        return cls(
            candidate_id=data["candidate_id"],
            provider=data.get("provider", Provider.UNKNOWN.value),
            challenge_kind=data.get("challenge_kind", ChallengeKind.UNKNOWN.value),
            state=data.get("state", CandidateState.UNKNOWN.value),
            confidence=data.get("confidence", Confidence.LOW.value),
            signals=list(data.get("signals", [])),
            visible=bool(data.get("visible", True)),
            blocking=data.get("blocking"),
            frame_ref=data.get("frame_ref"),
            widget_ref=data.get("widget_ref"),
            rect_css=data.get("rect_css"),
            sitekey=data.get("sitekey"),
            response_field_ref=data.get("response_field_ref"),
            available_strategies=list(data.get("available_strategies", [])),
        )


@dataclass
class DetectionResult:
    schema_version: int = 1
    status: str = DetectionStatus.ABSENT.value
    tab_id: str = ""
    document_generation: str = ""
    coverage: str = DetectionCoverage.TOP_DOCUMENT.value
    candidates: list[CaptchaCandidate] = field(default_factory=list)
    selected_candidate_id: str | None = None
    limitations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "status": self.status,
            "tab_id": self.tab_id,
            "document_generation": self.document_generation,
            "coverage": self.coverage,
            "candidates": [c.to_dict() for c in self.candidates],
            "selected_candidate_id": self.selected_candidate_id,
            "limitations": list(self.limitations),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)


@dataclass
class Observation:
    observation_id: str
    solve_id: str
    image_id: str
    prompt: str
    prompt_source: str = "dom"  # dom | image
    image_width: int = 0
    image_height: int = 0
    crop_rect: dict[str, float] | None = None
    viewport_rect: dict[str, float] | None = None
    allowed_actions: list[dict[str, Any]] = field(default_factory=list)
    tiles: list[dict[str, Any]] = field(default_factory=list)
    controls: list[dict[str, Any]] = field(default_factory=list)
    dynamic_grid: bool = False
    challenge_fingerprint: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SolveResult:
    schema_version: int = 1
    status: str = SolveStatus.WAITING.value
    terminal: bool = False
    success: bool = False
    verification_level: str = VerificationLevel.NONE.value
    solve_id: str | None = None
    candidate_id: str | None = None
    observation_id: str | None = None
    provider: str | None = None
    challenge_kind: str | None = None
    solver: str = "none"
    attempts: int = 0
    rounds: int = 0
    actions_used: int = 0
    elapsed_ms: int = 0
    remaining_ms: int = 0
    evidence: list[dict[str, Any]] = field(default_factory=list)
    detail: str = ""
    next_action: str | None = None
    retry_after_ms: int | None = None
    observation: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        self.terminal = self.status in TERMINAL_STATUSES
        self.success = self.status in SUCCESS_STATUSES

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "status": self.status,
            "terminal": self.terminal,
            "success": self.success,
            "verification_level": self.verification_level,
            "solve_id": self.solve_id,
            "candidate_id": self.candidate_id,
            "observation_id": self.observation_id,
            "provider": self.provider,
            "challenge_kind": self.challenge_kind,
            "solver": self.solver,
            "attempts": self.attempts,
            "rounds": self.rounds,
            "actions_used": self.actions_used,
            "elapsed_ms": self.elapsed_ms,
            "remaining_ms": self.remaining_ms,
            "evidence": list(self.evidence),
            "detail": self.detail,
            "next_action": self.next_action,
            "retry_after_ms": self.retry_after_ms,
            "observation": self.observation,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)


@dataclass
class Action:
    kind: str
    target_id: str | None = None
    point: dict[str, float] | None = None
    text: str | None = None
    drag_to: dict[str, float] | None = None
    image_id: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Action":
        return cls(
            kind=data.get("kind", ""),
            target_id=data.get("target_id"),
            point=data.get("point"),
            text=data.get("text"),
            drag_to=data.get("drag_to"),
            image_id=data.get("image_id"),
        )


@dataclass
class SolveSession:
    solve_id: str
    tab_id: str
    candidate: CaptchaCandidate
    document_generation: str
    deadline_monotonic: float
    start_time_monotonic: float
    max_attempts: int
    max_rounds: int
    max_actions: int
    strategy: str
    agent_vision: bool
    expected: dict[str, Any] | None
    activate_on_fail: bool

    attempts: int = 0
    rounds: int = 0
    actions_used: int = 0
    solver_name: str = "none"
    status: str = SolveStatus.WAITING.value
    status_detail: str = ""
    verification_level: str = VerificationLevel.NONE.value

    lock: threading.Lock = field(default_factory=threading.Lock)
    in_flight_actions: dict[str, threading.Event] = field(default_factory=dict)
    evidence: list[dict[str, Any]] = field(default_factory=list)
    action_receipts: dict[str, tuple[str, Any]] = field(default_factory=dict)
    baseline_token_fingerprint: str | None = None
    last_observation: Observation | None = None
    last_observation_image_bytes: bytes | None = None
    last_observation_image_format: str = "png"

    @property
    def is_expired(self) -> bool:
        return time.monotonic() >= self.deadline_monotonic

    @property
    def remaining_ms(self) -> int:
        rem = self.deadline_monotonic - time.monotonic()
        return max(0, int(rem * 1000))

    @property
    def remaining_s(self) -> float:
        return max(0.0, self.deadline_monotonic - time.monotonic())

    @property
    def elapsed_ms(self) -> int:
        return max(0, int((time.monotonic() - self.start_time_monotonic) * 1000))

    def check_budgets(self) -> SolveStatus | None:
        if self.is_expired:
            return SolveStatus.TIMEOUT
        if self.attempts >= self.max_attempts:
            return SolveStatus.EXHAUSTED
        if self.rounds >= self.max_rounds:
            return SolveStatus.EXHAUSTED
        if self.actions_used >= self.max_actions:
            return SolveStatus.EXHAUSTED
        return None
