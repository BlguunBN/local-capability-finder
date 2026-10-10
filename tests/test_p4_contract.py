"""P4: MCP Contract v1 — load/prepare/deactivate/refresh, structured errors."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import capability_finder as finder
import capability_mcp
import library_catalog

STUB_SERVER = """\
import json, sys
for line in sys.stdin:
    try:
        message = json.loads(line)
    except ValueError:
        continue
    if message.get("id") == 2:
        print(json.dumps({"jsonrpc": "2.0", "id": 2, "result": {"tools": [
            {"name": "stubtool", "description": "Stub tool for schema capture",
             "inputSchema": {"type": "object", "properties": {"q": {"type": "string"}}}},
        ]}}), flush=True)
"""


def write_skill(folder: Path, name: str, description: str) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\nbody text\n",
        encoding="utf-8",
    )
    (folder / "secret.txt").write_text("must never be served", encoding="utf-8")


class ContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        base = Path(cls.temp.name)
        cls.home = base / "home"
        cls.library = base / "lib"
        cls.repo = Path(__file__).resolve().parents[1]
        agents_skills = cls.home / ".agents" / "skills"
        write_skill(
            agents_skills / "approved-skill",
            "approved-skill",
            "Approved skill for contract tests",
        )
        risky = base / "risky-src"
        write_skill(risky / "blocked-skill", "blocked-skill", "Blocked skill")
        review = base / "review-src"
        write_skill(review / "review-skill", "review-skill", "Review skill")
        roots_patch = mock.patch.object(
            library_catalog, "ROOTS", [("agents", str(agents_skills))]
        )
        roots_patch.start()
        cls.addClassCleanup(roots_patch.stop)
        lib_patch = mock.patch.object(library_catalog, "LIBRARY", str(cls.library))
        lib_patch.start()
        cls.addClassCleanup(lib_patch.stop)
        env_patch = mock.patch.dict(os.environ, {"CAPFIND_HOME": str(cls.home),
                                                  "CAPFIND_LIBRARY": str(cls.library)})
        env_patch.start()
        cls.addClassCleanup(env_patch.stop)
        cls.library.mkdir(parents=True, exist_ok=True)
        library_catalog.register_source(
            str(cls.library), "risky", str(risky), trust="blocked"
        )
        library_catalog.register_source(
            str(cls.library), "review", str(review), trust="needs_review"
        )
        skills, counts = library_catalog.collect()
        library_catalog.write_json(skills, counts)
        stub = base / "stub_server.py"
        stub.write_text(STUB_SERVER, encoding="utf-8")
        (cls.library / "mcp_servers.yaml").write_text(
            "mcp_servers:\n"
            f"  stubserver:\n    command: {sys.executable}\n    args: [{stub}]\n"
            "    description: Stub MCP server\n",
            encoding="utf-8",
        )
        finder.refresh_index(home=cls.home, library=cls.library, probe_tools=True)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def call(self, name, arguments):
        return capability_mcp._call_tool(name, arguments)

    def approved_id(self):
        hits = self.call("search_capabilities", {"query": "approved skill contract"})
        self.assertTrue(hits)
        return hits[0]["id"]

    def test_search_get_load_flow_serves_only_skill_md(self):
        cap_id = self.approved_id()
        detail = self.call("get_capability", {"capability_id": cap_id})
        self.assertEqual(cap_id, detail["id"])
        loaded = self.call("load_skill", {"capability_id": cap_id})
        self.assertEqual(detail["content_hash"], loaded["revision"])
        self.assertIn("body text", loaded["content"])
        self.assertNotIn("must never be served", loaded["content"])
        with self.assertRaises(finder.CapError) as ctx:
            self.call(
                "load_skill", {"capability_id": cap_id, "revision": "sha256:stale"}
            )
        self.assertEqual("STALE_REVISION", ctx.exception.code)

    def test_structured_errors_carry_codes(self):
        with self.assertRaises(finder.CapError) as ctx:
            self.call("get_capability", {"capability_id": "skill:does-not-exist"})
        self.assertEqual("NOT_FOUND", ctx.exception.code)
        response = capability_mcp._dispatch(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "get_capability",
                    "arguments": {"capability_id": "skill:does-not-exist"},
                },
            }
        )
        self.assertEqual("NOT_FOUND", response["error"]["data"]["code"])
        response = capability_mcp._dispatch(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "search_capabilities",
                    "arguments": {"query": "x", "agent": "nope"},
                },
            }
        )
        self.assertEqual("UNSUPPORTED_AGENT", response["error"]["data"]["code"])
        # Conflict: occupy the target with an unmanaged directory first.
        cap_id = self.approved_id()
        target = self.home / ".codex" / "skills" / "approved-skill"
        target.mkdir(parents=True)
        response = capability_mcp._dispatch(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "activate_skill",
                    "arguments": {"capability_id": cap_id, "agent": "codex"},
                },
            }
        )
        self.assertEqual("CONFLICT", response["error"]["data"]["code"])

    def test_invalid_paths_carry_structured_code(self):
        with self.assertRaises(finder.CapError) as ctx:
            finder._validate_skill_name("../escape")
        self.assertEqual("INVALID_PATH", ctx.exception.code)
        self.assertIsInstance(ctx.exception, ValueError)

    def test_prepare_is_dry_run_with_findings(self):
        cap_id = self.approved_id()
        prepared = self.call(
            "prepare_activation", {"capability_id": cap_id, "agent": "codex"}
        )
        self.assertEqual("ready", prepared["plan"]["status"])
        self.assertFalse(prepared["approval_required"])
        self.assertFalse(os.path.lexists(prepared["plan"]["target"]))
        blocked = self.call("search_capabilities", {"query": "blocked skill"})
        self.assertTrue(blocked)
        prepared = self.call(
            "prepare_activation", {"capability_id": blocked[0]["id"], "agent": "codex"}
        )
        self.assertTrue(prepared["approval_required"])
        self.assertTrue(any(f["code"] == "UNTRUSTED" for f in prepared["findings"]))
        with self.assertRaises(finder.CapError) as ctx:
            self.call(
                "activate_skill",
                {
                    "capability_id": blocked[0]["id"],
                    "agent": "codex",
                    "confirm_untrusted": True,
                },
            )
        self.assertEqual("UNTRUSTED", ctx.exception.code)

    def test_review_skill_needs_explicit_confirmation(self):
        hits = self.call("search_capabilities", {"query": "review skill"})
        self.assertTrue(hits)
        with self.assertRaises(finder.CapError) as ctx:
            self.call(
                "activate_skill", {"capability_id": hits[0]["id"], "agent": "codex"}
            )
        self.assertEqual("UNTRUSTED", ctx.exception.code)
        result = self.call(
            "activate_skill",
            {
                "capability_id": hits[0]["id"],
                "agent": "codex",
                "confirm_untrusted": True,
            },
        )
        self.assertEqual("activated", result["status"])
        again = self.call(
            "activate_skill",
            {
                "capability_id": hits[0]["id"],
                "agent": "codex",
                "confirm_untrusted": True,
            },
        )
        self.assertEqual("already_active", again["status"])
        removed = self.call(
            "deactivate_skill", {"capability_id": hits[0]["id"], "agent": "codex"}
        )
        self.assertEqual("deactivated", removed["status"])
        removed_again = self.call(
            "deactivate_skill", {"capability_id": hits[0]["id"], "agent": "codex"}
        )
        self.assertEqual("already_removed", removed_again["status"])

    def test_refresh_catalog_tool(self):
        result = self.call("refresh_catalog", {})
        self.assertGreater(result["indexed"], 0)
        self.assertTrue(result["path"].endswith("capabilities_index.json"))

    def test_tool_schema_descriptor_and_probed_schema(self):
        servers = self.call(
            "search_capabilities", {"query": "stub server", "kind": "tool"}
        )
        by_name = {h["name"]: h["id"] for h in servers}
        self.assertIn("stubserver", by_name)
        tools = self.call(
            "search_capabilities", {"query": "stubtool capture", "kind": "tool"}
        )
        by_name.update({h["name"]: h["id"] for h in tools})
        descriptor = self.call(
            "get_tool_schema", {"capability_id": by_name["stubserver"]}
        )
        self.assertIsNone(descriptor["schema"])
        self.assertIn("probe", descriptor["note"])
        self.assertIn("stubtool", by_name)
        probed = self.call("get_tool_schema", {"capability_id": by_name["stubtool"]})
        self.assertEqual(
            {"type": "object", "properties": {"q": {"type": "string"}}},
            probed["schema"],
        )

    def test_audit_log_records_calls(self):
        response = capability_mcp._dispatch({
            "jsonrpc": "2.0", "id": 90, "method": "tools/call",
            "params": {"name": "search_capabilities",
                       "arguments": {"query": "audit probe"}},
        })
        self.assertIn("result", response)
        log = self.library / "_audit.log"
        self.assertTrue(log.is_file())
        entries = [
            json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()
        ]
        self.assertTrue(
            any(
                e["tool"] == "search_capabilities" and e["status"] == "ok"
                for e in entries
            )
        )

    def test_stdio_contract_flow(self):
        env = dict(
            os.environ,
            CAPFIND_HOME=str(self.home),
            CAPFIND_LIBRARY=str(self.library),
            PYTHONPATH=str(self.repo),
        )
        server = self.repo / "capability_mcp.py"
        cap_id = self.approved_id()

        def rpc(index, method, params=None):
            return json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": index,
                    "method": method,
                    "params": params or {},
                }
            )

        wire = (
            "\n".join(
                [
                    rpc(1, "initialize", {"protocolVersion": "2025-11-25"}),
                    rpc(2, "tools/list"),
                    rpc(
                        3,
                        "tools/call",
                        {
                            "name": "search_capabilities",
                            "arguments": {"query": "approved skill contract"},
                        },
                    ),
                    rpc(
                        4,
                        "tools/call",
                        {"name": "load_skill", "arguments": {"capability_id": cap_id}},
                    ),
                    rpc(
                        5,
                        "tools/call",
                        {
                            "name": "get_capability",
                            "arguments": {"capability_id": "skill:missing"},
                        },
                    ),
                ]
            )
            + "\n"
        )
        proc = subprocess.run(
            [sys.executable, str(server)],
            input=wire,
            text=True,
            capture_output=True,
            timeout=60,
            cwd=self.repo,
            env=env,
        )
        self.assertEqual("", proc.stderr)
        responses = {
            json.loads(line)["id"]: json.loads(line)
            for line in proc.stdout.splitlines()
        }
        self.assertEqual(9, len(responses[2]["result"]["tools"]))
        self.assertIn(cap_id, responses[3]["result"]["content"][0]["text"])
        body = responses[4]["result"]["content"][0]["text"]
        self.assertIn("body text", body)
        self.assertNotIn("must never be served", body)
        self.assertEqual("NOT_FOUND", responses[5]["error"]["data"]["code"])


if __name__ == "__main__":
    unittest.main()
