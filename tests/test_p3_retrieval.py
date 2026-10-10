"""P3: BM25 retrieval benchmark on a labeled fixture catalog.

Runs every case in tests/fixtures/retrieval_cases.jsonl (>=100) against a
synthetic catalog and reports top-1/top-3/no-match accuracy plus latency.
"""

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import capability_finder as finder
import library_catalog

SKILLS = {
    "docker-containers": "Build container images with Dockerfiles and run multi-service stacks with Compose",
    "kubernetes-deploy": "Author Kubernetes manifests and Helm releases for cluster workloads",
    "terraform-aws": "Provision AWS infrastructure with Terraform modules",
    "pdf-editor": "Edit PDF documents and pages",
    "dashboard-design": "Design analytics dashboards and charts",
    "dicom-preflight": "Validate DICOM medical imaging series before import",
    "dicom-anonymize": "Anonymize DICOM metadata for research sharing",
    "cad-viewer": "Inspect and review STEP CAD files and 3D models",
    "csv-cleaner": "Clean and normalize messy CSV spreadsheet data",
    "unit-tester": "Write table-driven unit tests with pytest",
    "code-reviewer": "Review pull requests for bugs and style",
    "git-workflow": "Branch, commit, and open pull requests; repository hygiene with git",
    "api-designer": "Design REST API endpoints with OpenAPI schemas",
    "postgres-tuning": "Tune PostgreSQL queries with EXPLAIN and indexes",
    "redis-cache": "Cache hot reads with Redis TTLs",
    "ci-pipeline": "Build GitHub Actions CI pipelines",
    "oauth-login": "Add OAuth authentication and login with PKCE and JWT sessions",
    "stripe-billing": "Bill subscriptions with Stripe webhooks",
    "email-sender": "Send transactional email with retries",
    "pdf-ocr": "Extract text from scanned PDFs with OCR",
    "image-resize": "Resize and compress images for the web",
    "cuda-quantum": "Develop CUDA quantum computing programs",
    "finops-aws": "Attribute AWS spend and cut idle cloud costs",
    "pentest-web": "Test web apps for OWASP injection flaws",
    "bio-sequence": "Align DNA sequences with BLAST",
    "iac-terraform-cloud": "Manage Terraform Cloud workspaces and runs",
    "web-fetch": "Fetch URL content as markdown",
    "web-scraper": "Scrape paginated listings into datasets",
    "markdown-notes": "Capture Markdown notes with wiki links",
    "json-schema": "Validate JSON payloads with schemas",
    "s3-backup": "Back up files to S3 object storage",
    "nginx-proxy": "Route traffic with an nginx reverse proxy",
    "react-forms": "Build React forms with validation",
    "cron-scheduler": "Run cron schedules with retries",
    "log-search": "Search logs with Loki queries",
    "feature-flags": "Roll out features behind flags",
    "websocket-chat": "Build websocket chat rooms",
    "graphql-gateway": "Federate GraphQL subgraphs",
    "license-audit": "Audit dependency licenses",
    "secret-scan": "Scan repos for leaked secrets",
    "load-test": "Load test APIs with k6",
    "yaml-lint": "Lint YAML configs",
    "accessibility-audit": "Audit WCAG contrast and keyboard support",
    "seo-meta": "Write SEO titles and meta descriptions",
    "changelog-writer": "Draft changelogs from commit history",
    "onboarding-emails": "Write onboarding email drips",
    "incident-runbook": "Write incident response runbooks and on-call rotations",
    "postmortem-notes": "Write blameless postmortems",
    "threat-model": "Model threats with STRIDE",
    "javascript-bundler": "Bundle JavaScript with tree-shaking",
    "typescript-strict": "Enforce strict TypeScript types",
    "python-dataframes": "Analyze tabular data with Python DataFrames",
    "database-migrate": "Version relational database schemas with migrations",
    "app-profiler": "Profile application performance bottlenecks",
    "docs-writer": "Write documentation guides and API references",
    "app-config": "Manage application configuration, environment variables, and secrets rotation",
}

TOOLS_YAML = (
    "mcp_servers:\n"
    "  playwright:\n"
    "    description: Automate browser with Playwright\n"
    "    command: npx\n    args: ['@playwright/mcp']\n"
    "  github-sync:\n"
    "    description: Sync code with GitHub repositories\n"
    "    command: npx\n    args: ['@acme/github-sync-mcp']\n"
)


def load_cases():
    path = Path(__file__).resolve().parent / "fixtures" / "retrieval_cases.jsonl"
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


class RetrievalBenchmarkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.home = Path(cls.temp.name) / "home"
        cls.library = Path(cls.temp.name) / "lib"
        roots_patch = mock.patch.object(
            library_catalog,
            "ROOTS",
            [("agents", str(cls.home / ".agents" / "skills"))],
        )
        roots_patch.start()
        cls.addClassCleanup(roots_patch.stop)
        lib_patch = mock.patch.object(library_catalog, "LIBRARY", str(cls.library))
        lib_patch.start()
        cls.addClassCleanup(lib_patch.stop)
        rows = []
        for name, description in SKILLS.items():
            folder = cls.home / ".agents" / "skills" / name
            folder.mkdir(parents=True)
            (folder / "SKILL.md").write_text(
                f"---\nname: {name}\ndescription: {description}\n---\nbody\n",
                encoding="utf-8",
            )
            rows.append(
                {
                    "name": name,
                    "description": description,
                    "category": "bench",
                    "path": str(folder),
                    "skill_md": str(folder / "SKILL.md"),
                    "source": "agents",
                    "canonical": True,
                    "roots": ["agents"],
                }
            )
        cls.library.mkdir(parents=True, exist_ok=True)
        (cls.library / "skills_index.json").write_text(
            json.dumps({"skills": rows}), encoding="utf-8"
        )
        (cls.library / "mcp_servers.yaml").write_text(TOOLS_YAML, encoding="utf-8")
        finder.refresh_index(home=cls.home, library=cls.library)
        # Warm the token cache before measuring.
        finder.search("warm up the cache", home=cls.home, library=cls.library)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_cases_file_has_full_coverage(self):
        cases = load_cases()
        self.assertGreaterEqual(len(cases), 100, "need at least 100 labeled cases")
        modes = {case["mode"] for case in cases}
        self.assertTrue({"top1", "top3", "none"} <= modes)

    def _reindex(self):
        skills, counts = library_catalog.collect()
        library_catalog.write_json(skills, counts)
        (self.library / "mcp_servers.yaml").write_text(TOOLS_YAML, encoding="utf-8")
        finder.refresh_index(home=self.home, library=self.library)

    def test_retrieval_accuracy_and_latency(self):
        self._reindex()
        cases = load_cases()
        misses = []
        latencies = []
        for case in cases:
            start = time.perf_counter()
            results = finder.search(
                case["query"],
                kind=case.get("kind"),
                home=self.home,
                library=self.library,
            )
            latencies.append((time.perf_counter() - start) * 1000)
            names = [row["name"] for row in results]
            for row in results:
                self.assertEqual(
                    {"id", "type", "name", "reason", "availability", "matched"},
                    set(row),
                    "search rows stay lightweight",
                )
            if case["mode"] == "top1" and (not names or names[0] != case["expected"]):
                misses.append((case["query"], case["expected"], names[:3]))
            elif case["mode"] == "top3" and case["expected"] not in names:
                misses.append((case["query"], case["expected"], names[:3]))
            elif case["mode"] == "none" and names:
                misses.append((case["query"], None, names[:3]))
        latencies.sort()
        p50 = latencies[len(latencies) // 2]
        p95 = latencies[int(len(latencies) * 0.95)]
        total = len(cases)
        print(
            f"\nretrieval: {total - len(misses)}/{total} "
            f"p50={p50:.1f}ms p95={p95:.1f}ms"
        )
        for miss in misses:
            print("MISS", miss)
        self.assertEqual([], misses)
        self.assertLess(p95, 2000.0)

    def test_exact_id_lookup_returns_that_record(self):
        detail = finder.show(
            finder.search(
                "table driven pytest",
                kind="skill",
                home=self.home,
                library=self.library,
            )[0]["id"],
            home=self.home,
            library=self.library,
        )
        by_canonical = finder.search(detail["id"], home=self.home, library=self.library)
        self.assertEqual(detail["id"], by_canonical[0]["id"])
        self.assertIn("exact", by_canonical[0]["matched"])
        by_legacy = finder.search(
            detail["legacy_ids"][0], home=self.home, library=self.library
        )
        self.assertEqual(detail["id"], by_legacy[0]["id"])

    def test_incremental_catalog_skips_unchanged_files(self):
        skills, counts = library_catalog.collect()
        library_catalog.write_json(skills, counts)
        calls = {"count": 0}
        real_frontmatter = library_catalog.parse_frontmatter
        real_metadata = library_catalog.parse_metadata

        def counting_frontmatter(text):
            calls["count"] += 1
            return real_frontmatter(text)

        def counting_metadata(text):
            calls["count"] += 1
            return real_metadata(text)

        target = self.home / ".agents" / "skills" / "yaml-lint" / "SKILL.md"
        target.write_text(
            target.read_text(encoding="utf-8").replace(
                "Lint YAML configs", "Lint YAML files"
            ),
            encoding="utf-8",
        )
        with (
            mock.patch.object(
                library_catalog, "parse_frontmatter", side_effect=counting_frontmatter
            ),
            mock.patch.object(
                library_catalog, "parse_metadata", side_effect=counting_metadata
            ),
        ):
            skills2, _ = library_catalog.collect()
        # Only the edited file is re-parsed; everything else is hash-skipped.
        self.assertEqual(2, calls["count"])
        self.assertEqual(len(skills), len(skills2))

    def test_refresh_preserves_last_good_index_on_bad_catalog(self):
        payload = finder.refresh_index(home=self.home, library=self.library)
        self.assertTrue(payload["capabilities"])
        snapshot = (self.library / "capabilities_index.json").read_bytes()
        (self.library / "skills_index.json").write_text("{broken", encoding="utf-8")
        with self.assertRaises((ValueError, RuntimeError)):
            finder.refresh_index(home=self.home, library=self.library)
        self.assertEqual(
            snapshot, (self.library / "capabilities_index.json").read_bytes()
        )
        codes = {f["code"] for f in finder.doctor(home=self.home, library=self.library)}
        self.assertIn("INDEX_STALE_OR_UNREADABLE", codes)


if __name__ == "__main__":
    unittest.main()
