#!/usr/bin/env python3
"""Small, local capability index shared by the CLI and MCP adapter."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

try:
    import tomllib
except ImportError:  # pragma: no cover - supported Python is 3.11+
    tomllib = None

try:
    import yaml
except ImportError:
    yaml = None


DEFAULT_HOME = Path.home()
AGENT_DIRS = {
    "agents": ".agents/skills", "codex": ".codex/skills",
    "claude": ".claude/skills", "gemini": ".gemini/skills",
    "antigravity": ".gemini/config/skills",
    "openclaw": ".openclaw/skills", "commandcode": ".commandcode/skills",
    "openclaw-autoclaw": ".openclaw-autoclaw/skills",
    "opencode": ".config/opencode/skills", "cursor": ".cursor/skills",
    "pi": ".pi/agent/skills", "pi-legacy": ".pi/skills",
    "omp": ".omp/agent/skills", "omp-managed": ".omp/managed-skills",
    "cline": ".cline/skills", "roo": ".roo/skills",
    "hermes": ".hermes/skills", "kilocode": ".kilocode/skills",
    "trae": ".trae/skills", "continue": ".continue/skills",
    "aider-desk": ".aider-desk/skills",
}
TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)
STOPWORDS = {"a", "an", "the", "for", "with", "to", "of", "in", "on", "my", "i", "need", "find", "use", "skill", "skills", "tool", "tools", "plugin", "plugins", "no", "match", "make", "create", "build", "write", "fix", "search"}


def _paths(home: Path | None, library: Path | None) -> tuple[Path, Path]:
    home = Path(home or DEFAULT_HOME).resolve()
    return home, Path(library or home / "ai-agent-library").resolve()


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _id(kind: str, key: str) -> str:
    return f"{kind}:{hashlib.sha256(key.encode('utf-8')).hexdigest()[:16]}"


def _tokens(value: str) -> list[str]:
    normalized = unicodedata.normalize("NFKC", value).casefold().replace("_", "-").replace("-", " ")
    return [part for part in TOKEN_RE.findall(normalized) if part not in STOPWORDS]


def _short(value: str, limit: int = 115) -> str:
    return " ".join(str(value or "").split())[:limit]


def _has(term: str, words: list[str]) -> bool:
    return any(term == word or (len(term) >= 5 and word.startswith(term)) or
               (len(term) >= 2 and not term.isascii() and term in word) for word in words)


def _skill_entries(home: Path, library: Path) -> list[dict]:
    catalog = _read_json(library / "skills_index.json")
    rows = catalog.get("skills", []) if isinstance(catalog, dict) else catalog
    rows = rows if isinstance(rows, list) else []
    archive = library / "on-demand"
    archived_owners = {}
    if archive.is_dir():
        for manifest_path in archive.glob("*manifest.json"):
            archive_manifest = _read_json(manifest_path)
            if isinstance(archive_manifest, dict):
                for entry in archive_manifest.get("entries", []):
                    if isinstance(entry, dict) and entry.get("name") and entry.get("store_paths"):
                        archived_owners[str(entry["name"])] = str(entry["store_paths"][0]["store"])
    if archive.is_dir():
        for md in archive.rglob("SKILL.md"):
            try:
                content = md.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            name_match = re.search(r"^name:\s*(.+)$", content, re.MULTILINE)
            desc_match = re.search(r"^description:\s*(.+)$", content, re.MULTILINE)
            rows.append({"name": (name_match.group(1).strip() if name_match else md.parent.name),
                         "description": (desc_match.group(1).strip() if desc_match else ""),
                         "category": "on-demand", "path": str(md.parent), "skill_md": str(md),
                         "source": archived_owners.get(md.parent.name, "archive"), "canonical": True,
                         "roots": [archived_owners.get(md.parent.name, "archive")]})
    grouped: dict[tuple[str, str], dict] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        md = Path(str(row.get("skill_md", "")))
        if not md.is_file():
            continue
        name = str(row.get("name") or md.parent.name).strip().strip("\"'")
        if not name:
            continue
        try:
            key = (name.casefold(), os.path.normcase(str(md.parent.resolve())))
        except OSError:
            continue
        store = str(row.get("source", "library"))
        variant = {"store": store, "path": str(md.parent), "skill_md": str(md)}
        if key not in grouped:
            grouped[key] = {"id": "", "type": "skill",
                            "name": name, "description": _short(row.get("description", ""), 300),
                            "category": str(row.get("category", "")), "source_path": str(md),
                            "variants": [], "availability": [], "aliases": [], "quality": 0,
                            "owner_stores": [], "observed_active": []}
        item = grouped[key]
        for owner in row.get("roots", [store]):
            if owner not in item["owner_stores"]:
                item["owner_stores"].append(owner)
            if owner in AGENT_DIRS and archive not in md.parents and owner not in item["observed_active"]:
                item["observed_active"].append(owner)
        if not any(existing["path"] == variant["path"] for existing in item["variants"]):
            item["variants"].append(variant)
        if row.get("canonical") and store != "archive":
            item["source_path"] = str(md)
            item["quality"] = 1
    for item in grouped.values():
        item["variants"].sort(key=lambda v: (v["store"] == "archive", v["store"], v["path"]))
    # A matching SKILL.md alone does not prove that scripts or references match.
    for (name, _), item in grouped.items():
        owner_order = {"agents": 0, "library": 1, "gemini": 2, "claude": 3, "openclaw": 4, "codex": 5, "archive": 99}
        owner = min(item.pop("owner_stores"), key=lambda value: (owner_order.get(value, 50), value))
        if owner == "plugin-cache":
            owner = "codex"  # Preserve IDs created before plugin-cache had its own root label.
        item["id"] = _id("skill", f"{name}:{owner}")
        item["availability"] = item.pop("observed_active")
        for agent, subdir in AGENT_DIRS.items():
            md = home / subdir / item["name"] / "SKILL.md"
            try:
                if agent not in item["availability"] and md.is_file() and md.samefile(item["source_path"]):
                    item["availability"].append(agent)
            except OSError:
                pass
        if not item["availability"]:
            item["availability"] = ["on-demand"]
    # Rare same-name, same-owner variants still need distinct exact IDs.
    used_ids: set[str] = set()
    for item in sorted(grouped.values(), key=lambda value: (value["name"].casefold(), value["source_path"])):
        if item["id"] in used_ids:
            item["id"] = _id("skill", f"{item['id']}:{item['source_path'].casefold()}")
        used_ids.add(item["id"])
    return list(grouped.values())


def _mcp_configs(home: Path, library: Path) -> list[tuple[str, Path, dict]]:
    found: list[tuple[str, Path, dict]] = []
    locations = [("catalog", library / "mcp_servers.yaml", "yaml"),
                 ("codex", home / ".codex" / "config.toml", "toml"),
                 ("claude", home / ".claude.json", "json"),
                 ("gemini", home / ".gemini" / "settings.json", "json"),
                 ("antigravity", home / ".gemini" / "config" / "mcp_config.json", "json"),
                 ("cursor", home / ".cursor" / "mcp.json", "json"),
                 ("openclaw", home / ".openclaw" / "openclaw.json", "json"),
                 ("opencode", home / ".config" / "opencode" / "opencode.json", "json"),
                 ("opencode", home / "opencode.json", "json")]
    for agent, path, kind in locations:
        try:
            if kind == "yaml":
                if yaml is None:
                    # Keep the shared registry searchable without a third-party parser.
                    names = re.findall(r"^  ([A-Za-z0-9_-]+):\s*$", path.read_text(encoding="utf-8"), re.MULTILINE)
                    data = {"mcp_servers": {name: {} for name in names}}
                else:
                    data = yaml.safe_load(path.read_text(encoding="utf-8"))
            elif kind == "toml":
                data = tomllib.loads(path.read_text(encoding="utf-8")) if tomllib else {}
            else:
                data = _read_json(path)
        except Exception:  # one malformed agent config must not break the index
            continue
        if not isinstance(data, dict):
            continue
        servers = data.get("mcp_servers") or data.get("mcpServers") or data.get("mcp_servers_config")
        if not servers and isinstance(data.get("mcp"), dict):
            servers = data["mcp"] if agent == "opencode" else data["mcp"].get("servers")
        if isinstance(servers, dict):
            found.append((agent, path, servers))
    omp_locations = [home / ".omp" / "agent" / "mcp.json",
                     home / ".omp" / "agent" / ".mcp.json",
                     *sorted(home.glob(".omp/profiles/*/agent/mcp.json"))]
    for path in omp_locations:
        data = _read_json(path)
        if not isinstance(data, dict) or not isinstance(data.get("mcpServers"), dict):
            continue
        disabled_names = data.get("disabledServers", [])
        disabled = {name.casefold() for name in disabled_names if isinstance(name, str)} if isinstance(disabled_names, list) else set()
        servers = {name: config for name, config in data["mcpServers"].items()
                   if str(name).casefold() not in disabled}
        if servers:
            found.append(("omp", path, servers))
    # Hermes profiles can live outside ~/.hermes; ask the CLI for the active
    # config path instead of indexing a different profile's registrations.
    hermes = shutil.which("hermes")
    if hermes:
        try:
            result = subprocess.run([hermes, "config", "path"], capture_output=True,
                                    text=True, timeout=15, check=False)
            if result.returncode == 0:
                config_path = Path(result.stdout.strip())
                if yaml is not None and config_path.is_file():
                    data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
                    servers = data.get("mcp_servers") if isinstance(data, dict) else None
                    if isinstance(servers, dict):
                        found.append(("hermes", config_path, servers))
        except Exception:  # one malformed agent config must not break the index
            pass
    return found


def _probe_signature(config: dict) -> str:
    digest = hashlib.sha256(json.dumps(config, sort_keys=True, default=str).encode("utf-8"))
    args = config.get("args", [])
    values = [config.get("command"), *(args if isinstance(args, list) else [])]
    for index, value in enumerate(values):
        if not isinstance(value, str):
            continue
        path = Path(value)
        if index == 0 and not path.is_file():
            path = Path(shutil.which(value) or value)
        try:
            stat = path.stat()
        except OSError:
            continue
        if path.is_file():
            digest.update(f"{path.resolve()}:{stat.st_size}:{stat.st_mtime_ns}".encode("utf-8"))
    return digest.hexdigest()


def _mcp_identifiers(name: str, config: dict) -> list[str]:
    """Add safe package and host identifiers when a server name is opaque."""
    parts = []
    command = config.get("command")
    if isinstance(command, str):
        parts.append(Path(command).stem)
    url = config.get("url")
    if isinstance(url, str):
        try:
            parts.append(urlsplit(url).hostname or "")
        except ValueError:
            pass
    args = config.get("args")
    if isinstance(args, list):
        parts.extend(arg for arg in args if isinstance(arg, str) and "mcp" in arg.casefold()
                     and re.fullmatch(r"@?[\w-]+(?:/[\w-]+)?", arg))
    name_tokens = set(_tokens(name))
    return [token for token in dict.fromkeys(_tokens(" ".join(parts)))
            if len(token) >= 3 and token not in name_tokens and token != "mcp"]


def _tool_entries(home: Path, library: Path, probe_tools: bool) -> list[dict]:
    grouped: dict[str, dict] = {}
    catalog_signatures: dict[str, str] = {}
    for agent, path, servers in _mcp_configs(home, library):
        for name, config in servers.items():
            if not isinstance(config, dict):
                config = {}
            key = str(name).casefold()
            enabled = config.get("enabled") is not False and config.get("disabled") is not True
            if agent == "catalog" and enabled:
                catalog_signatures[key] = _probe_signature(config)
            item = grouped.setdefault(key, {"id": _id("tool", f"server:{key}"), "type": "tool",
                                            "name": str(name), "description": f"MCP server: {name}",
                                            "category": "mcp-server", "source_path": str(path),
                                            "variants": [], "availability": [], "aliases": [], "quality": 0})
            if agent != "catalog" and enabled and agent not in item["availability"]:
                item["availability"].append(agent)
            item["variants"].append({"store": agent, "path": str(path), "server": str(name),
                                     "enabled": enabled})
            if not item.get("description") or item["description"].startswith("MCP server:"):
                item["description"] = _short(config.get("description") or f"MCP server: {name}", 300)
            for alias in _mcp_identifiers(str(name), config):
                if alias not in item["aliases"]:
                    item["aliases"].append(alias)
            if probe_tools and agent == "catalog" and enabled:
                for tool in _probe_tools(config):
                    tool_name = str(tool.get("name", ""))
                    if not tool_name:
                        continue
                    tool_key = f"{key}:{tool_name.casefold()}"
                    grouped[tool_key] = {"id": _id("tool", tool_key), "type": "tool",
                                         "name": tool_name, "description": _short(tool.get("description", ""), 300),
                                         "category": f"mcp-tool/{name}", "source_path": str(path),
                                         "variants": [{"store": "catalog", "path": str(path), "server": str(name)}],
                                         "availability": item["availability"], "aliases": [str(name)],
                                         "config_fingerprint": catalog_signatures[key],
                                         "probed_at_epoch": int(time.time()), "quality": 1}
    previous = _read_json(library / "capabilities_index.json")
    if not probe_tools and isinstance(previous, dict):
        for old in previous.get("capabilities", []):
            if not isinstance(old, dict) or not str(old.get("category", "")).startswith("mcp-tool/") or not old.get("aliases"):
                continue
            server_key = str(old["aliases"][0]).casefold()
            signature = catalog_signatures.get(server_key)
            if (signature and old.get("config_fingerprint") == signature
                    and isinstance(old.get("probed_at_epoch"), (int, float))
                    and time.time() - old["probed_at_epoch"] < 7 * 86400
                    and server_key in grouped
                    and all(item["id"] != old.get("id") for item in grouped.values())):
                cached = dict(old)
                cached["availability"] = grouped[server_key]["availability"]
                grouped[f"cached:{old['id']}"] = cached
    return list(grouped.values())


def _probe_tools(config: dict) -> list[dict]:
    """Explicit refresh-only probe. Skip package runners and credentialed servers."""
    command = config.get("command")
    args = config.get("args", [])
    if not isinstance(command, str) or not isinstance(args, list) or config.get("env"):
        return []
    if Path(command).stem.casefold() in {"npx", "uvx", "bunx", "pnpm", "npm"}:
        return []
    try:
        messages = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "capability-finder", "version": "1"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        ]
        clean_env = {key: os.environ[key] for key in ("PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "USERPROFILE") if key in os.environ}
        result = subprocess.run([command, *map(str, args)], input="\n".join(json.dumps(m) for m in messages) + "\n",
                                capture_output=True, text=True, timeout=5, encoding="utf-8", errors="replace", env=clean_env)
        for line in result.stdout.splitlines():
            try:
                response = json.loads(line)
            except ValueError:
                continue
            if response.get("id") == 2:
                return response.get("result", {}).get("tools", [])[:200]
    except (OSError, subprocess.TimeoutExpired, ValueError):
        pass
    return []


def _plugin_entries(home: Path) -> list[dict]:
    cache = home / ".codex" / "plugins" / "cache"
    if not cache.is_dir():
        return []
    codex_config = home / ".codex" / "config.toml"
    enabled_names: set[str] | None = None
    if codex_config.is_file() and tomllib:
        try:
            config = tomllib.loads(codex_config.read_text(encoding="utf-8"))
            configured = config.get("plugins", {})
            if isinstance(configured, dict):
                enabled_names = {name.split("@", 1)[0].casefold() for name, value in
                                 configured.items() if isinstance(name, str) and isinstance(value, dict) and value.get("enabled") is True}
        except (OSError, ValueError, TypeError):
            pass
    grouped: dict[str, dict] = {}
    for manifest in cache.rglob(".codex-plugin/plugin.json"):
        data = _read_json(manifest)
        if not isinstance(data, dict) or not data.get("name"):
            continue
        name = str(data["name"])
        root = manifest.parent.parent
        version = str(data.get("version", root.name))
        key = name.casefold()
        if enabled_names is not None and key not in enabled_names:
            continue
        interface = data.get("interface") if isinstance(data.get("interface"), dict) else {}
        item = {"id": _id("plugin", key), "type": "plugin", "name": name,
                "description": _short(data.get("description") or interface.get("shortDescription", ""), 300),
                "category": str(interface.get("category", "plugin")),
                "source_path": str(manifest), "variants": [{"store": "codex", "path": str(root), "version": version}],
                "availability": ["codex"],
                "aliases": data.get("keywords", []) if isinstance(data.get("keywords"), list) else [],
                "quality": 1}
        version_key = tuple(int(part) for part in re.findall(r"\d+", version))
        previous_key = tuple(int(part) for part in re.findall(r"\d+", grouped[key]["variants"][0]["version"])) if key in grouped else ()
        if key not in grouped or version_key > previous_key:
            grouped[key] = item
    for name in enabled_names or ():
        if name not in grouped:
            grouped[name] = {"id": _id("plugin", name), "type": "plugin", "name": name,
                             "description": f"Enabled Codex plugin: {name}", "category": "plugin",
                             "source_path": str(codex_config), "variants": [],
                             "availability": ["codex"], "aliases": [], "quality": 0}
    return list(grouped.values())


def refresh_index(probe_tools: bool = False, home: Path | None = None, library: Path | None = None) -> dict:
    home, library = _paths(home, library)
    items = _skill_entries(home, library) + _tool_entries(home, library, probe_tools) + _plugin_entries(home)
    items.sort(key=lambda x: (x["type"], x["name"].casefold(), x["id"]))
    payload = {"schema": 1, "indexed_at_epoch": time.time(),
               "active_skill_stores": [agent for agent, folder in AGENT_DIRS.items()
                                       if (home / folder).is_dir()], "watched_paths": [
        {"path": str(path), "mtime_ns": _mtime(path)} for path in _watch_paths(home, library, items)
    ], "capabilities": items}
    library.mkdir(parents=True, exist_ok=True)
    dest = library / "capabilities_index.json"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=library, prefix=".capabilities-", suffix=".json", delete=False) as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
        tmp = Path(fh.name)
    os.replace(tmp, dest)
    _read_index.cache_clear()
    return payload


def _watch_paths(home: Path, library: Path, items: list[dict]) -> list[Path]:
    paths = {library / "skills_index.json", library / "mcp_servers.yaml",
             home / ".codex" / "config.toml", home / ".claude.json",
             home / ".gemini" / "settings.json", home / ".gemini" / "config" / "mcp_config.json",
             home / ".cursor" / "mcp.json", home / ".openclaw" / "openclaw.json",
             home / ".config" / "opencode" / "opencode.json", home / "opencode.json",
             home / ".omp" / "agent" / "mcp.json", home / ".omp" / "agent" / ".mcp.json"}
    for item in items:
        if item["type"] in ("tool", "plugin"):
            if item.get("source_path"):
                source = Path(item["source_path"])
                if source.is_file():
                    paths.add(source)
    return sorted(paths)


def _mtime(path: Path) -> int | None:
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return None


@lru_cache(maxsize=4)
def _read_index(path: Path, mtime_ns: int, size: int) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise ValueError(f"capability index is unreadable: {path}; run: capability_finder.py refresh") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("capabilities"), list):
        raise ValueError(f"capability index is malformed: {path}; run: capability_finder.py refresh")
    return payload


def _load(home: Path | None, library: Path | None) -> dict:
    _, library = _paths(home, library)
    path = library / "capabilities_index.json"
    try:
        stat = path.stat()
    except FileNotFoundError as exc:
        raise FileNotFoundError("capability index missing; run: library_catalog.py") from exc
    payload = _read_index(path, stat.st_mtime_ns, stat.st_size)
    watched = payload.get("watched_paths")
    if isinstance(watched, list):
        for entry in watched:
            if isinstance(entry, dict) and isinstance(entry.get("path"), str):
                if _mtime(Path(entry["path"])) != entry.get("mtime_ns"):
                    raise ValueError("capability catalog is stale; run: library_catalog.py")
    elif _mtime(library / "skills_index.json") and _mtime(library / "skills_index.json") > stat.st_mtime_ns:
        raise ValueError("capability catalog is stale; run: library_catalog.py")
    return payload


def search(query: str, kind: str | None = None, limit: int = 3, home: Path | None = None, library: Path | None = None) -> list[dict]:
    if kind not in (None, "skill", "tool", "plugin"):
        raise ValueError("kind must be skill, tool, or plugin")
    if not 1 <= limit <= 20:
        raise ValueError("limit must be 1..20")
    terms = list(dict.fromkeys(_tokens(query)))
    if not terms:
        return []
    catalog = _load(home, library)
    candidates = [item for item in catalog["capabilities"] if not kind or item["type"] == kind]
    active_stores = set(catalog.get("active_skill_stores", []))
    tokenized = []
    frequencies = {term: 0 for term in terms}
    for item in candidates:
        name = _tokens(item["name"])
        category = _tokens(item.get("category", ""))
        desc = _tokens(item.get("description", ""))
        aliases = _tokens(" ".join(map(str, item.get("aliases", []))))
        matches = [tuple(_has(term, field) for field in (name, category, desc, aliases)) for term in terms]
        tokenized.append((item, name, matches))
        for term, matched in zip(terms, matches):
            if any(matched):
                frequencies[term] += 1
    scored = []
    for item, name, matches in tokenized:
        hits = sum(any(matched) for matched in matches)
        name_hit = any(matched[0] for matched in matches)
        alias_hit = any(matched[3] for matched in matches)
        if not name_hit and (hits < math.ceil(len(terms) / 3) or (len(terms) > 1 and hits == 1 and not alias_hit)):
            continue
        score = sum((1 + math.log((len(candidates) + 1) / (frequencies[term] + 1))) *
                    sum(weight for weight, found in zip((8, 2, 1, 3), matched) if found)
                    for term, matched in zip(terms, matches))
        score += 8 * hits / len(terms) + item.get("quality", 0)
        if item["type"] == "skill":
            score += 1
        if name == terms:
            score += 12
        score *= (hits / len(terms)) ** 2
        reason = _short(item.get("description") or item.get("category") or "Local capability", 90)
        available = item["availability"]
        if item["type"] == "skill" and len(active_stores) > 1 and active_stores <= set(available):
            available = ["all"]
        elif item["type"] == "skill" and len(active_stores) > 1 and len(available) >= 6:
            missing = active_stores - set(available)
            if len(missing) <= 2:
                available = ["all active except " + ", ".join(sorted(missing))]
        scored.append((score, item["name"].casefold(), {"id": item["id"], "type": item["type"],
                                                     "name": item["name"], "reason": reason,
                                                     "availability": available}))
    best: dict[str, tuple[float, str, dict]] = {}
    for entry in scored:
        score, name, row = entry
        previous = best.get(name)
        if previous is None or (score, row["type"] == "skill") > (previous[0], previous[2]["type"] == "skill"):
            best[name] = entry
    return [row for _, _, row in sorted(best.values(), key=lambda x: (-x[0], x[1]))[:limit]]


def show(capability_id: str, home: Path | None = None, library: Path | None = None) -> dict | None:
    capabilities = _load(home, library)["capabilities"]
    for item in capabilities:
        if item["id"] == capability_id:
            detail = dict(item)
            if item["type"] == "skill":
                detail["usage"] = f"Read the SKILL.md at {item['source_path']}; activate by exact ID only if this agent requires an active skill folder."
                detail["alternate_versions"] = [
                    {"id": other["id"], "source_path": other["source_path"], "availability": other["availability"]}
                    for other in capabilities
                    if other["type"] == "skill" and other["name"].casefold() == item["name"].casefold() and other["id"] != item["id"]
                ]
            elif item["type"] == "tool":
                detail["usage"] = "Use the named MCP server/tool if configured for this agent; source_path identifies its local config or discovery CLI."
            else:
                detail["usage"] = "Use through the installed Codex plugin; source_path identifies its manifest."
            return detail
    return None


def activate(capability_id: str, agent: str, home: Path | None = None, library: Path | None = None) -> dict:
    home, library = _paths(home, library)
    item = show(capability_id, home, library)
    if item is None or item["type"] != "skill":
        raise ValueError("activate requires an exact skill ID from search")
    if agent not in AGENT_DIRS:
        raise ValueError(f"unknown agent: {agent}")
    variants = item["variants"]
    chosen = next((v for v in variants if v["store"] == agent), variants[0])
    src = Path(chosen["path"])
    if not (src / "SKILL.md").is_file():
        raise FileNotFoundError(f"skill source unavailable: {src}")
    name = item["name"]
    if not name or name in (".", "..") or "/" in name or "\\" in name or Path(name).name != name:
        raise ValueError(f"invalid skill folder name: {name!r}")
    target_root = home / AGENT_DIRS[agent]
    target_root.mkdir(parents=True, exist_ok=True)
    target = target_root / name
    if target.exists() or target.is_symlink():
        if target.resolve() == src.resolve():
            return {"status": "already_active", "id": capability_id, "agent": agent, "path": str(target)}
        raise FileExistsError(f"refusing to overwrite existing skill: {target}")
    try:
        os.symlink(src, target, target_is_directory=True)
    except FileExistsError:
        if target.resolve() == src.resolve():
            return {"status": "already_active", "id": capability_id, "agent": agent, "path": str(target)}
        raise FileExistsError(f"refusing to overwrite existing skill: {target}")
    except OSError:
        if os.name == "nt":
            command = ("$data = [Console]::In.ReadToEnd() | ConvertFrom-Json; "
                       "Set-Location -LiteralPath $data.parent; "
                       "New-Item -ItemType Junction -Path . -Name $data.name "
                       "-Target $data.source -ErrorAction Stop | Out-Null")
            result = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
                input=json.dumps({"parent": str(target_root), "name": name, "source": str(src)}),
                capture_output=True, text=True, encoding="utf-8", errors="replace")
            if result.returncode != 0 or not target.is_dir() or target.resolve() != src.resolve():
                raise OSError(result.stderr.strip() or "junction creation failed")
        else:
            raise
    refresh_index(home=home, library=library)
    return {"status": "activated", "id": capability_id, "agent": agent, "path": str(target)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Find installed skills, MCP tools, and plugins locally")
    sub = parser.add_subparsers(dest="command", required=True)
    refresh = sub.add_parser("refresh")
    refresh.add_argument("--probe-tools", action="store_true", help="probe local command MCP servers (5s timeout each)")
    find = sub.add_parser("search")
    find.add_argument("query")
    find.add_argument("--kind", choices=("skill", "tool", "plugin"))
    find.add_argument("--limit", type=int, default=3)
    find.add_argument("--json", action="store_true")
    detail = sub.add_parser("show")
    detail.add_argument("id")
    action = sub.add_parser("activate")
    action.add_argument("id")
    action.add_argument("--agent", required=True, choices=sorted(AGENT_DIRS))
    args = parser.parse_args(argv)
    try:
        if args.command == "refresh":
            data = refresh_index(args.probe_tools)
            print(json.dumps({"indexed": len(data["capabilities"]), "path": str(_paths(None, None)[1] / "capabilities_index.json")}))
        elif args.command == "search":
            rows = search(args.query, args.kind, args.limit)
            if args.json:
                print(json.dumps(rows, ensure_ascii=False))
            else:
                for row in rows:
                    print(f"{row['id']}  {row['name']} [{row['type']}]  {row['reason']}")
        elif args.command == "show":
            item = show(args.id)
            if item is None:
                raise ValueError("capability ID not found")
            print(json.dumps(item, ensure_ascii=False, indent=2))
        else:
            print(json.dumps(activate(args.id, args.agent), ensure_ascii=False))
        return 0
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
