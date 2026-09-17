"""Doctor's severity judgements.

A doctor that cries wolf is worse than none: every false failure sends someone
debugging something that was never broken.
"""

from __future__ import annotations

from tabpilot import doctor
from tabpilot.config import Config


def _by_name(checks):
    return {check.name: check for check in checks}


class TestDisplayCheck:
    """Each unit sets DISPLAY on its own ExecStart, so an SSH shell without one
    says nothing about the stack's health."""

    def test_an_unset_display_is_fine_while_xvfb_runs(self, monkeypatch):
        monkeypatch.delenv("DISPLAY", raising=False)
        monkeypatch.setattr(doctor, "_process_running", lambda needle: needle.startswith("Xvfb"))
        checks = _by_name(doctor.check_linux_display(Config()))
        assert checks["Xvfb"].status == doctor.OK
        assert checks["DISPLAY"].status == doctor.OK
        assert "units set" in checks["DISPLAY"].detail

    def test_an_unset_display_with_no_xvfb_is_a_warning(self, monkeypatch):
        monkeypatch.delenv("DISPLAY", raising=False)
        monkeypatch.setattr(doctor, "_process_running", lambda _needle: False)
        checks = _by_name(doctor.check_linux_display(Config()))
        assert checks["DISPLAY"].status == doctor.WARN
        assert checks["Xvfb"].status == doctor.WARN

    def test_a_set_display_is_reported_as_is(self, monkeypatch):
        monkeypatch.setenv("DISPLAY", ":42")
        monkeypatch.setattr(doctor, "_process_running", lambda _needle: False)
        assert _by_name(doctor.check_linux_display(Config()))["DISPLAY"].detail == ":42"


class TestVncExposure:
    def test_an_open_unauthenticated_vnc_is_a_failure_not_a_warning(self, monkeypatch):
        """It is a remote desktop onto a browser holding live logins."""
        monkeypatch.setattr(doctor, "_process_running", lambda _needle: True)
        monkeypatch.setattr(doctor, "_run", lambda cmd, timeout=5.0: (0, "1234 x11vnc -nopw -forever"))
        check = _by_name(doctor.check_linux_display(Config()))["vnc exposure"]
        assert check.status == doctor.FAIL
        assert "owns those accounts" in check.fix

    def test_loopback_bound_vnc_passes(self, monkeypatch):
        monkeypatch.setattr(doctor, "_process_running", lambda _needle: True)
        monkeypatch.setattr(
            doctor, "_run",
            lambda cmd, timeout=5.0: (0, "1234 x11vnc -localhost -rfbauth /home/u/.vnc/passwd"),
        )
        assert _by_name(doctor.check_linux_display(Config()))["vnc exposure"].status == doctor.OK

    def test_no_vnc_at_all_is_only_a_warning(self, monkeypatch):
        """VNC is optional; it is needed once, to sign in by hand."""
        monkeypatch.setattr(doctor, "_process_running", lambda needle: needle.startswith("Xvfb"))
        checks = _by_name(doctor.check_linux_display(Config()))
        assert checks["x11vnc"].status == doctor.WARN
        assert "vnc exposure" not in checks
        assert "storepasswd" in checks["x11vnc"].fix
