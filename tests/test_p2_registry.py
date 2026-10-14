"""P2: canonical registry, stable identities, source registration, lockfile."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import capability_finder as finder
import library_catalog


def write_skill(folder: Path, name: str, description: str, extra: str = "") -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n{extra}---\nbody\n",
        encoding="utf-8",
    )


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.home = base / "home"
        self.library = base / "lib"
        self.stack = base / "stack" / "suites"
        write_skill(
            self.stack / "systematic-debugging-suite",
            "systematic-debugging-suite",
            "Reproduce, isolate, repair, and verify bugs",
            "version: 1.2.0\ntags: [debugging, engineering]\n",
        )
        write_skill(
            self.stack / "web-scraping-suite",
            "web-scraping-suite",
            "Extract structured data from websites",
        )
        write_skill(
            self.home / ".agents" / "skills" / "web-scraping-suite",
            "web-scraping-suite",
            "Different local scraper implementation",
        )
        roots_patch = mock.patch.object(
            library_catalog, "ROOTS",
            [("agents", str(self.home / ".agents" / "skills"))],
        )
        roots_patch.start()
        self.addCleanup(roots_patch.stop)
        library_catalog.register_source(
            str(self.library), "aes", str(self.stack), priority=10, trust="approved"
        )
        self.reindex()

    def reindex(self):
        skills, _ = library_catalog.collect(str(self.library))
        self.library.mkdir(parents=True, exist_ok=True)
        (self.library / "skills_index.json").write_text(
            json.dumps({"skills": skills}), encoding="utf-8"
        )
        (self.library / "mcp_servers.yaml").write_text(
            "mcp_servers: {}\n", encoding="utf-8"
        )
        return finder.refresh_index(home=self.home, library=self.library)

    def test_aes_source_resolves_canonical_ids(self):
        hits = finder.search(
            "reproduce isolate repair bugs",
            kind="skill",
            home=self.home,
            library=self.library,
        )
        self.assertTrue(hits)
        self.assertEqual("aes:skill:systematic-debugging-suite", hits[0]["id"])
        detail = finder.show(hits[0]["id"], home=self.home, library=self.library)
        self.assertEqual("aes", detail["source_id"])
        self.assertEqual(1, detail["schema_version"])
        self.assertEqual("1.2.0", detail["version"])
        self.assertIn("debugging", detail["tags"])
        self.assertEqual("approved", detail["trust_status"])
        self.assertEqual("available", detail["installation_state"])
        self.assertTrue(detail["content_hash"].startswith("sha256:"))
        self.assertEqual([], detail["managed_targets"])
        self.assertEqual(1, len(detail["legacy_ids"]))

    def test_blank_frontmatter_values_do_not_capture_next_line(self):
        metadata = library_catalog.parse_metadata(
            "---\nname: example\nversion:\ntags:\nrequirements: python 3.11\n---\n"
        )
        self.assertIsNone(metadata["version"])
        self.assertEqual([], metadata["tags"])
        self.assertEqual(["python", "3.11"], metadata["requirements"])

    def test_legacy_id_still_resolves(self):
        canonical = "aes:skill:systematic-debugging-suite"
        detail = finder.show(canonical, home=self.home, library=self.library)
        legacy = detail["legacy_ids"][0]
        self.assertNotEqual(canonical, legacy)
        self.assertEqual(
            canonical,
            finder.show(legacy, home=self.home, library=self.library)["id"],
        )

    def test_duplicate_name_across_sources_stays_distinct(self):
        hits = finder.search(
            "scraper extract websites",
            kind="skill",
            home=self.home,
            library=self.library,
        )
        self.assertEqual(1, len([r for r in hits if r["name"] == "web-scraping-suite"]))
        detail = finder.show(hits[0]["id"], home=self.home, library=self.library)
        self.assertEqual(1, len(detail["alternate_versions"]))
        ids = {detail["id"], detail["alternate_versions"][0]["id"]}
        self.assertEqual(2, len(ids))
        self.assertIn("aes:skill:web-scraping-suite", ids)

    def test_content_revision_keeps_stable_id(self):
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

    def test_unregister_source_removes_records(self):
        library_catalog.unregister_source(str(self.library), "aes")
        self.assertEqual([], library_catalog.load_sources(str(self.library)))
        self.reindex()
        self.assertEqual(
            [],
            finder.search(
                "reproduce isolate repair bugs",
                kind="skill",
                home=self.home,
                library=self.library,
            ),
        )
        with self.assertRaises(ValueError):
            library_catalog.unregister_source(str(self.library), "aes")

    def test_lockfile_tracks_activation_lifecycle(self):
        canonical = "aes:skill:systematic-debugging-suite"
        revision = finder.show(canonical, home=self.home, library=self.library)[
            "content_hash"
        ]
        result = finder.activate(
            canonical, "codex", home=self.home, library=self.library
        )
        self.assertEqual(canonical, result["id"])
        lock = json.loads(
            (self.library / "activations-lock.json").read_text(encoding="utf-8")
        )
        self.assertEqual(1, len(lock["entries"]))
        entry = lock["entries"][0]
        self.assertEqual(
            (canonical, revision, "codex"),
            (entry["id"], entry["revision"], entry["agent"]),
        )
        detail = finder.show(canonical, home=self.home, library=self.library)
        self.assertTrue(
            any(
                t["agent"] == "codex" and t["revision"] == revision
                for t in detail["managed_targets"]
            )
        )
        finder.deactivate_skill(
            canonical, "codex", home=self.home, library=self.library
        )
        lock = json.loads(
            (self.library / "activations-lock.json").read_text(encoding="utf-8")
        )
        self.assertEqual([], lock["entries"])

    def test_local_skills_keep_namespaced_ids(self):
        hits = finder.search(
            "different local scraper",
            kind="skill",
            home=self.home,
            library=self.library,
        )
        names = [h["id"] for h in hits if h["name"] == "web-scraping-suite"]
        self.assertIn("agents:skill:web-scraping-suite", names)


if __name__ == "__main__":
    unittest.main()
