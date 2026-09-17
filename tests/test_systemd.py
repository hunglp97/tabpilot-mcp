"""The Ubuntu stack: unit templates and the guards around them.

These run on any OS — they check what gets written, not that systemd accepts it.
"""

from __future__ import annotations

import configparser
import re

import pytest

from tabpilot import systemd
from tabpilot.config import Config
from tabpilot.errors import TabPilotError


def _parse_unit(name: str) -> dict[str, dict[str, str]]:
    """Parse a rendered unit file the way systemd's own INI reader would."""
    parser = configparser.ConfigParser(strict=False, interpolation=None)
    parser.optionxform = str.lower
    parser.read_string(systemd.render_unit(name, env_file="/tmp/stack.env"))
    return {section: dict(parser[section]) for section in parser.sections()}


@pytest.fixture
def config(tmp_path):
    return Config(display=":99", cdp_port=9222, vnc_port=5901,
                  user_data_dir=str(tmp_path / "chrome-profile"))


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(systemd, "find_chrome_binary", lambda: "/usr/bin/google-chrome")
    return tmp_path


class TestUnitTemplates:
    def test_all_four_units_exist(self):
        assert set(systemd.TEMPLATES) == set(systemd.UNITS)

    def test_every_unit_formats_with_an_env_file(self):
        for name in systemd.TEMPLATES:
            rendered = systemd.render_unit(name, env_file="/home/u/.config/tabpilot/stack.env")
            assert "EnvironmentFile=/home/u/.config/tabpilot/stack.env" in rendered, name

    def test_no_template_placeholder_is_left_unfilled(self):
        """An unrendered `{field}` reaches systemd as a literal and the unit is
        rejected at daemon-reload."""
        import re

        for name in systemd.TEMPLATES:
            rendered = systemd.render_unit(name)
            leftover = re.findall(r"\{[a-z_]+\}", rendered)
            assert not leftover, f"{name}: {leftover}"

    def test_the_ordering_chain_is_declared(self):
        """Chrome starting before Xvfb exists is the failure this prevents."""
        assert "After=tabpilot-xvfb.service" in systemd.TEMPLATES["tabpilot-wm"]
        assert "Requires=tabpilot-xvfb.service" in systemd.TEMPLATES["tabpilot-wm"]
        assert "After=tabpilot-wm.service" in systemd.TEMPLATES["tabpilot-chrome"]
        assert "Requires=tabpilot-wm.service" in systemd.TEMPLATES["tabpilot-chrome"]

    def test_every_unit_restarts_itself(self):
        """The shell-script approach leaves a crashed Chrome dead with nobody
        watching; that is the whole reason these are units."""
        for name, template in systemd.TEMPLATES.items():
            assert "Restart=always" in template, name

    def test_chrome_crash_loops_back_off(self):
        template = systemd.TEMPLATES["tabpilot-chrome"]
        assert "StartLimitIntervalSec" in template and "StartLimitBurst" in template

    def test_start_limit_directives_sit_in_the_unit_section(self):
        """systemd ignores StartLimit* under [Service] and only mentions it in the
        journal, so the backoff appears configured while doing nothing."""
        for name in systemd.TEMPLATES:
            sections = _parse_unit(name)
            for key in ("StartLimitIntervalSec", "StartLimitBurst"):
                if any(key.lower() in section for section in sections.values()):
                    assert key.lower() in sections["Unit"], f"{name}: {key} is not under [Unit]"

    def test_every_unit_is_well_formed_ini(self):
        """A malformed unit is rejected at daemon-reload with a terse message."""
        for name in systemd.TEMPLATES:
            sections = _parse_unit(name)
            assert "Unit" in sections, name
            assert "Service" in sections, name
            assert "execstart" in sections["Service"], name

    def test_the_wm_waits_for_the_display_instead_of_sleeping(self):
        """A fixed sleep is a race that fails exactly when the machine is busy."""
        assert "xdpyinfo" in systemd.TEMPLATES["tabpilot-wm"]

    def test_no_stray_template_specifier(self):
        """%i only means something in an instantiated unit; here it would expand
        to an empty string and silently break DISPLAY."""
        for name, template in systemd.TEMPLATES.items():
            assert "%i" not in template, name


class TestChromeFlags:
    FLAGS = systemd.TEMPLATES["tabpilot-chrome"]

    def test_cdp_is_pinned_to_loopback(self):
        """Binding to 0.0.0.0 hands full control of a logged-in browser to anyone
        who can route to the host. The DevTools port has no authentication."""
        assert "--remote-debugging-address=127.0.0.1" in self.FLAGS

    def test_dev_shm_is_worked_around(self):
        """Containers ship a 64 MB /dev/shm and Chrome tabs die with OOM."""
        assert "--disable-dev-shm-usage" in self.FLAGS

    def test_the_keyring_is_bypassed(self):
        """Without this Chrome blocks on start waiting for a system keyring that
        no headless server is running."""
        assert "--password-store=basic" in self.FLAGS

    def test_the_profile_directory_is_created_first(self):
        assert "ExecStartPre" in self.FLAGS and "mkdir -p" in self.FLAGS


