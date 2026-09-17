"""Config resolution: env vars, CLI overrides, and the inline-image decision."""

from __future__ import annotations

import argparse
from pathlib import Path

from tabpilot.config import Config, add_common_args


def test_defaults_are_loopback_and_budgeted():
    config = Config()
    assert config.cdp_base_url == "http://127.0.0.1:9222"
    assert config.max_chars == 20_000
    assert config.timeout_s == 20.0


def test_env_overrides(monkeypatch):
    monkeypatch.setenv("TABPILOT_CDP_PORT", "9333")
    monkeypatch.setenv("TABPILOT_MAX_CHARS", "500")
    monkeypatch.setenv("TABPILOT_BACKEND", "CDP")
    config = Config.from_env()
    assert config.cdp_port == 9333
    assert config.max_chars == 500
    assert config.backend == "cdp"  # normalised to lower case


def test_unparseable_env_falls_back_rather_than_crashing(monkeypatch):
    monkeypatch.setenv("TABPILOT_CDP_PORT", "not-a-number")
    assert Config.from_env().cdp_port == 9222


def test_cli_args_win_over_env(monkeypatch):
    monkeypatch.setenv("TABPILOT_CDP_PORT", "9333")
    parser = argparse.ArgumentParser()
    add_common_args(parser)
    args = parser.parse_args(["--cdp-port", "9444", "--max-chars", "10"])
    config = Config.from_env().merge_args(args)
    assert config.cdp_port == 9444
    assert config.max_chars == 10


def test_unset_cli_args_leave_env_alone(monkeypatch):
    monkeypatch.setenv("TABPILOT_CDP_PORT", "9333")
    parser = argparse.ArgumentParser()
    add_common_args(parser)
    config = Config.from_env().merge_args(parser.parse_args([]))
    assert config.cdp_port == 9333


def test_screenshot_dir_expands_user(monkeypatch):
    monkeypatch.setenv("TABPILOT_SCREENSHOT_DIR", "~/shots")
    assert Config.from_env().screenshot_dir == Path.home() / "shots"


class TestInlineImages:
    """A saved path is useless to a client on another machine; bytes are wasteful
    to one on the same machine. The explicit argument always wins."""

    def test_explicit_argument_wins_over_config(self):
        assert Config(return_images="never").wants_inline_image(True) is True
        assert Config(return_images="always").wants_inline_image(False) is False

    def test_always_and_never(self):
        assert Config(return_images="always").wants_inline_image(None) is True
        assert Config(return_images="never").wants_inline_image(None) is False

    def test_auto_inlines_when_remote(self, monkeypatch):
        monkeypatch.delenv("SSH_CONNECTION", raising=False)
        monkeypatch.setenv("TABPILOT_REMOTE", "1")
        assert Config(return_images="auto").wants_inline_image(None) is True

    def test_auto_inlines_over_ssh(self, monkeypatch):
        monkeypatch.delenv("TABPILOT_REMOTE", raising=False)
        monkeypatch.setenv("SSH_CONNECTION", "10.0.0.1 22 10.0.0.2 22")
        assert Config(return_images="auto").wants_inline_image(None) is True

    def test_auto_saves_locally_otherwise(self, monkeypatch):
        monkeypatch.delenv("TABPILOT_REMOTE", raising=False)
        monkeypatch.delenv("SSH_CONNECTION", raising=False)
        assert Config(return_images="auto").wants_inline_image(None) is False

    def test_remote_env_flips_the_default(self, monkeypatch):
        monkeypatch.setenv("TABPILOT_REMOTE", "1")
        monkeypatch.delenv("TABPILOT_RETURN_IMAGES", raising=False)
        assert Config.from_env().return_images == "always"
