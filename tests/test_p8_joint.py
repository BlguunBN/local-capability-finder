"""P8: Finder + Engineering-Stack joint contract scenarios.

Uses a synthesized mini-stack (registered as `aes`, mirroring the real
companion layout) plus one personal specialist skill and one blocked import.
All fixtures are temp dirs; nothing touches the real library or agents.
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import capability_finder as finder
import library_catalog


def write_skill(folder: Path, name: str, description: str) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n{name} body\n",
        encoding="utf-8",
    )


class JointContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        base = Path(cls.temp.name)
        cls.home = base / "home"
        cls.library = base / "lib"
        cls.repo = Path(__file__).resolve().parents[1]
        # Mini-stack mirroring agentic-engineering-stack/suites/.
        cls.stack = base / "stack" / "suites"
        write_skill(
            cls.stack / "systematic-debugging-suite",
            "systematic-debugging-suite",
            "Reproduce, isolate, repair, and verify bugs",
        )
        write_skill(
            cls.stack / "unit-testing-suite",
            "unit-testing-suite",
            "Table-driven unit tests with fixtures",
        )
        # Personal specialist skill absent from the core suites.
        write_skill(
            cls.home / ".agents" / "skills" / "dicom-preflight",
            "dicom-preflight",
            "Validate DICOM medical imaging series",
        )
        # Same-name skill from another origin with different content.
        write_skill(
            cls.home / ".claude" / "skills" / "unit-testing-suite",
            "unit-testing-suite",
            "Different local testing notes",
        )
        # Unapproved import.
        write_skill(
            base / "imports" / "shady-skill",
            "shady-skill",
            "Imported community skill of unknown provenance",
        )
        roots_patch = mock.patch.object(
            library_catalog,
            "ROOTS",
            [
                ("agents", str(cls.home / ".agents" / "skills")),
                ("claude", str(cls.home / ".claude" / "skills")),
            ],
        )
        roots_patch.start()
        cls.addClassCleanup(roots_patch.stop)
        lib_patch = mock.patch.object(library_catalog, "LIBRARY", str(cls.library))
        lib_patch.start()
        cls.addClassCleanup(lib_patch.stop)
        cls.library.mkdir(parents=True, exist_ok=True)
        library_catalog.register_source(str(cls.library), "aes", str(cls.stack))
        library_catalog.register_source(
            str(cls.library), "community", str(base / "imports"), trust="blocked"
        )
        cls.reindex()

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    @classmethod
    def reindex(cls):
        skills, counts = library_catalog.collect()
        library_catalog.write_json(skills, counts)
        (cls.library / "mcp_servers.yaml").write_text(
            "mcp_servers: {}\n", encoding="utf-8"
        )
        return finder.refresh_index(home=cls.home, library=cls.library)

    def test_stack_registers_by_namespace_without_install(self):
        ids = {
            c["id"]
            for c in finder.refresh_index(home=self.home, library=self.library)[
                "capabilities"
            ]
        }
        self.assertIn("aes:skill:systematic-debugging-suite", ids)
        self.assertIn("aes:skill:unit-testing-suite", ids)
        self.assertEqual([], list((self.home / ".codex").rglob("SKILL.md"))
                         if (self.home / ".codex").exists() else [])

    def test_search_and_load_one_suite_only(self):
        hits = finder.search(
            "reproduce isolate repair bugs",
            kind="skill",
            home=self.home,
            library=self.library,
        )
        self.assertEqual("aes:skill:systematic-debugging-suite", hits[0]["id"])
        loaded = finder.load_skill(hits[0]["id"], home=self.home, library=self.library)
        self.assertIn("systematic-debugging-suite body", loaded["content"])
        self.assertNotIn("unit-testing-suite body", loaded["content"])

    def test_suite_edit_keeps_id_changes_hash(self):
        canonical = "aes:skill:systematic-debugging-suite"
        before = finder.show(canonical, home=self.home, library=self.library)
        md = self.stack / "systematic-debugging-suite" / "SKILL.md"
        md.write_text(
            md.read_text(encoding="utf-8").replace("verify bugs", "verify bugs fast"),
            encoding="utf-8",
        )
        self.reindex()
        after = finder.show(canonical, home=self.home, library=self.library)
        self.assertEqual(canonical, after["id"])
        self.assertNotEqual(before["content_hash"], after["content_hash"])
        md.write_text(
            md.read_text(encoding="utf-8").replace("verify bugs fast", "verify bugs"),
            encoding="utf-8",
        )
        self.reindex()

    def test_specialist_request_loads_personal_skill(self):
        hits = finder.search(
            "validate dicom series", kind="skill", home=self.home, library=self.library
        )
        self.assertTrue(hits)
        self.assertEqual("agents:skill:dicom-preflight", hits[0]["id"])
        loaded = finder.load_skill(hits[0]["id"], home=self.home, library=self.library)
        self.assertIn("dicom-preflight body", loaded["content"])

    def test_activation_dry_run_apply_verify_rollback(self):
        canonical = "aes:skill:unit-testing-suite"
        plan = finder.preview_activation(
            canonical, "codex", home=self.home, library=self.library
        )
        self.assertEqual("ready", plan["status"])
        self.assertFalse(os.path.lexists(plan["target"]))
        result = finder.activate(
            canonical, "codex", home=self.home, library=self.library
        )
        self.assertEqual("activated", result["status"])
        self.assertTrue((Path(result["path"]) / "SKILL.md").is_file())
        out = finder.deactivate_skill(
            canonical, "codex", home=self.home, library=self.library
        )
        self.assertEqual("deactivated", out["status"])
        self.assertFalse(os.path.lexists(plan["target"]))
        self.assertEqual([], finder.doctor(home=self.home, library=self.library))

    def test_duplicate_name_selects_exact_source(self):
        detail = finder.show(
            "aes:skill:unit-testing-suite", home=self.home, library=self.library
        )
        self.assertEqual(1, len(detail["alternate_versions"]))
        other_id = detail["alternate_versions"][0]["id"]
        self.assertNotEqual(detail["id"], other_id)
        self.assertIn(
            "unit-testing-suite body",
            finder.load_skill(detail["id"], home=self.home, library=self.library)[
                "content"
            ],
        )
        self.assertIn(
            "Different local testing notes",
            finder.load_skill(other_id, home=self.home, library=self.library)[
                "content"
            ],
        )

    def test_blocked_import_stays_inspectable_but_inert(self):
        hits = finder.search(
            "unknown provenance community",
            kind="skill",
            home=self.home,
            library=self.library,
        )
        self.assertTrue(hits)
        detail = finder.show(hits[0]["id"], home=self.home, library=self.library)
        self.assertEqual("blocked", detail["trust_status"])
        with self.assertRaises(finder.CapError) as ctx:
            finder.activate(
                hits[0]["id"],
                "codex",
                home=self.home,
                library=self.library,
                confirm=True,
            )
        self.assertEqual("UNTRUSTED", ctx.exception.code)

    def test_finder_downtime_fails_explicitly_with_stack_fallback(self):
        (self.library / "capabilities_index.json").unlink()
        with self.assertRaises(FileNotFoundError) as ctx:
            finder.search("anything", home=self.home, library=self.library)
        self.assertIn("library_catalog.py", str(ctx.exception))
        # The Stack's files remain directly readable without the Finder.
        fallback = (self.stack / "systematic-debugging-suite" / "SKILL.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("Reproduce, isolate", fallback)
        self.reindex()

    def test_incompatible_sources_fail_explicitly(self):
        with self.assertRaises(ValueError):
            library_catalog.register_source(
                str(self.library), "bad", str(self.temp.name) + "-missing"
            )
        with self.assertRaises(ValueError):
            library_catalog.register_source(
                str(self.library), "bad2", str(self.stack), trust="bogus"
            )
        with self.assertRaises(finder.CapError) as ctx:
            finder.load_skill(
                "aes:skill:no-such-suite", home=self.home, library=self.library
            )
        self.assertEqual("NOT_FOUND", ctx.exception.code)

    def test_startup_context_stays_lightweight(self):
        import json as json_module

        rows = finder.search(
            "unit tests", kind="skill", home=self.home, library=self.library
        )
        self.assertTrue(rows)
        payload = json_module.dumps(rows, ensure_ascii=False).encode("utf-8")
        self.assertLess(len(payload), 2400)
        for row in rows:
            self.assertNotIn("content", row)
            self.assertNotIn("body", row.get("reason", ""))


if __name__ == "__main__":
    unittest.main()
