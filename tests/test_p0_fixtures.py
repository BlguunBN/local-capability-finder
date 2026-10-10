"""P0 fixture: nested, unicode, symlink, duplicate, broken, malformed skills.

Documents current behavior on adversarial filesystem shapes. Uses temp
dirs only; never touches the real library.
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import capability_finder as finder
import library_catalog


def make_skill(folder: Path, name: str | None, description: str | None) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    fm = ""
    if name is not None or description is not None:
        fm = "---\n"
        if name is not None:
            fm += f"name: {name}\n"
        if description is not None:
            fm += f"description: {description}\n"
        fm += "---\n"
    (folder / "SKILL.md").write_text(fm + "body\n", encoding="utf-8")


class P0FixtureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name) / "home"
        self.library = Path(self.temp.name) / "lib"
        self.home.mkdir()
        self.library.mkdir()
        self.roots = self.home / ".agents" / "skills"
        self.roots.mkdir(parents=True)

        make_skill(
            self.roots / "group" / "nested-skill",
            "nested-skill",
            "Nested skill two levels deep",
        )
        make_skill(
            self.roots / "ünïcode skill",
            "unicode-space-skill",
            "Unicode and space path",
        )
        # Duplicate name, physically distinct folders.
        make_skill(self.roots / "dup-a" / "dup-skill", "dup-skill", "First copy")
        # Malformed: no frontmatter block at all.
        bad = self.roots / "malformed-skill"
        bad.mkdir(parents=True)
        (bad / "SKILL.md").write_text("just prose, no frontmatter\n", encoding="utf-8")
        # Broken symlink must not break the walk.
        self.broken = self.roots / "broken-link" / "SKILL.md"
        self.broken.parent.mkdir(parents=True)
        try:
            self.broken.symlink_to(self.roots / "does-not-exist" / "SKILL.md")
            self.have_broken_link = True
        except (OSError, NotImplementedError):
            self.have_broken_link = False
        # Alias symlink to the nested skill: same real file, second root name.
        self.alias = self.home / ".claude" / "skills" / "nested-alias"
        try:
            self.alias.parent.mkdir(parents=True, exist_ok=True)
            self.alias.symlink_to(
                self.roots / "group" / "nested-skill", target_is_directory=True
            )
            self.have_alias = True
        except (OSError, NotImplementedError):
            self.have_alias = False

    def _collect(self):
        roots = [("agents", str(self.roots))]
        if self.have_alias:
            roots.append(("claude", str(self.alias.parent)))
        old = library_catalog.ROOTS
        library_catalog.ROOTS = roots
        try:
            return library_catalog.collect()
        finally:
            library_catalog.ROOTS = old

    def test_collect_survives_tricky_shapes(self):
        skills, counts = self._collect()
        names = {s["name"] for s in skills}
        self.assertIn("nested-skill", names)
        self.assertIn("unicode-space-skill", names)
        self.assertIn("dup-skill", names)
        self.assertIn("malformed-skill", names)  # falls back to dir name
        nested = next(s for s in skills if s["name"] == "nested-skill")
        self.assertEqual("group", nested["category"])
        malformed = next(s for s in skills if s["name"] == "malformed-skill")
        self.assertEqual("", malformed["description"])
        if self.have_alias:
            # Documented current behavior: os.walk does not descend into a
            # per-skill symlinked dir (followlinks=False), so the alias root
            # contributes nothing. Whole-root junctions still work because
            # the walk starts at the root path itself.
            self.assertEqual(["agents"], nested["roots"])

    def test_finder_search_and_id_stability_on_fixture(self):
        skills, _ = self._collect()
        rows = []
        for s in skills:
            if not Path(s["skill_md"]).is_file():
                continue  # broken links never reach the index
            rows.append(
                {
                    "name": s["name"],
                    "description": s["description"],
                    "category": s["category"],
                    "path": s["path"],
                    "skill_md": s["skill_md"],
                    "source": s["source"],
                    "canonical": s["canonical"],
                    "roots": s["roots"],
                }
            )
        (self.library / "skills_index.json").write_text(
            json.dumps({"skills": rows}), encoding="utf-8"
        )
        (self.library / "mcp_servers.yaml").write_text(
            "mcp_servers: {}\n", encoding="utf-8"
        )
        payload = finder.refresh_index(home=self.home, library=self.library)
        before = {
            c["name"]: c["id"] for c in payload["capabilities"] if c["type"] == "skill"
        }
        hits = finder.search(
            "nested skill deep", kind="skill", home=self.home, library=self.library
        )
        self.assertTrue(hits and hits[0]["name"] == "nested-skill")
        self.assertEqual(
            [], finder.search("qzxqzxunmatched", home=self.home, library=self.library)
        )
        # IDs survive a re-refresh with zero content changes.
        again = finder.refresh_index(home=self.home, library=self.library)
        after = {
            c["name"]: c["id"] for c in again["capabilities"] if c["type"] == "skill"
        }
        self.assertEqual(before, after)
        if self.have_broken_link:
            self.assertFalse(os.path.exists(self.broken))


if __name__ == "__main__":
    unittest.main()
