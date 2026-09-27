"""Tests for frame discovery, isolated world frame eval, and drag primitives."""

import pytest

from conftest import FakeBackend, FakeSession
from tabpilot.backends.base import Capability, FrameRef


def test_frame_ref_creation():
    ref = FrameRef(
        tab_id="tab-1",
        frame_id="frame-123",
        name="recaptcha-frame",
        url="https://www.google.com/recaptcha/api2/anchor",
        security_origin="https://www.google.com",
    )
    assert ref.tab_id == "tab-1"
    assert ref.frame_id == "frame-123"
    assert ref.name == "recaptcha-frame"


def test_fake_backend_list_frames_and_eval():
    ref = FrameRef(
        tab_id="t1",
        frame_id="f1",
        name="child-frame",
        url="https://example.com/iframe",
    )
    backend = FakeBackend(
        responses={
            "list_frames": [ref],
            "raw": 42,
        }
    )
    session = FakeSession(backend)

    frames = backend.list_frames("t1")
    assert len(frames) == 1
    assert frames[0].frame_id == "f1"

    res = backend.evaluate_in_frame("t1", frames[0], "1 + 41")
    assert res == 42


def test_drag_primitive_records_events():
    backend = FakeBackend()
    backend.drag("t1", from_x=10.0, from_y=20.0, to_x=110.0, to_y=20.0, steps=5)

    calls = [c for c in backend.calls if c[0] == "drag"]
    assert len(calls) == 1
    # Check recorded arguments
    assert calls[0][1] == (10, 20, 110, 20)
