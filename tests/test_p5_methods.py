"""P5: link-first activation with verified copy fallback, per-method lifecycle."""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import capability_finder as finder


def make_skill(folder: Path, name: str, description: str) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\nbody\n",
        encoding="utf-8",
    )
    (folder / "ref.md").write_text("reference", encoding="utf-8")


class CopyMethodTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name) / "home"
        self.library = Path(self.temp.name) / "lib"
        src = self.home / ".agents" / "skills" / "copy-skill"
        make_skill(src, "copy-skill", "Copy method fixture skill")
        rows = [
            {
                "name": "copy-skill",
                "description": "Copy method fixture skill",
                "category": "t",
                "path": str(src),
                "skill_md": str(src / "SKILL.md"),
                "source": "agents",
                "canonical": True,
                "roots": ["agents"],
            }
        ]
        self.library.mkdir(parents=True, exist_ok=True)
        (self.library / "skills_index.json").write_text(
            json.dumps({"skills": rows}), encoding="utf-8"
        )
        (self.library / "mcp_servers.yaml").write_text(
            "mcp_servers: {}\n", encoding="utf-8"
        )
        finder.refresh_index(home=self.home, library=self.library)
        self.cap_id = finder.search(
            "copy method fixture", kind="skill", home=self.home, library=self.library
        )[0]["id"]

    def target(self):
        return self.home / ".codex" / "skills" / "copy-skill"

    def test_preview_defaults_to_link_and_reports_copy(self):
        self.assertIn(
            "symlink",
            finder.preview_activation(
                self.cap_id, "codex", home=self.home, library=self.library
            )["method"],
        )
        plan = finder.preview_activation(
            self.cap_id, "codex", home=self.home, library=self.library, method="copy"
        )
        self.assertEqual("copy", plan["method"])
        self.assertEqual("ready", plan["status"])
        with self.assertRaises(ValueError):
            finder.preview_activation(
                self.cap_id,
                "codex",
                home=self.home,
                library=self.library,
                method="clone",
            )

    def test_copy_lifecycle_is_verified_and_reversible(self):
        result = finder.activate(
            self.cap_id, "codex", home=self.home, library=self.library, method="copy"
        )
        self.assertEqual("activated", result["status"])
        target = self.target()
        self.assertTrue(target.is_dir())
        self.assertFalse(target.is_symlink())
        self.assertEqual("reference", (target / "ref.md").read_text(encoding="utf-8"))
        manifest = json.loads(
            (self.library / "_activations" / "codex__copy-skill.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual("copy", manifest["link_kind"])
        self.assertTrue(manifest["source_tree"].startswith("sha256:"))
        lock = json.loads(
            (self.library / "activations-lock.json").read_text(encoding="utf-8")
        )
        self.assertEqual("copy", lock["entries"][0]["method"])
        # Re-activating the identical copy is idempotent.
        again = finder.activate(
            self.cap_id, "codex", home=self.home, library=self.library, method="copy"
        )
        self.assertEqual("already_active", again["status"])
        out = finder.deactivate_skill(
            self.cap_id, "codex", home=self.home, library=self.library
        )
        self.assertEqual("deactivated", out["status"])
        self.assertFalse(os.path.lexists(target))
        source = self.home / ".agents" / "skills" / "copy-skill"
        self.assertTrue((source / "SKILL.md").is_file())

    def test_copy_preserves_source_symlinks(self):
        source = self.home / ".agents" / "skills" / "copy-skill"
        link = source / "linked-ref.md"
        try:
            link.symlink_to("ref.md")
        except OSError as exc:
            self.skipTest(f"file symlinks unavailable: {exc}")
        result = finder.activate(
            self.cap_id, "codex", home=self.home, library=self.library, method="copy"
        )
        self.assertEqual("activated", result["status"])
        self.assertTrue((self.target() / "linked-ref.md").is_symlink())

    def test_modified_copy_is_never_deleted(self):
        finder.activate(
            self.cap_id, "codex", home=self.home, library=self.library, method="copy"
        )
        target = self.target()
        (target / "user-notes.txt").write_text("user data", encoding="utf-8")
        with self.assertRaises(FileExistsError):
            finder.deactivate_skill(
                self.cap_id, "codex", home=self.home, library=self.library
            )
        self.assertEqual("user data", (target / "user-notes.txt").read_text())
        codes = {f["code"] for f in finder.doctor(home=self.home, library=self.library)}
        self.assertIn("ACTIVATION_DRIFT", codes)


if __name__ == "__main__":
    unittest.main()
