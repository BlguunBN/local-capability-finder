import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import capability_finder as finder
import capability_mcp


class CapabilityFinderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.library = self.home / "ai-agent-library"
        self.library.mkdir()
        self.skill = self.home / ".agents" / "skills" / "cad-viewer"
        self.skill.mkdir(parents=True)
        (self.skill / "SKILL.md").write_text(
            "---\nname: cad-viewer\ndescription: View CAD files\n---\n",
            encoding="utf-8",
        )
        (self.library / "skills_index.json").write_text(
            json.dumps(
                {
                    "skills": [
                        {
                            "name": "cad-viewer",
                            "description": "View CAD files",
                            "category": "cad",
                            "path": str(self.skill),
                            "skill_md": str(self.skill / "SKILL.md"),
                            "source": "agents",
                            "canonical": True,
                            "roots": ["agents"],
                        },
                        {
                            "name": "cad-viewer",
                            "description": "View CAD files",
                            "category": "cad",
                            "path": str(self.skill),
                            "skill_md": str(self.skill / "SKILL.md"),
                            "source": "claude",
                            "canonical": False,
                            "roots": ["claude"],
                        },
                    ]
                }
            ),
            encoding="utf-8",
        )
        (self.library / "mcp_servers.yaml").write_text(
            "mcp_servers:\n  playwright:\n    command: npx\n    args: ['@playwright/mcp']\n",
            encoding="utf-8",
        )
        plugin = (
            self.home
            / ".codex"
            / "plugins"
            / "cache"
            / "vendor"
            / "cad-plugin"
            / "1.0"
            / ".codex-plugin"
        )
        plugin.mkdir(parents=True)
        (plugin / "plugin.json").write_text(
            json.dumps(
                {"name": "cad-plugin", "version": "1.0", "description": "CAD tooling"}
            ),
            encoding="utf-8",
        )

    def test_refresh_search_show_and_dedupe(self):
        payload = finder.refresh_index(home=self.home, library=self.library)
        self.assertEqual(1, sum(x["type"] == "skill" for x in payload["capabilities"]))
        skills = finder.search(
            "view cad", kind="skill", home=self.home, library=self.library
        )
        self.assertEqual("cad-viewer", skills[0]["name"])
        self.assertLessEqual(len(skills), 3)
        self.assertEqual(
            {"id", "type", "name", "reason", "availability"}, set(skills[0])
        )
        self.assertEqual(
            str(self.skill / "SKILL.md"),
            finder.show(skills[0]["id"], home=self.home, library=self.library)[
                "source_path"
            ],
        )
        self.assertTrue(
            finder.search(
                "playwright", kind="tool", home=self.home, library=self.library
            )
        )
        self.assertTrue(
            finder.search(
                "cad-plugin", kind="plugin", home=self.home, library=self.library
            )
        )
        self.assertEqual(
            [], finder.search("qzxqzxunmatched", home=self.home, library=self.library)
        )

    def test_activation_uses_exact_id_and_never_overwrites(self):
        finder.refresh_index(home=self.home, library=self.library)
        cap_id = finder.search(
            "cad viewer", kind="skill", home=self.home, library=self.library
        )[0]["id"]
        with self.assertRaises(ValueError):
            finder.activate(
                "skill:cad-viewer", "codex", home=self.home, library=self.library
            )
        result = finder.activate(cap_id, "codex", home=self.home, library=self.library)
        self.assertEqual("activated", result["status"])
        self.assertTrue(
            (self.home / ".codex" / "skills" / "cad-viewer" / "SKILL.md").is_file()
        )
        again = finder.activate(cap_id, "codex", home=self.home, library=self.library)
        self.assertEqual("already_active", again["status"])

    def test_missing_source_is_not_activated(self):
        finder.refresh_index(home=self.home, library=self.library)
        cap_id = finder.search(
            "cad", kind="skill", home=self.home, library=self.library
        )[0]["id"]
        (self.skill / "SKILL.md").unlink()
        with self.assertRaises(FileNotFoundError):
            finder.activate(cap_id, "codex", home=self.home, library=self.library)

    def test_conflicting_skill_is_never_overwritten(self):
        finder.refresh_index(home=self.home, library=self.library)
        cap_id = finder.search(
            "cad viewer", kind="skill", home=self.home, library=self.library
        )[0]["id"]
        existing = self.home / ".codex" / "skills" / "cad-viewer"
        existing.mkdir(parents=True)
        (existing / "SKILL.md").write_text("different instructions", encoding="utf-8")
        with self.assertRaises(FileExistsError):
            finder.activate(cap_id, "codex", home=self.home, library=self.library)
        self.assertEqual(
            "different instructions",
            (existing / "SKILL.md").read_text(encoding="utf-8"),
        )

    def test_unrelated_skill_install_does_not_disable_search(self):
        finder.refresh_index(home=self.home, library=self.library)
        other = self.home / ".gemini" / "skills" / "unrelated"
        other.mkdir(parents=True)
        (other / "SKILL.md").write_text("unrelated", encoding="utf-8")
        self.assertEqual(
            "cad-viewer",
            finder.search("cad viewer", home=self.home, library=self.library)[0]["name"],
        )

    def test_autoclaw_is_an_exact_activation_target(self):
        finder.refresh_index(home=self.home, library=self.library)
        cap_id = finder.search(
            "cad viewer", kind="skill", home=self.home, library=self.library
        )[0]["id"]
        result = finder.activate(
            cap_id, "openclaw-autoclaw", home=self.home, library=self.library
        )
        self.assertEqual("activated", result["status"])
        self.assertTrue(
            (self.home / ".openclaw-autoclaw" / "skills" / "cad-viewer" / "SKILL.md").is_file()
        )

    def test_distinct_same_name_versions_are_inspectable(self):
        other = self.home / ".claude" / "skills" / "cad-viewer"
        other.mkdir(parents=True)
        (other / "SKILL.md").write_text(
            "different Claude instructions", encoding="utf-8"
        )
        path = self.library / "skills_index.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["skills"].append(
            {
                "name": "cad-viewer",
                "description": "Claude CAD",
                "category": "cad",
                "path": str(other),
                "skill_md": str(other / "SKILL.md"),
                "source": "claude",
                "roots": ["claude"],
            }
        )
        path.write_text(json.dumps(data), encoding="utf-8")
        finder.refresh_index(home=self.home, library=self.library)
        result = finder.search(
            "cad viewer", kind="skill", home=self.home, library=self.library
        )
        self.assertEqual(1, sum(row["name"] == "cad-viewer" for row in result))
        detail = finder.show(result[0]["id"], home=self.home, library=self.library)
        self.assertEqual(1, len(detail["alternate_versions"]))

    def test_bad_plugin_and_unavailable_mcp_server_do_not_break_refresh(self):
        bad = (
            self.home
            / ".codex"
            / "plugins"
            / "cache"
            / "vendor"
            / "broken"
            / "1"
            / ".codex-plugin"
        )
        bad.mkdir(parents=True)
        (bad / "plugin.json").write_text("{bad json", encoding="utf-8")
        (self.library / "mcp_servers.yaml").write_text(
            "mcp_servers:\n  unavailable:\n    command: definitely-not-installed\n",
            encoding="utf-8",
        )
        finder.refresh_index(home=self.home, library=self.library, probe_tools=True)
        self.assertEqual(
            "unavailable",
            finder.search(
                "unavailable", kind="tool", home=self.home, library=self.library
            )[0]["name"],
        )

    def test_skill_id_survives_edit_and_archive_move(self):
        finder.refresh_index(home=self.home, library=self.library)
        original = finder.search(
            "cad viewer", kind="skill", home=self.home, library=self.library
        )[0]["id"]
        (self.skill / "SKILL.md").write_text(
            "---\nname: cad-viewer\ndescription: View updated CAD files\n---\n",
            encoding="utf-8",
        )
        finder.refresh_index(home=self.home, library=self.library)
        self.assertEqual(
            original,
            finder.search(
                "cad viewer", kind="skill", home=self.home, library=self.library
            )[0]["id"],
        )
        archive = self.library / "on-demand"
        archive.mkdir()
        (archive / "pilot-manifest.json").write_text(
            json.dumps(
                {
                    "entries": [
                        {"name": "cad-viewer", "store_paths": [{"store": "agents"}]}
                    ]
                }
            ),
            encoding="utf-8",
        )
        archived = archive / "skills" / "cad-viewer"
        archived.parent.mkdir()
        self.skill.rename(archived)
        finder.refresh_index(home=self.home, library=self.library)
        self.assertEqual(
            original,
            finder.search(
                "cad viewer", kind="skill", home=self.home, library=self.library
            )[0]["id"],
        )
        self.assertEqual(
            ["on-demand"],
            finder.search(
                "cad viewer", kind="skill", home=self.home, library=self.library
            )[0]["availability"],
        )
        self.assertEqual(
            1,
            len(
                finder.show(original, home=self.home, library=self.library)["variants"]
            ),
        )

    def test_mcp_server_description_drives_intent_search(self):
        (self.library / "mcp_servers.yaml").write_text(
            "mcp_servers:\n  playwright:\n    command: npx\n    args: ['@playwright/mcp']\n"
            "    description: Browser automation for end-to-end testing\n",
            encoding="utf-8",
        )
        finder.refresh_index(home=self.home, library=self.library)
        hits = finder.search(
            "browser automation", kind="tool", home=self.home, library=self.library
        )
        self.assertTrue(hits)
        self.assertEqual("playwright", hits[0]["name"])

    def test_undescribed_mcp_server_is_findable_by_package_identifier(self):
        (self.library / "mcp_servers.yaml").write_text(
            "mcp_servers:\n  opaque:\n    command: npx\n    args: ['@acme/widget-observer-mcp']\n",
            encoding="utf-8",
        )
        finder.refresh_index(home=self.home, library=self.library)
        hits = finder.search(
            "widget observer", kind="tool", home=self.home, library=self.library
        )
        self.assertTrue(
            hits, "command/args identifiers must be searchable for undescribed servers"
        )
        self.assertEqual("opaque", hits[0]["name"])

    def test_omp_config_servers_are_indexed_and_disabled_ones_are_hidden(self):
        omp_dir = self.home / ".omp" / "agent"
        omp_dir.mkdir(parents=True)
        (omp_dir / "mcp.json").write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "capability-finder": {
                            "command": "python",
                            "args": ["capability_mcp.py"],
                            "description": "Search installed skills, MCP tools, and plugins",
                        },
                        "suppressed": {
                            "command": "python",
                            "args": ["suppressed_mcp.py"],
                            "description": "Suppressed MCP server",
                        },
                    },
                    "disabledServers": ["suppressed"],
                }
            ),
            encoding="utf-8",
        )
        finder.refresh_index(home=self.home, library=self.library)
        hits = finder.search(
            "search installed skills",
            kind="tool",
            home=self.home,
            library=self.library,
        )
        self.assertTrue(hits, "OMP-registered MCP servers must be searchable")
        self.assertEqual("capability-finder", hits[0]["name"])
        self.assertIn("omp", hits[0]["availability"])
        self.assertFalse(
            [
                item
                for item in finder.search(
                    "suppressed", kind="tool", home=self.home, library=self.library
                )
                if item["name"] == "suppressed"
            ],
            "disabledServers must hide a server regardless of its definition",
        )

    def test_mcp_advertises_three_tools(self):
        initialized = capability_mcp._dispatch(
            {
                "jsonrpc": "2.0",
                "id": 0,
                "method": "initialize",
                "params": {"protocolVersion": "unsupported-version"},
            }
        )
        self.assertEqual("2025-03-26", initialized["result"]["protocolVersion"])
        response = capability_mcp._dispatch(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
        )
        self.assertEqual(
            {"search_capabilities", "get_capability", "activate_skill"},
            {tool["name"] for tool in response["result"]["tools"]},
        )
        self.assertIsNone(
            capability_mcp._dispatch(
                {"jsonrpc": "2.0", "method": "notifications/initialized"}
            )
        )

    def test_mcp_wire_output_is_ascii_safe(self):
        """Non-ASCII hits must not kill the transport on a non-UTF-8 stdout.

        Windows pipes default stdout to the ANSI code page, so raw CJK text or
        arrows in a skill reason used to raise UnicodeEncodeError outside the
        dispatch try/except and kill the server mid-call.
        """
        response = capability_mcp._response(
            1, {"content": [{"type": "text", "text": "→ 한국어 ✓"}]}
        )
        wire = capability_mcp._encode(response)
        self.assertTrue(wire.isascii(), "wire bytes must stay ASCII for any stdout encoding")
        wire.encode("cp1252")  # must not raise on a legacy-codepage stdout
        self.assertEqual(
            "→ 한국어 ✓",
            json.loads(wire)["result"]["content"][0]["text"],
            "ASCII escaping must round-trip the original characters",
        )


if __name__ == "__main__":
    unittest.main()
