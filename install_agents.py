#!/usr/bin/env python3
"""Register the local capability finder MCP server with installed agents."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import tomllib
from datetime import datetime
from pathlib import Path


NAME = "local-capability-finder"
AGENTS = ("codex", "claude", "gemini", "antigravity", "hermes", "cursor", "opencode", "omp")
ROOT = Path(__file__).resolve().parent
SERVER = Path(os.environ.get("CAPFIND_SERVER_PATH", ROOT / "capability_mcp.py")).expanduser().resolve()


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict):
        raise ValueError(f"expected JSON object in {path}")
    return data


def _json_entry(path: Path, section: str) -> dict | None:
    group = _read_json(path).get(section, {})
    if not isinstance(group, dict):
        raise ValueError(f"expected {section} object in {path}")
    return group.get(NAME)


def _write_json_entry(path: Path, section: str, entry: dict) -> tuple[Path, Path | None, bytes]:
    if path.is_symlink():
        raise ValueError(f"refusing to replace symlinked config: {path}")
    data = _read_json(path)
    group = data.setdefault(section, {})
    if not isinstance(group, dict) or NAME in group:
        raise ValueError(f"cannot add {NAME} in {path}")
    group[NAME] = entry
    path.parent.mkdir(parents=True, exist_ok=True)
    backup = None
    if path.exists():
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        backup = path.with_name(f"{path.name}.bak-{stamp}")
        if backup.exists():
            raise FileExistsError(backup)
        shutil.copy2(path, backup)
    written = (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    with tempfile.NamedTemporaryFile("wb", dir=path.parent,
                                     prefix=f".{path.name}.", suffix=".tmp", delete=False) as tmp:
        tmp.write(written)
        temporary = Path(tmp.name)
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return path, backup, written


def _rollback_json_entry(change: tuple[Path, Path | None, bytes]) -> None:
    """Undo our write only if no other process changed the file meanwhile."""
    path, backup, written = change
    if path.read_bytes() != written:
        raise RuntimeError(f"cannot safely roll back {path}; it changed after installation")
    if backup is None:
        path.unlink()
        return
    with tempfile.NamedTemporaryFile("wb", dir=path.parent, prefix=f".{path.name}.",
                                     suffix=".tmp", delete=False) as tmp:
        temporary = Path(tmp.name)
    try:
        shutil.copy2(backup, temporary)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _run(args: list[str], timeout: int = 60,
         input_text: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, input=input_text, capture_output=True, text=True,
                          timeout=timeout, check=False)


def _same_path(actual: object, expected: Path) -> bool:
    return isinstance(actual, str) and os.path.normcase(str(Path(actual).resolve())) == os.path.normcase(str(expected.resolve()))


TOOL_NAMES = ("search_capabilities", "get_capability", "activate_skill")
_SERVED: dict[str, bool] = {}


def _interpreter_serves(interpreter: str) -> bool:
    """True when `interpreter` can launch SERVER and advertise its three tools.

    Registration is verified functionally rather than against sys.executable:
    any working Python can run the server, so comparing against whichever
    interpreter happens to run this script reports a healthy setup as broken
    whenever the two differ. Results are cached per interpreter path.
    """
    key = os.path.normcase(str(Path(interpreter).resolve()))
    if key not in _SERVED:
        payload = "".join(
            json.dumps(message) + "\n"
            for message in (
                {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            )
        )
        try:
            result = _run([interpreter, str(SERVER)], timeout=30, input_text=payload)
            _SERVED[key] = False
            if result.returncode == 0:
                for line in result.stdout.splitlines():
                    try:
                        reply = json.loads(line)
                    except ValueError:
                        continue
                    if not isinstance(reply, dict) or reply.get("id") != 2:
                        continue
                    body = reply.get("result")
                    tools = body.get("tools", []) if isinstance(body, dict) else []
                    names = {tool.get("name") for tool in tools if isinstance(tool, dict)} if isinstance(tools, list) else set()
                    _SERVED[key] = all(name in names for name in TOOL_NAMES)
                    break
        except (OSError, subprocess.TimeoutExpired):
            _SERVED[key] = False
    return _SERVED[key]


def _registered(agent: str, home: Path, cli: str | None) -> bool:
    entry = None
    if agent == "codex":
        path = home / ".codex" / "config.toml"
        if path.exists():
            data = tomllib.loads(path.read_text(encoding="utf-8-sig"))
            servers = data.get("mcp_servers", {})
            if not isinstance(servers, dict):
                raise ValueError(f"expected mcp_servers object in {path}")
            entry = servers.get(NAME)
    elif agent == "claude":
        entry = _json_entry(home / ".claude.json", "mcpServers")
    elif agent == "gemini":
        entry = _json_entry(home / ".gemini" / "settings.json", "mcpServers")
    elif agent == "antigravity":
        entry = _json_entry(home / ".gemini" / "config" / "mcp_config.json", "mcpServers")
    elif agent == "cursor":
        entry = _json_entry(home / ".cursor" / "mcp.json", "mcpServers")
    elif agent == "opencode":
        entry = _json_entry(home / ".config" / "opencode" / "opencode.json", "mcp")
    elif agent == "omp":
        path = home / ".omp" / "agent" / "mcp.json"
        data = _read_json(path)
        disabled = data.get("disabledServers", [])
        if isinstance(disabled, list) and NAME.casefold() in {str(name).casefold() for name in disabled}:
            raise RuntimeError("omp registration is disabled")
        entry = _json_entry(path, "mcpServers")
    elif agent == "hermes":
        result = _run([cli or "hermes", "config", "get", "--json",
                       f"mcp_servers.{NAME}"], timeout=20)
        if result.returncode:
            if "Config key not set" in result.stdout + result.stderr:
                return False
            raise RuntimeError("could not read Hermes MCP registration")
        entry = json.loads(result.stdout)
    else:
        raise ValueError(agent)
    if entry is None:
        return False
    if not isinstance(entry, dict) or entry.get("enabled") is False or entry.get("disabled") is True:
        raise RuntimeError(f"{agent} registration exists but is disabled or malformed")
    command = entry.get("command")
    if agent == "opencode":
        interpreter, server_arg = (command if isinstance(command, list) and len(command) == 2
                                   else (None, None))
        matching = entry.get("type") == "local"
    else:
        interpreter = command
        arguments = entry.get("args")
        server_arg = arguments[0] if isinstance(arguments, list) and len(arguments) == 1 else None
        matching = True
    matching = bool(
        matching
        and isinstance(interpreter, str)
        and isinstance(server_arg, str)
        and _same_path(server_arg, SERVER)
        and _interpreter_serves(interpreter)
    )
    if not matching:
        raise RuntimeError(f"{agent} registration exists but does not launch {SERVER}")
    if agent == "hermes":
        check = _run([cli or "hermes", "mcp", "test", NAME], timeout=30)
        tool_output = check.stdout + check.stderr
        if check.returncode or not all(name in tool_output for name in TOOL_NAMES):
            raise RuntimeError("Hermes registration exists but its tools are unavailable")
    return True


def _install(agent: str, home: Path, cli: str | None) -> tuple[Path, Path | None, bytes] | None:
    if agent == "omp":
        return _write_json_entry(home / ".omp" / "agent" / "mcp.json", "mcpServers",
                                 {"command": sys.executable, "args": [str(SERVER)]})
    if agent == "antigravity":
        return _write_json_entry(home / ".gemini" / "config" / "mcp_config.json", "mcpServers",
                                 {"command": sys.executable, "args": [str(SERVER)]})
    if agent == "cursor":
        return _write_json_entry(home / ".cursor" / "mcp.json", "mcpServers",
                                 {"command": sys.executable, "args": [str(SERVER)]})
    if agent == "opencode":
        return _write_json_entry(home / ".config" / "opencode" / "opencode.json", "mcp",
                                 {"type": "local", "command": [sys.executable, str(SERVER)], "enabled": True})
    commands = {
        "codex": ["mcp", "add", NAME, "--", sys.executable, str(SERVER)],
        "claude": ["mcp", "add", "--scope", "user", NAME, "--", sys.executable, str(SERVER)],
        "gemini": ["mcp", "add", "--scope", "user", NAME, sys.executable, str(SERVER)],
        "hermes": ["mcp", "add", NAME, "--command", sys.executable,
                   "--connect-timeout", "5", "--args", str(SERVER)],
    }
    result = _run([cli or agent, *commands[agent]],
                  input_text="y\n" if agent == "hermes" else None)
    if result.returncode:
        raise RuntimeError(f"{agent} registration failed (exit {result.returncode})")
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agents", nargs="+", choices=AGENTS, default=AGENTS,
                        help="clients to configure; default: all supported installed clients")
    parser.add_argument("--dry-run", action="store_true", help="show changes without writing")
    parser.add_argument("--skip-refresh", action="store_true", help="do not refresh local indexes")
    args = parser.parse_args()
    if not (ROOT / "capability_mcp.py").is_file():
        parser.error(f"MCP server missing: {ROOT / 'capability_mcp.py'}")
    if not args.dry_run and not SERVER.is_file():
        parser.error(f"MCP server missing: {SERVER}")
    if not args.dry_run and not args.skip_refresh:
        result = _run([sys.executable, str(ROOT / "library_catalog.py"),
                       "--if-stale", "12", "--quiet"], timeout=180)
        if result.returncode:
            raise RuntimeError("local catalog refresh failed")
    home = Path.home()
    failed = False
    for agent in dict.fromkeys(args.agents):
        cli = shutil.which(agent)
        config_dir = {"cursor": home / ".cursor", "opencode": home / ".config" / "opencode",
                      "antigravity": home / ".gemini" / "config",
                      "omp": home / ".omp" / "agent"}.get(agent)
        if not cli and not (config_dir and config_dir.is_dir()):
            print(f"{agent}: skipped (client not installed)")
            continue
        change = None
        try:
            if _registered(agent, home, cli):
                print(f"{agent}: already registered; left unchanged")
                continue
            if args.dry_run:
                print(f"{agent}: would register {NAME}")
                continue
            change = _install(agent, home, cli)
            if not _registered(agent, home, cli):
                raise RuntimeError("registration could not be verified")
            print(f"{agent}: registered {NAME}")
        except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
            failed = True
            if change is not None:
                try:
                    _rollback_json_entry(change)
                except (OSError, RuntimeError) as rollback_error:
                    print(f"{agent}: rollback failed ({rollback_error})", file=sys.stderr)
            print(f"{agent}: failed ({error})", file=sys.stderr)
    if not args.dry_run and not args.skip_refresh:
        result = _run([sys.executable, str(ROOT / "capability_finder.py"), "refresh"], timeout=60)
        if result.returncode:
            failed = True
            print("capability index refresh failed", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
