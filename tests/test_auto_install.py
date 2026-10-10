from __future__ import annotations

import json
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from unittest.mock import patch

import auto_install
import auto_hook


class AutoInstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)

    def test_managed_block_is_reversible_and_preserves_other_content(self):
        path = auto_install.target("codex", self.home)
        path.parent.mkdir(parents=True)
        original = b"\xef\xbb\xbf# Existing\r\nKeep this.\r\n"
        path.write_bytes(original)
        auto_install.action("codex", self.home, "enable", "explicit", False, False)
        once = path.read_bytes()
        self.assertTrue(once.startswith(original))
        self.assertIn(b"find-best-skills", once)
        auto_install.action("codex", self.home, "enable", "explicit", False, False)
        self.assertEqual(path.read_bytes(), once)
        auto_install.action("codex", self.home, "disable", "explicit", False, False)
        self.assertEqual(path.read_bytes(), original)
        self.assertGreaterEqual(len(list(path.parent.glob("AGENTS.md.bak-capfind-*"))), 2)

    def test_backup_names_remain_unique_when_clock_timestamp_repeats(self):
        path = self.home / "AGENTS.md"
        path.write_text("preserve", encoding="utf-8")

        class FixedDateTime:
            @staticmethod
            def now():
                class FixedStamp:
                    @staticmethod
                    def strftime(_format):
                        return "20260101-000000-000000"
                return FixedStamp()

        with patch.object(auto_install, "datetime", FixedDateTime):
            first = auto_install._backup(path)
            second = auto_install._backup(path)
        self.assertNotEqual(first, second)
        self.assertEqual(first.read_text(encoding="utf-8"), "preserve")
        self.assertEqual(second.read_text(encoding="utf-8"), "preserve")

    def test_hook_preserves_other_hooks_and_removes_only_ours(self):
        path = auto_install._hook_path("claude", self.home)
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"hooks": {
            "UserPromptSubmit": [{"hooks": [
                {"type": "command", "command": "other"},
                {"type": "command", "command": "stale", "name": "capfind-find-best-skills"},
            ]}],
            "PreToolUse": [{"hooks": [{"type": "command", "command": "lint"}]}],
        }}), encoding="utf-8")
        auto_install.action("claude", self.home, "enable", "smart", True, False)
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(len(data["hooks"]["UserPromptSubmit"]), 2)
        self.assertEqual(data["hooks"]["UserPromptSubmit"][0]["hooks"][0]["command"], "other")
        self.assertEqual(len(data["hooks"]["PreToolUse"]), 1)
        auto_install.action("claude", self.home, "disable", "smart", False, False)
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(len(data["hooks"]["UserPromptSubmit"]), 1)
        self.assertEqual(data["hooks"]["UserPromptSubmit"][0]["hooks"][0]["command"], "other")

    def test_dry_run_does_not_write(self):
        auto_install.action("gemini", self.home, "enable", "explicit", True, True)
        self.assertFalse(auto_install.target("gemini", self.home).exists())
        self.assertFalse(auto_install._hook_path("gemini", self.home).exists())

    def test_gemini_hook_change_refreshes_watched_catalog(self):
        with patch.object(auto_install.Path, "home", return_value=self.home), \
             patch("capability_finder.refresh_index") as refresh, \
             patch("sys.argv", ["auto_install.py", "enable", "--agents", "gemini", "--hooks"]):
            self.assertEqual(auto_install.main(), 0)
        refresh.assert_called_once_with()

    def test_claude_hook_injects_exact_cached_matches(self):
        event = {"prompt": "build a CAD model with the best skills"}
        incoming = io.StringIO(json.dumps(event))
        outgoing = io.StringIO()
        hits = [{"id": "skill:123", "name": "cad"}]
        with patch("sys.argv", ["auto_hook.py", "claude", "--mode", "explicit"]), \
             patch("sys.stdin", incoming), patch("sys.stdout", outgoing), \
             patch("capability_finder.search", return_value=hits) as search:
            self.assertEqual(auto_hook.main(), 0)
        body = json.loads(outgoing.getvalue())
        context = body["hookSpecificOutput"]["additionalContext"]
        self.assertIn("skill:123 cad", context)
        search.assert_called_once_with("build a CAD model", kind="skill", limit=3, agent="claude")

    def test_hook_search_failure_releases_session_marker(self):
        event = {"prompt": "build a CAD model with the best skills", "session_id": "test-session"}
        incoming = io.StringIO(json.dumps(event))
        outgoing = io.StringIO()
        route = {"triggered": True, "query": "build a CAD model"}
        with patch("sys.argv", ["auto_hook.py", "claude"]), \
             patch("sys.stdin", incoming), patch("sys.stdout", outgoing), \
             patch("auto_skill_router.route_once", return_value=route), \
             patch("auto_skill_router.forget_route") as forget, \
             patch("capability_finder.search", side_effect=ValueError("stale")):
            self.assertEqual(auto_hook.main(), 0)
        self.assertEqual(json.loads(outgoing.getvalue()), {})
        forget.assert_called_once_with(event["prompt"], mode="explicit", agent="claude",
                                       session_id="test-session")


if __name__ == "__main__":
    unittest.main()
