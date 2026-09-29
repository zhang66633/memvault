"""Tests for project_slug and ScopeResolver (Sprint 10)."""
from __future__ import annotations

import os
import sys
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from memvault.scopes import ScopeResolver, project_slug


class TestProjectSlug:
    def test_windows_path(self):
        assert project_slug(r"D:\Claude_code\memory") == "claude-code-memory"

    def test_posix_path(self):
        assert project_slug("/home/alice/my-proj") == "my-proj"

    def test_windows_user_folder_is_not_project(self):
        assert project_slug(r"C:\Users\Alice") is None

    def test_posix_home_is_not_project(self):
        assert project_slug("/home/alice") is None

    def test_root_is_none(self):
        assert project_slug("/") is None
        assert project_slug("") is None


class TestScopeResolver:
    def make(self, **kw):
        defaults = dict(
            env_user_id="",
            env_agent_id="",
            env_run_id="",
            use_os_user=True,
            use_project_agent=True,
            os_user="alice",
            cwd="/home/alice/my-proj",
            project_dir=None,
        )
        defaults.update(kw)
        return ScopeResolver(**defaults)

    def test_automatic_defaults(self):
        r = self.make()
        assert r.resolve() == ("alice", "my-proj", None)

    def test_project_dir_overrides_cwd(self):
        r = self.make(project_dir="/work/team-proj", cwd="/home/alice/my-proj")
        assert r.resolve()[1] == "work-team-proj"

    def test_explicit_arguments_win(self):
        r = self.make()
        user, agent, run = r.resolve(user_id="bob", agent_id="agent:x", run_id="run-1")
        assert user == "bob"
        assert agent == "agent:x"
        assert run == "run-1"

    def test_env_overrides_before_automatic(self):
        r = self.make(env_user_id="env-user", env_agent_id="env-agent", env_run_id="r9")
        assert r.resolve() == ("env-user", "env-agent", "r9")

    def test_no_fallback_returns_none(self):
        r = self.make(use_os_user=False, use_project_agent=False)
        assert r.resolve() == (None, None, None)

    def test_run_never_auto(self):
        r = self.make()
        assert r.resolve()[2] is None

    def test_partial_explicit_only_user(self):
        r = self.make()
        user, agent, run = r.resolve(user_id="bob")
        assert user == "bob"
        assert agent == "my-proj"
        assert run is None

    def test_describe_carries_diagnostics(self):
        r = self.make(project_dir="/work/team-proj")
        d = r.describe()
        assert d.agent_id == "work-team-proj"
        assert d.os_user == "alice"
        assert d.cwd == "/home/alice/my-proj"
        assert d.project_dir == "/work/team-proj"
