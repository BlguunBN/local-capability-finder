"""Protocol-era behavior for the stdio MCP adapter."""

import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

import capability_mcp


MODERN_META = {
    "io.modelcontextprotocol/protocolVersion": "2026-07-28",
    "io.modelcontextprotocol/clientCapabilities": {},
    "io.modelcontextprotocol/clientInfo": {"name": "test-client", "version": "1.0.0"},
}


def request(method, params=None):
    return {"jsonrpc": "2.0", "id": 7, "method": method, "params": params or {}}


class MCPProtocolTests(unittest.TestCase):
    def test_audit_omits_free_text_and_swallows_audit_failures(self):
        import tempfile
        import capability_finder

        with tempfile.TemporaryDirectory() as temp:
            home, library = Path(temp) / "home", Path(temp) / "lib"
            library.mkdir(parents=True)
            with patch("capability_finder._paths", return_value=(home, library)):
                capability_mcp._audit("search_capabilities", {
                    "query": "private search", "project_hint": "private project",
                    "agent": "codex", "capability_id": "skill:one", "limit": 3,
                }, "ok")
            entry = json.loads((library / "_audit.log").read_text(encoding="utf-8"))
            self.assertEqual({"agent": "codex", "capability_id": "skill:one"}, entry["arguments"])
        with patch("capability_finder._paths", side_effect=RuntimeError("audit unavailable")):
            capability_mcp._audit("search_capabilities", {"query": "private"}, "ok")

    def test_discover_advertises_modern_version_and_tools(self):
        response = capability_mcp._dispatch(request("server/discover", {"_meta": MODERN_META}))
        result = response["result"]
        self.assertEqual("complete", result["resultType"])
        self.assertEqual(["2026-07-28"], result["supportedVersions"])
        self.assertEqual({"tools": {}}, result["capabilities"])
        self.assertEqual("local-capability-finder", result["_meta"]["io.modelcontextprotocol/serverInfo"]["name"])
        self.assertEqual("0.2.0", result["_meta"]["io.modelcontextprotocol/serverInfo"]["version"])
        self.assertEqual("public", result["cacheScope"])
        self.assertGreaterEqual(result["ttlMs"], 0)

    def test_modern_tools_work_without_initialize(self):
        listed = capability_mcp._dispatch(request("tools/list", {"_meta": MODERN_META}))
        self.assertEqual("complete", listed["result"]["resultType"])
        self.assertEqual(9, len(listed["result"]["tools"]))
        self.assertEqual("public", listed["result"]["cacheScope"])
        self.assertGreaterEqual(listed["result"]["ttlMs"], 0)
        self.assertIn("io.modelcontextprotocol/serverInfo", listed["result"]["_meta"])
        with patch.object(capability_mcp, "_call_tool", return_value=[{"id": "skill:one"}]):
            called = capability_mcp._dispatch(request("tools/call", {
                "name": "search_capabilities", "arguments": {"query": "cad"}, "_meta": MODERN_META,
            }))
        self.assertEqual("complete", called["result"]["resultType"])
        self.assertIn("skill:one", called["result"]["content"][0]["text"])

    def test_modern_requests_validate_metadata(self):
        missing = capability_mcp._dispatch(request("server/discover"))
        self.assertEqual(-32602, missing["error"]["code"])
        incomplete = capability_mcp._dispatch(request("tools/list", {
            "_meta": {"io.modelcontextprotocol/protocolVersion": "2026-07-28"},
        }))
        self.assertEqual(-32602, incomplete["error"]["code"])
        unsupported = capability_mcp._dispatch(request("tools/list", {
            "_meta": {**MODERN_META, "io.modelcontextprotocol/protocolVersion": "2099-01-01"},
        }))
        self.assertEqual(-32022, unsupported["error"]["code"])
        self.assertEqual({"requested": "2099-01-01", "supported": ["2026-07-28"]}, unsupported["error"]["data"])

    def test_contract_handshake_is_explicit_and_versioned(self):
        info = capability_mcp._call_tool("get_contract_info", {})
        self.assertEqual("local-capability-finder", info["contract"])
        self.assertEqual(1, info["version"])
        self.assertEqual("library_catalog.py register-source", info["registration"])
        self.assertTrue({"register_source", "search", "get", "load", "activation-preview", "activation"}.issubset(info["features"]))

    def test_search_forwards_agent_and_project_hint(self):
        with patch("capability_finder.search", return_value=[]) as search:
            capability_mcp._call_tool("search_capabilities", {
                "query": "deploy workers", "kind": "skill", "agent": "codex",
                "project_hint": "Cloudflare Workers", "limit": 3,
            })
        search.assert_called_once_with(
            "deploy workers", kind="skill", limit=3,
            agent="codex", project_hint="Cloudflare Workers",
        )
        with self.assertRaisesRegex(ValueError, "project_hint"):
            capability_mcp._call_tool("search_capabilities", {
                "query": "deploy workers", "project_hint": "x" * 201,
            })

    def test_legacy_handshake_and_progress_metadata_remain_supported(self):
        initialized = capability_mcp._dispatch(request("initialize", {"protocolVersion": "2025-11-25"}))
        self.assertEqual("2025-11-25", initialized["result"]["protocolVersion"])
        listed = capability_mcp._dispatch(request("tools/list", {"_meta": {"progressToken": "x"}}))
        self.assertNotIn("resultType", listed["result"])
        self.assertEqual(9, len(listed["result"]["tools"]))

    def test_modern_stdio_wire_without_initialize(self):
        server = Path(capability_mcp.__file__)
        wire = "\n".join(json.dumps(request(method, {"_meta": MODERN_META}))
                         for method in ("server/discover", "tools/list")) + "\n"
        process = subprocess.run(
            [sys.executable, str(server)], input=wire, text=True,
            capture_output=True, timeout=5, check=True,
        )
        responses = [json.loads(line) for line in process.stdout.splitlines()]
        self.assertEqual(2, len(responses))
        self.assertEqual(["2026-07-28"], responses[0]["result"]["supportedVersions"])
        self.assertEqual(9, len(responses[1]["result"]["tools"]))
        self.assertEqual("", process.stderr)


if __name__ == "__main__":
    unittest.main()
