"""Checks for the per-agent active-skill inventory."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from startup_inventory import snapshot


class StartupInventoryTest(unittest.TestCase):
    def test_counts_only_top_level_active_skills(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            root = home / ".codex" / "skills"
            active = root / "example"
            active.mkdir(parents=True)
            (active / "SKILL.md").write_text(
                "---\nname: example\ndescription: Example capability\n---\n", encoding="utf-8"
            )
            (root / "empty").mkdir()
            (active / "nested").mkdir()
            (active / "nested" / "SKILL.md").write_text("nested", encoding="utf-8")

            result = snapshot(home)

            self.assertEqual(1, result["stores"]["codex"]["active_skills"])
            self.assertEqual(len("example: Example capability"),
                             result["stores"]["codex"]["normalized_list_characters"])
            self.assertIsNone(result["actual_agent_prompt_tokens"])
            self.assertNotIn("agents", result["stores"])


if __name__ == "__main__":
    unittest.main()
