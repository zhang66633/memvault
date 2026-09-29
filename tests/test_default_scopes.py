"""Sprint 10: MemoryEngine implicit default scopes (no args -> resolver)."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from memvault.scopes import project_slug


class TestDefaultScopes:
    def test_whoami(self, engine_defaults, tmp_path):
        who = engine_defaults.whoami()
        assert who["user_id"] == "tester"
        assert who["run_id"] is None
        assert who["agent_id"] == project_slug(str(tmp_path / "myproj"))

    def test_add_without_scope_uses_defaults(self, engine_defaults):
        out = engine_defaults.add([{"role": "user", "content": "我叫默认用户"}])
        mem = out["results"][0]
        assert mem["user_id"] == "tester"
        assert mem["agent_id"] == engine_defaults.whoami()["agent_id"]
        assert mem["run_id"] is None

    def test_get_all_and_search_without_scope_hit_defaults(self, engine_defaults):
        engine_defaults.add([{"role": "user", "content": "我喜欢爬山"}])
        assert len(engine_defaults.get_all()["results"]) == 1
        hits = engine_defaults.search("爬山")["results"]
        assert hits and "爬山" in hits[0]["memory"]

    def test_explicit_scope_is_isolated_from_defaults(self, engine_defaults):
        engine_defaults.add([{"role": "user", "content": "我叫显式人"}], user_id="other")
        # default scope sees its own only
        default_all = engine_defaults.get_all()["results"]
        assert all(m["user_id"] == "tester" for m in default_all)
        # explicit scope sees the other only
        other = engine_defaults.get_all(user_id="other")["results"]
        assert len(other) == 1 and other[0]["user_id"] == "other"
        assert engine_defaults.search("显式人", user_id="other")["results"]

    def test_delete_all_without_scope_clears_defaults_only(self, engine_defaults):
        engine_defaults.add([{"role": "user", "content": "我叫默认人"}])
        engine_defaults.add([{"role": "user", "content": "我叫显式人"}], user_id="other")
        engine_defaults.delete_all()
        assert engine_defaults.get_all()["results"] == []
        other = engine_defaults.get_all(user_id="other")["results"]
        assert len(other) == 1

    def test_explicit_run_id_persists(self, engine_defaults):
        out = engine_defaults.add(
            [{"role": "user", "content": "我喜欢跑步"}], run_id="run-7"
        )
        assert out["results"][0]["run_id"] == "run-7"
