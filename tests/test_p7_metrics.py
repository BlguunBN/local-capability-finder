"""P7: metrics snapshot is present, typed, and read-only."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import capability_finder as finder


class MetricsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name) / "home"
        self.library = Path(self.temp.name) / "lib"
        skill = self.home / ".agents" / "skills" / "metric-skill"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text(
            "---\nname: metric-skill\ndescription: Metrics fixture skill\n---\n",
            encoding="utf-8",
        )
        self.library.mkdir(parents=True, exist_ok=True)
        (self.library / "skills_index.json").write_text(
            json.dumps(
                {
                    "skills": [
                        {
                            "name": "metric-skill",
                            "description": "Metrics fixture skill",
                            "category": "t",
                            "path": str(skill),
                            "skill_md": str(skill / "SKILL.md"),
                            "source": "agents",
                            "canonical": True,
                            "roots": ["agents"],
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        (self.library / "mcp_servers.yaml").write_text(
            "mcp_servers: {}\n", encoding="utf-8"
        )
        finder.refresh_index(home=self.home, library=self.library)

    def test_metrics_snapshot_shape(self):
        before = (self.library / "capabilities_index.json").read_bytes()
        metrics = finder.collect_metrics(home=self.home, library=self.library)
        self.assertEqual([], metrics["errors"])
        self.assertGreaterEqual(metrics["catalog"]["capabilities"], 1)
        self.assertEqual(1, metrics["catalog"]["by_type"].get("skill"))
        self.assertGreater(metrics["catalog"]["index_bytes"], 0)
        self.assertEqual(8, metrics["search"]["queries"])
        for key in ("p50_ms", "p95_ms", "max_result_bytes"):
            self.assertIsInstance(metrics["search"][key], (int, float))
        self.assertEqual(8, metrics["mcp"]["tools"])
        self.assertEqual("2025-11-25", metrics["mcp"]["protocol"])
        self.assertIn("estimate", metrics["note"])
        # Read-only: the index file is byte-identical afterwards.
        self.assertEqual(
            before, (self.library / "capabilities_index.json").read_bytes()
        )

    def test_metrics_reports_missing_index(self):
        (self.library / "capabilities_index.json").unlink()
        metrics = finder.collect_metrics(home=self.home, library=self.library)
        self.assertTrue(metrics["errors"])


if __name__ == "__main__":
    unittest.main()