class TestVncDefaults:
    def test_vnc_is_loopback_only(self):
        assert "-localhost" in systemd.TEMPLATES["tabpilot-vnc"]

    def test_the_auth_flag_is_injected_not_hardcoded(self):
        assert "$TABPILOT_VNC_AUTH" in systemd.TEMPLATES["tabpilot-vnc"]

    def test_a_missing_password_file_blocks_the_install(self, config, fake_home):
        """Defaulting to -nopw would leave an unauthenticated remote desktop onto
        a browser holding live logins."""
        with pytest.raises(TabPilotError) as caught:
            systemd.render_env(config)
        assert "storepasswd" in caught.value.remedy
        assert "--vnc-insecure" in caught.value.remedy

    def test_insecure_is_possible_but_must_be_asked_for(self, config, fake_home):
        env = systemd.render_env(config, vnc_insecure=True)
        assert "TABPILOT_VNC_AUTH=-nopw" in env

    def test_a_password_file_is_used_when_present(self, config, fake_home):
        passwd = fake_home / ".vnc" / "passwd"
        passwd.parent.mkdir(parents=True)
        passwd.write_bytes(b"secret")
        assert f"TABPILOT_VNC_AUTH=-rfbauth {passwd}" in systemd.render_env(config)


class TestRenderEnv:
    def test_it_carries_every_variable_the_units_reference(self, config, fake_home):
        """A unit referencing an unset variable expands it to an empty string,
        so Chrome starts with no profile path and no debugging port, silently."""
        env = systemd.render_env(config, vnc_insecure=True)
        referenced = set()
        for template in systemd.TEMPLATES.values():
            referenced.update(re.findall(r"\$\{?(TABPILOT_[A-Z_]+)", template))
        assert referenced, "the units reference no variables at all -- check the templates"
        for name in sorted(referenced):
            assert f"{name}=" in env, f"{name} is referenced by a unit but never set"

    def test_window_size_is_derived_from_the_screen_geometry(self, config, fake_home):
        env = systemd.render_env(config, vnc_insecure=True, screen="1440x900x24")
        assert "TABPILOT_WINDOW_SIZE=1440x900" in env

    def test_a_missing_chrome_is_reported_with_an_install_command(self, config, monkeypatch, tmp_path):
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setattr(systemd, "find_chrome_binary", lambda: None)
        with pytest.raises(TabPilotError) as caught:
            systemd.render_env(config, vnc_insecure=True)
        assert "apt install" in caught.value.remedy


class TestLogs:
    def test_a_bare_name_is_prefixed(self, monkeypatch):
        captured = {}
        monkeypatch.setattr(systemd.shutil, "which", lambda _: "/usr/bin/journalctl")
        monkeypatch.setattr(systemd.subprocess, "call", lambda cmd: captured.setdefault("cmd", cmd) or 0)
        systemd.logs("chrome", lines=10)
        assert "tabpilot-chrome" in captured["cmd"]

    def test_an_unknown_unit_is_refused_with_the_valid_names(self):
        with pytest.raises(TabPilotError, match="tabpilot-chrome"):
            systemd.logs("nginx")

    def test_follow_and_no_pager_are_mutually_exclusive(self, monkeypatch):
        captured = {}
        monkeypatch.setattr(systemd.shutil, "which", lambda _: "/usr/bin/journalctl")
        monkeypatch.setattr(systemd.subprocess, "call", lambda cmd: captured.setdefault("cmd", cmd) or 0)
        systemd.logs("chrome", follow=True)
        assert "-f" in captured["cmd"] and "--no-pager" not in captured["cmd"]


def test_up_refuses_before_install(config, monkeypatch, tmp_path):
    monkeypatch.setattr(systemd.shutil, "which", lambda _: "/usr/bin/systemctl")
    monkeypatch.setattr(systemd, "UNIT_DIR", tmp_path / "units")
    with pytest.raises(TabPilotError) as caught:
        systemd.up(config)
    assert "install-stack" in caught.value.remedy


def test_everything_refuses_cleanly_without_systemd(config, monkeypatch):
    monkeypatch.setattr(systemd.shutil, "which", lambda _: None)
    with pytest.raises(TabPilotError, match="systemctl is not available"):
        systemd.up(config)


class TestMemoryHigh:
    """Chrome usually shares its host. MemoryHigh throttles rather than killing,
    so a runaway browser slows down instead of dying mid-workflow."""

    def test_no_limit_renders_as_systemds_own_spelling(self, config, fake_home, monkeypatch, tmp_path):
        monkeypatch.setattr(systemd, "UNIT_DIR", tmp_path / "units")
        monkeypatch.setattr(systemd, "ENV_FILE", tmp_path / "stack.env")
        monkeypatch.setattr(systemd, "ENV_DIR", tmp_path)
        monkeypatch.setattr(systemd, "_systemctl", lambda *a, **k: None)
        monkeypatch.setattr(systemd.shutil, "which", lambda name: None if name == "loginctl" else "/bin/true")
        systemd.install(config, vnc_insecure=True, enable=False)
        unit = (tmp_path / "units" / "tabpilot-chrome.service").read_text()
        assert "MemoryHigh=infinity" in unit

    def test_a_limit_is_applied_to_chrome_only(self, config, fake_home, monkeypatch, tmp_path):
        monkeypatch.setattr(systemd, "UNIT_DIR", tmp_path / "units")
        monkeypatch.setattr(systemd, "ENV_FILE", tmp_path / "stack.env")
        monkeypatch.setattr(systemd, "ENV_DIR", tmp_path)
        monkeypatch.setattr(systemd, "_systemctl", lambda *a, **k: None)
        monkeypatch.setattr(systemd.shutil, "which", lambda name: None if name == "loginctl" else "/bin/true")
        systemd.install(config, vnc_insecure=True, enable=False, memory_high="4G")
        units = tmp_path / "units"
        assert "MemoryHigh=4G" in (units / "tabpilot-chrome.service").read_text()
        for other in ("tabpilot-xvfb", "tabpilot-wm", "tabpilot-vnc"):
            assert "MemoryHigh" not in (units / f"{other}.service").read_text()
