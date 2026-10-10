import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import capability_finder as finder


class AgentRankingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.library = self.home / "ai-agent-library"
        self.library.mkdir()

    def search(self, query, **kwargs):
        return finder.search(query, home=self.home, library=self.library, **kwargs)

    def index(self, capabilities):
        (self.library / "capabilities_index.json").write_text(
            json.dumps({"schema": 1, "active_skill_stores": ["codex", "claude"],
                        "capabilities": capabilities}), encoding="utf-8"
        )

    @staticmethod
    def item(capability_id, name, kind="skill", availability=None, store="archive",
             description="Review security code", category="security"):
        return {"id": capability_id, "type": kind, "name": name,
                "description": description, "category": category,
                "source_path": f"/source/{capability_id}/SKILL.md",
                "variants": [{"store": store, "path": f"/source/{capability_id}"}],
                "availability": availability or ["on-demand"], "aliases": [], "quality": 1}

    def test_requested_agent_selects_matching_same_name_version(self):
        self.index([
            self.item("skill:codex", "security-review", availability=["codex"], store="codex"),
            self.item("skill:claude", "security-review", availability=["claude"], store="claude"),
        ])
        self.assertEqual("skill:codex", self.search("security review", agent="codex")[0]["id"])
        self.assertEqual("skill:claude", self.search("security review", agent="claude")[0]["id"])
        self.assertEqual(1, len(self.search("security review", agent="codex")))

    def test_active_plugin_and_archived_skill_use_agent_availability(self):
        self.index([
            self.item("skill:archive", "security-review", availability=["on-demand"], store="claude"),
            self.item("plugin:codex", "security-review", kind="plugin", availability=["codex"], store="codex"),
        ])
        self.assertEqual("plugin:codex", self.search("security review", agent="codex")[0]["id"])
        self.assertEqual("skill:archive", self.search("security review", agent="claude")[0]["id"])

    def test_project_hint_is_a_small_optional_tie_breaker(self):
        self.index([
            self.item("skill:alpha", "deploy-alpha", description="Deploy an application", category="hosting"),
            self.item("skill:zeta", "deploy-zeta", description="Deploy an application", category="cloudflare"),
        ])
        self.assertEqual("skill:alpha", self.search("deploy")[0]["id"])
        self.assertEqual("skill:zeta", self.search("deploy", project_hint="cloudflare workers")[0]["id"])
        with self.assertRaisesRegex(ValueError, "unknown agent"):
            self.search("deploy", agent="unknown")


if __name__ == "__main__":
    unittest.main()
