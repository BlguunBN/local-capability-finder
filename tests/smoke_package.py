"""Install a packed or published npm artifact, then exercise CLI and MCP stdio."""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from benchmark_queries import create_fixture


ROOT = Path(__file__).resolve().parents[1]


def run(*command: str, env: dict[str, str] | None = None) -> str:
    result = subprocess.run(command, cwd=ROOT, env=env, capture_output=True,
                            text=True, encoding="utf-8", timeout=90, check=True)
    return result.stdout


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--published", action="store_true",
                        help="install this checkout's version from the public npm registry")
    args = parser.parse_args()
    npm = shutil.which("npm.cmd" if os.name == "nt" else "npm")
    node = shutil.which("node")
    if not npm or not node:
        raise RuntimeError("Node.js and npm are required")
    with tempfile.TemporaryDirectory() as temporary:
        base = Path(temporary)
        prefix = base / "installed"
        if args.published:
            version = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))["version"]
            artifact = f"local-capability-finder-cli@{version}"
            install_source = artifact
        else:
            packed = json.loads(run(npm, "pack", "--json", "--pack-destination", str(base)))
            tarball = base / packed[0]["filename"]
            artifact = tarball.name
            install_source = str(tarball)
        run(npm, "install", "--prefix", str(prefix), "--ignore-scripts",
            "--no-audit", "--no-fund", "--registry=https://registry.npmjs.org",
            install_source)
        package = prefix / "node_modules" / "local-capability-finder-cli"
        cli = package / "bin" / "capfind.js"
        if not cli.is_file() or (package / ".git").exists():
            raise AssertionError("npm artifact was not installed cleanly")

        home = base / "home"
        home.mkdir()
        create_fixture(home)
        env = os.environ.copy()
        env.update({"HOME": str(home), "USERPROFILE": str(home),
                    "LOCALAPPDATA": str(base / "appdata"), "CAPFIND_PYTHON": sys.executable})
        refreshed = json.loads(run(node, str(cli), "refresh", env=env))
        if refreshed["indexed"] < 8:
            raise AssertionError(f"fixture index is incomplete: {refreshed}")
        matches = json.loads(run(node, str(cli), "search", "inspect STEP CAD file",
                                 "--kind", "skill", "--json", env=env))
        if not matches or matches[0]["name"] != "cad-viewer":
            raise AssertionError(f"packaged CLI search failed: {matches}")

        requests = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2025-11-25", "capabilities": {},
                        "clientInfo": {"name": "package-smoke", "version": "1"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
             "params": {"name": "search_capabilities",
                        "arguments": {"query": "inspect STEP CAD file", "kind": "skill"}}},
        ]
        modern_meta = {
            "io.modelcontextprotocol/protocolVersion": "2026-07-28",
            "io.modelcontextprotocol/clientCapabilities": {},
            "io.modelcontextprotocol/clientInfo": {"name": "package-smoke", "version": "1"},
        }
        requests.extend([
            {"jsonrpc": "2.0", "id": 4, "method": "server/discover",
             "params": {"_meta": modern_meta}},
            {"jsonrpc": "2.0", "id": 5, "method": "tools/list",
             "params": {"_meta": modern_meta}},
            {"jsonrpc": "2.0", "id": 6, "method": "tools/call",
             "params": {"name": "search_capabilities",
                        "arguments": {"query": "inspect STEP CAD file", "kind": "skill"},
                        "_meta": modern_meta}},
        ])
        wire = "\n".join(json.dumps(row) for row in requests) + "\n"
        result = subprocess.run([node, str(cli), "mcp"], cwd=base, env=env, input=wire,
                                capture_output=True, text=True, encoding="utf-8",
                                timeout=20, check=True)
        replies = [json.loads(line) for line in result.stdout.splitlines()]
        by_id = {row.get("id"): row for row in replies}
        if len(replies) != 6 or set(by_id) != {1, 2, 3, 4, 5, 6}:
            raise AssertionError(f"invalid MCP wire replies: {result.stdout!r}; {result.stderr!r}")
        if by_id[1]["result"]["serverInfo"]["name"] != "local-capability-finder":
            raise AssertionError("MCP initialize failed")
        names = {tool["name"] for tool in by_id[2]["result"]["tools"]}
        if "search_capabilities" not in names or "get_capability" not in names:
            raise AssertionError(f"MCP tool discovery failed: {names}")
        content = json.loads(by_id[3]["result"]["content"][0]["text"])
        if not content or content[0]["name"] != "cad-viewer":
            raise AssertionError(f"MCP search failed: {content}")
        if by_id[4]["result"]["supportedVersions"] != ["2026-07-28"]:
            raise AssertionError("modern MCP discovery failed")
        if {tool["name"] for tool in by_id[5]["result"]["tools"]} != names:
            raise AssertionError("modern MCP tool discovery failed")
        modern_content = json.loads(by_id[6]["result"]["content"][0]["text"])
        if not modern_content or modern_content[0]["name"] != "cad-viewer":
            raise AssertionError(f"modern MCP search failed: {modern_content}")
        installed_version = json.loads((package / "package.json").read_text(encoding="utf-8"))["version"]
        expected_version = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))["version"]
        if installed_version != expected_version:
            raise AssertionError(f"installed version {installed_version} != checkout {expected_version}")
        print(f"Packaged CLI and both MCP protocols passed: {artifact}, {len(names)} tools")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
