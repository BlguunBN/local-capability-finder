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
class CapError(ValueError):
    """Structured contract error: ValueError-compatible message plus a stable code."""
    CODES = ("NOT_FOUND", "AMBIGUOUS_ID", "UNTRUSTED", "UNSUPPORTED_AGENT",
             "CONFLICT", "INVALID_PATH", "STALE_REVISION", "PERMISSION_DENIED",
             "ROLLBACK_FAILED", "EXECUTION_FAILED")

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)
STOPWORDS = {"a", "an", "the", "for", "with", "to", "of", "in", "on", "my", "i", "need", "find", "use", "skill", "skills", "tool", "tools", "plugin", "plugins", "no", "match", "make", "create", "build", "write", "fix", "search"}


def _paths(home: Path | None, library: Path | None) -> tuple[Path, Path]:
    home = Path(home or os.environ.get("CAPFIND_HOME") or DEFAULT_HOME).expanduser().resolve()
    override = os.environ.get("CAPFIND_LIBRARY")
    return home, Path(library or override or home / "ai-agent-library").expanduser().resolve()


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
    if not isinstance(catalog, dict) or not isinstance(catalog.get("skills"), list):
        raise ValueError(f"skill catalog is malformed: {library / 'skills_index.json'}; run: library_catalog.py")
    rows = catalog["skills"]
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
                            "owner_stores": [], "observed_active": [],
                            "origin_store": store, "version": None, "tags": [],
                            "compatible_agents": [], "requirements": [], "row_hash": None}
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
            item["origin_store"] = store
            for field in ("version", "tags", "compatible_agents", "requirements"):
                if row.get(field) is not None:
                    item[field] = row[field]
            if row.get("content_hash"):
                item["row_hash"] = row["content_hash"]
    for item in grouped.values():
        item["variants"].sort(key=lambda v: (v["store"] == "archive", v["store"], v["path"]))
    # A matching SKILL.md alone does not prove that scripts or references match.
    for (name, _), item in grouped.items():
        owner_order = {"agents": 0, "library": 1, "gemini": 2, "claude": 3, "openclaw": 4, "codex": 5, "archive": 99}
        owner = min(item.pop("owner_stores"), key=lambda value: (owner_order.get(value, 50), value))
        if owner == "plugin-cache":
            owner = "codex"  # Preserve IDs created before plugin-cache had its own root label.
        item["legacy_id"] = _id("skill", f"{name}:{owner}")
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
    used_legacy: set[str] = set()
    for item in sorted(grouped.values(), key=lambda value: (value["name"].casefold(), value["source_path"])):
        legacy = item["legacy_id"]
        if legacy in used_legacy:
            legacy = _id("skill", f"{legacy}:{item['source_path'].casefold()}")
        used_legacy.add(legacy)
        item["legacy_id"] = legacy
    _assign_canonical_ids(grouped, home, library)
    return list(grouped.values())


def _content_hash(path: str) -> str | None:
    try:
        digest = hashlib.sha256()
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return "sha256:" + digest.hexdigest()
    except OSError:
        return None


def _registered_source_map(library: Path) -> dict[str, dict]:
    try:
        from library_catalog import load_sources
    except ImportError:
        return {}
    try:
        return {s["id"]: s for s in load_sources(str(library)) if isinstance(s, dict)}
    except (OSError, ValueError):
        return {}


def _source_trust(library: Path) -> dict[str, str]:
    trust = {store: "approved" for store in AGENT_DIRS}
    trust.update({"library": "approved", "catalog": "approved",
                  "plugin-cache": "approved", "archive": "approved"})
    for source_id, source in _registered_source_map(library).items():
        trust[source_id] = source.get("trust", "unscanned")
    return trust


def _lock_path(library: Path) -> Path:
    return library / "activations-lock.json"


def _read_lock(library: Path) -> dict:
    data = _read_json(_lock_path(library))
    if isinstance(data, dict) and isinstance(data.get("entries"), list):
        return data
    return {"schema_version": 1, "entries": []}


def _write_lock(library: Path, lock: dict) -> None:
    dest = _lock_path(library)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=dest.parent,
                                     prefix=".activations-lock-", suffix=".json", delete=False) as fh:
        json.dump(lock, fh, ensure_ascii=False, indent=2)
        tmp = Path(fh.name)
    os.replace(tmp, dest)


def _upsert_lock(library: Path, entry: dict) -> None:
    lock = _read_lock(library)
    lock["entries"] = [e for e in lock["entries"]
                       if not (isinstance(e, dict) and e.get("id") == entry["id"]
                               and e.get("agent") == entry["agent"])]
    lock["entries"].append(entry)
    _write_lock(library, lock)


def _remove_lock(library: Path, ids: tuple[str, ...], agent: str) -> None:
    lock = _read_lock(library)
    lock["entries"] = [e for e in lock["entries"]
                       if not (isinstance(e, dict) and e.get("id") in ids
                               and e.get("agent") == agent)]
    _write_lock(library, lock)


def _assign_canonical_ids(grouped: dict, home: Path, library: Path) -> None:
    """Stable `<source>:skill:<name>` identities; content changes hash, not ID."""
    registered = _registered_source_map(library)
    trust = _source_trust(library)
    order = {"agents": 0, "library": 1, "gemini": 2, "claude": 3,
             "openclaw": 4, "codex": 5, "archive": 99}
    buckets: dict[tuple[str, str], list[dict]] = {}
    for item in grouped.values():
        stores = {v["store"] for v in item["variants"]}
        origin = item.get("origin_store")
        if origin not in stores:
            origin = min(stores, key=lambda s: (registered.get(s, {}).get("priority",
                                 order.get(s, 50)), s))
        item["source_id"] = origin
        item["schema_version"] = 1
        item["trust_status"] = trust.get(origin, "unscanned")
        try:
            archived = (library / "on-demand") in Path(item["source_path"]).parents
        except (OSError, ValueError):
            archived = False
        item["installation_state"] = "archived" if archived else "available"
        buckets.setdefault((origin, item["name"]), []).append(item)
    for (origin, name), members in buckets.items():
        members.sort(key=lambda m: m["source_path"])
        for index, item in enumerate(members):
            base = f"{origin}:skill:{name}"
            if index == 0:
                item["id"] = base
            else:
                suffix = hashlib.sha256(item["source_path"].casefold().encode()).hexdigest()[:8]
                item["id"] = f"{base}:{suffix}"
            item["legacy_ids"] = [item.pop("legacy_id")]
    overrides = _trust_overrides(library)
    for item in grouped.values():
        override = overrides.get(item["id"])
        if isinstance(override, dict) and override.get("status") in VALID_STATUSES:
            item["trust_status"] = override["status"]
            item["trust_reason"] = override.get("reason", "")
    lock = _read_lock(library)
    for item in grouped.values():
        item["content_hash"] = _content_hash(item["source_path"])
        targets = []
        for entry in lock.get("entries", []):
            if not isinstance(entry, dict):
                continue
            if entry.get("id") in (item["id"], *item["legacy_ids"]) and entry.get("agent"):
                targets.append({"agent": entry["agent"], "path": entry.get("path"),
                                "method": entry.get("method", "symlink"),
                                "revision": entry.get("revision")})
        for agent in item["availability"]:
            if agent not in AGENT_DIRS or agent == "on-demand":
                continue
            if any(target["agent"] == agent for target in targets):
                continue
            probe = home / AGENT_DIRS[agent] / item["name"] / "SKILL.md"
            try:
                if probe.is_file():
                    targets.append({"agent": agent, "path": str(probe.parent),
                                    "method": "unmanaged", "revision": None})
            except OSError:
                pass
        item["managed_targets"] = targets


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
                    schema = tool.get("inputSchema")
                    grouped[tool_key] = {"id": _id("tool", tool_key), "type": "tool",
                                         "name": tool_name, "description": _short(tool.get("description", ""), 300),
                                         "input_schema": schema if isinstance(schema, dict) else None,
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
    started = time.perf_counter()
    items = _skill_entries(home, library) + _tool_entries(home, library, probe_tools) + _plugin_entries(home)
    elapsed = time.perf_counter() - started
    items.sort(key=lambda x: (x["type"], x["name"].casefold(), x["id"]))
    payload = {"schema": 1, "indexed_at_epoch": time.time(),
               "indexed_in_seconds": round(elapsed, 3),
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
    _TOKEN_CACHE.clear()
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


BM25_K1 = 1.2
BM25_B = 0.75
FIELD_WEIGHTS = (("name", 8.0), ("category", 2.0), ("description", 1.0), ("aliases", 3.0))
SYNONYMS = {
    "k8s": ["kubernetes"], "k9s": ["kubernetes"],
    "js": ["javascript"], "ts": ["typescript"], "py": ["python"],
    "gh": ["github"], "db": ["database"], "auth": ["authentication"],
    "config": ["configuration"], "docs": ["documentation"],
    "perf": ["performance"], "repo": ["repository"], "env": ["environment"],
    "tf": ["terraform"], "cicd": ["ci", "cd"],
}

_TOKEN_CACHE: dict = {}


def _tf(term: str, tokens: list[str]) -> int:
    return sum(1 for word in tokens
               if term == word or (len(term) >= 5 and word.startswith(term))
               or (len(term) >= 2 and not term.isascii() and term in word))


def _field_tokens(item: dict) -> tuple[list[str], list[str], list[str], list[str]]:
    return (_tokens(item["name"]), _tokens(item.get("category", "")),
            _tokens(item.get("description", "")),
            _tokens(" ".join(map(str, item.get("aliases", [])))))


def _corpus(home: Path | None, library: Path | None, kind: str | None) -> tuple[list, dict]:
    """Tokenized candidates + BM25 corpus stats, cached per index file."""
    home, library = _paths(home, library)
    path = library / "capabilities_index.json"
    try:
        stat = path.stat()
    except OSError:
        stat = None
    stamp = (stat.st_mtime_ns if stat else 0, stat.st_size if stat else 0)
    cached = _TOKEN_CACHE.get((str(path), kind))
    if cached is not None and cached[0] == (str(home), stamp):
        return cached[1]
    catalog = _load(home, library)
    docs = []
    for item in catalog["capabilities"]:
        if kind and item["type"] != kind:
            continue
        docs.append((item, _field_tokens(item)))
    totals = [0.0] * len(FIELD_WEIGHTS)
    for _, fields in docs:
        for index, tokens in enumerate(fields):
            totals[index] += len(tokens)
    count = max(len(docs), 1)
    stats = {"count": len(docs), "avg": [value / count for value in totals]}
    _TOKEN_CACHE.clear()
    _TOKEN_CACHE[(str(path), kind)] = ((str(home), stamp), (docs, stats))
    return docs, stats


def _compact_row(item: dict, matched: set, active_stores: set) -> dict:
    reason = _short(item.get("description") or item.get("category") or "Local capability", 90)
    available = item["availability"]
    if item["type"] == "skill" and len(active_stores) > 1 and active_stores <= set(available):
        available = ["all"]
    elif item["type"] == "skill" and len(active_stores) > 1 and len(available) >= 6:
        missing = active_stores - set(available)
        if len(missing) <= 2:
            available = ["all active except " + ", ".join(sorted(missing))]
    return {"id": item["id"], "type": item["type"], "name": item["name"],
            "reason": reason, "availability": available, "matched": sorted(matched)}


def search(query: str, kind: str | None = None, limit: int = 3, home: Path | None = None,
           library: Path | None = None, agent: str | None = None,
           project_hint: str | None = None) -> list[dict]:
    if kind not in (None, "skill", "tool", "plugin"):
        raise ValueError("kind must be skill, tool, or plugin")
    if not 1 <= limit <= 20:
        raise ValueError("limit must be 1..20")
    if agent is not None and agent not in AGENT_DIRS:
        raise CapError("UNSUPPORTED_AGENT", f"unknown agent: {agent}")
    terms = list(dict.fromkeys(_tokens(query)))
    if not terms:
        return []
    expansions = [(syn, 0.5) for term in terms for syn in SYNONYMS.get(term, [])
                  if syn not in terms]
    context_terms = list(dict.fromkeys(_tokens(project_hint or "")))[:10]
    docs, stats = _corpus(home, library, kind)
    count = stats["count"]
    if count == 0:
        return []
    catalog_active = _load(home, library).get("active_skill_stores", [])
    active_stores = set(catalog_active)
    want = query.strip().casefold()
    exact_id_hit = None
    for item, _ in docs:
        legacy = [entry.casefold() for entry in item.get("legacy_ids", [])]
        if item["id"].casefold() == want or want in legacy:
            exact_id_hit = item
            break
    query_terms = [(term, 1.0) for term in terms] + expansions
    doc_freq = [[0] * len(FIELD_WEIGHTS) for _ in query_terms]
    term_hits = []
    for _, fields in docs:
        per_term = []
        for position, (qterm, _) in enumerate(query_terms):
            counts = [_tf(qterm, tokens) for tokens in fields]
            per_term.append(counts)
            for index, value in enumerate(counts):
                if value:
                    doc_freq[position][index] += 1
        term_hits.append(per_term)
    scored = []
    for (item, fields), per_term in zip(docs, term_hits):
        matched: set[str] = set()
        score = 0.0
        for (qterm, qweight), dfs, counts in zip(query_terms, doc_freq, per_term):
            for index, ((field, weight), term_freq, df) in enumerate(zip(FIELD_WEIGHTS, counts, dfs)):
                if not term_freq:
                    continue
                length = len(fields[index])
                avg = stats["avg"][index]
                idf = math.log(1 + (count - df + 0.5) / (df + 0.5))
                denom = term_freq + BM25_K1 * (1 - BM25_B + BM25_B * (length / avg if avg else 1))
                score += qweight * weight * idf * (term_freq * (BM25_K1 + 1)) / denom
                matched.add(field)
        name_hit = any(per_term[i][0] for i in range(len(terms)))
        alias_hit = any(per_term[i][3] for i in range(len(terms)))
        syn_anchor = any(per_term[len(terms) + s][0] or per_term[len(terms) + s][3]
                         for s in range(len(expansions))) if expansions else False
        syn_any = any(any(per_term[len(terms) + s]) for s in range(len(expansions))) if expansions else False
        hits = sum(1 for i in range(len(terms)) if any(per_term[i]))
        if not (name_hit or alias_hit or syn_anchor) and (
                hits < math.ceil(len(terms) / 3) or (len(terms) > 1 and hits == 1 and not alias_hit)):
            continue
        effective = hits or (0.5 if syn_any else 0)
        score += 8 * effective / len(terms) + item.get("quality", 0)
        if item["type"] == "skill":
            score += 1
        if _tokens(item["name"]) == terms:
            score += 12
        score *= (effective / len(terms)) ** 2
        if context_terms:
            name = _tokens(item["name"])
            category = _tokens(item.get("category", ""))
            description = _tokens(item.get("description", ""))
            aliases = _tokens(" ".join(map(str, item.get("aliases", []))))
            score += min(4, sum(2 if _has(term, name) else 1 if _has(term, category)
                                or _has(term, aliases) else 0.5 if _has(term, description) else 0
                                for term in context_terms))
        if agent:
            availability = set(item.get("availability", []))
            owners = {variant.get("store") for variant in item.get("variants", [])}
            if agent in availability:
                score += 8
            elif item["type"] == "skill" and "agents" in availability:
                score += 6
            elif item["type"] == "skill" and "on-demand" in availability and agent in owners:
                score += 2
            elif item["type"] == "plugin" or (item["type"] == "tool" and availability):
                score -= 4
        scored.append((score, item["name"].casefold(), item, matched))
    best: dict[str, tuple[float, str, dict, set]] = {}
    for entry in scored:
        score, name, row, matched = entry
        previous = best.get(name)
        if previous is None or score > previous[0]:
            best[name] = entry
    rows = [_compact_row(item, matched, active_stores) for _, _, item, matched in
            sorted(best.values(), key=lambda x: (-x[0], x[1]))]
    if exact_id_hit is not None:
        rows = [row for row in rows if row["id"] != exact_id_hit["id"]]
        rows.insert(0, _compact_row(exact_id_hit, {"exact"}, active_stores))
    return rows[:limit]
def show(capability_id: str, home: Path | None = None, library: Path | None = None) -> dict | None:
    home, library = _paths(home, library)
    overrides = _trust_overrides(library)
    capabilities = _load(home, library)["capabilities"]
    for item in capabilities:
        if item["id"] == capability_id or capability_id in item.get("legacy_ids", []):
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
            if item["type"] == "skill" and not Path(item["source_path"]).is_file():
                detail["installation_state"] = "broken"
            override = overrides.get(item["id"], {})
            if isinstance(override, dict) and override.get("status") in VALID_STATUSES:
                detail["trust_status"] = override["status"]
                detail["trust_reason"] = override.get("reason", "")
            return detail
    return None


def _activation_manifest_path(library: Path, agent: str, name: str) -> Path:
    return library / "_activations" / f"{agent}__{name}.json"


def _validate_skill_name(name: str) -> str:
    if not name or name in (".", "..") or "/" in name or "\\" in name or Path(name).name != name:
        raise CapError("INVALID_PATH", f"invalid skill folder name: {name!r}")
    return name


def _is_junction(path: Path) -> bool:
    probe = getattr(path, "is_junction", None)
    if callable(probe):
        try:
            return bool(probe())
        except OSError:
            return False
    try:
        return path.lstat().st_reparse_tag == 0xA0000003
    except (OSError, AttributeError):
        return False


def _managed_link_kind(path: Path) -> str:
    if _is_junction(path):
        return "junction"
    if path.is_symlink():
        return "directory_symlink" if path.is_dir() else "file_symlink"
    return "directory"


def _points_at(link: Path, src: Path) -> bool:
    try:
        return link.resolve() == src.resolve()
    except OSError:
        return False


def _tree_hash(path: Path) -> str | None:
    try:
        digest = hashlib.sha256()
        items = sorted(path.rglob("*"), key=lambda p: p.relative_to(path).as_posix().casefold())
        for item in items:
            rel = item.relative_to(path).as_posix()
            digest.update(rel.encode("utf-8", "surrogatepass") + b"\0")
            if _is_junction(item) or item.is_symlink():
                digest.update(b"L\0" + os.readlink(item).encode("utf-8", "surrogatepass"))
            elif item.is_file():
                digest.update(b"F\0")
                with open(item, "rb") as handle:
                    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                        digest.update(chunk)
            else:
                digest.update(b"D\0")
        return "sha256:" + digest.hexdigest()
    except OSError:
        return None


def _remove_managed_link(path: Path, kind: str) -> None:
    if kind in ("junction", "directory_symlink"):
        os.rmdir(path)
    else:
        path.unlink()


def _write_activation_manifest(path: Path, manifest: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _adopt_activation_manifest(library: Path, manifest_path: Path, plan: dict) -> None:
    if not manifest_path.is_file():
        target = Path(plan["target"])
        copied = plan.get("method") == "copy"
        _write_activation_manifest(manifest_path, {
            "format": 1, "status": "applied", "stage": "adopted",
            "id": plan["id"], "agent": plan["agent"], "name": plan["name"],
            "source": plan["source"], "target": plan["target"],
            "method": "copy" if copied else "link",
            "source_tree": _tree_hash(target) if copied else None,
            "link_kind": "copy" if copied else _managed_link_kind(target),
            "created_at_epoch": time.time(),
        })
    lock = _read_lock(library)
    if not any(isinstance(e, dict) and e.get("id") == plan["id"]
               and e.get("agent") == plan["agent"] for e in lock["entries"]):
        _upsert_lock(library, {"id": plan["id"], "revision": plan.get("revision"),
                               "agent": plan["agent"], "path": plan["target"],
                               "method": _managed_link_kind(Path(plan["target"])),
                               "installed_at_epoch": time.time()})


def preview_activation(capability_id: str, agent: str, home: Path | None = None, library: Path | None = None,
                       method: str = "link") -> dict:
    """Dry-run plan for activate. Makes no filesystem changes."""
    if method not in ("link", "copy"):
        raise ValueError("method must be link or copy")
    home, library = _paths(home, library)
    item = show(capability_id, home, library)
    if item is None or item["type"] != "skill":
        raise CapError("NOT_FOUND", "activate requires an exact skill ID from search")
    if agent not in AGENT_DIRS:
        raise CapError("UNSUPPORTED_AGENT", f"unknown agent: {agent}")
    variants = item["variants"]
    chosen = next((v for v in variants if v["store"] == agent), variants[0])
    src = Path(chosen["path"])
    name = _validate_skill_name(item["name"])
    target = home / AGENT_DIRS[agent] / name
    plan = {"id": item["id"], "requested_id": capability_id, "agent": agent, "name": name,
            "revision": item.get("content_hash"),
            "trust_status": item.get("trust_status", "unscanned"),
            "source": str(src), "target": str(target),
            "method": "copy" if method == "copy" else ("symlink" if os.name != "nt" else "symlink, junction fallback"),
            "manifest": str(_activation_manifest_path(library, agent, name)),
            "status": "ready", "conflict": None}
    if not (src / "SKILL.md").is_file():
        plan["status"] = "conflict"
        plan["conflict"] = f"skill source unavailable: {src}"
    elif os.path.lexists(target):
        if _points_at(target, src):
            plan["status"] = "already_active"
        elif (method == "copy" and target.is_dir() and not target.is_symlink()
                and not _is_junction(target) and _tree_hash(target) == _tree_hash(src)):
            plan["status"] = "already_active"
        else:
            plan["status"] = "conflict"
            plan["conflict"] = f"refusing to overwrite existing skill: {target}"
    return plan


def _junction_fallback(target_root: Path, name: str, src: Path, target: Path) -> None:
    command = ("$data = [Console]::In.ReadToEnd() | ConvertFrom-Json; "
               "Set-Location -LiteralPath $data.parent; "
               "New-Item -ItemType Junction -Path . -Name $data.name "
               "-Target $data.source -ErrorAction Stop | Out-Null")
    result = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
        input=json.dumps({"parent": str(target_root), "name": name, "source": str(src)}),
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode != 0 or not target.is_dir() or not _points_at(target, src):
        raise OSError(result.stderr.strip() or "junction creation failed")


def activate(capability_id: str, agent: str, home: Path | None = None, library: Path | None = None,
         confirm: bool = False, method: str = "link") -> dict:
    home, library = _paths(home, library)
    plan = preview_activation(capability_id, agent, home, library, method)
    trust = plan.get("trust_status", "unscanned")
    if trust == "blocked":
        raise CapError("UNTRUSTED", f"skill is blocked by policy: {plan['id']}")
    if trust in ("needs_review", "unscanned") and not confirm:
        raise CapError("UNTRUSTED",
                       f"skill requires review before activation (trust={trust}); "
                       "re-run with explicit confirmation")
    manifest_path = Path(plan["manifest"])
    if plan["status"] == "conflict":
        if os.path.lexists(plan["target"]):
            raise FileExistsError(plan["conflict"])
        raise FileNotFoundError(plan["conflict"])
    if plan["status"] == "already_active":
        _adopt_activation_manifest(library, manifest_path, plan)
        return {"status": "already_active", "id": plan["id"], "agent": agent, "path": plan["target"]}
    src, target = Path(plan["source"]), Path(plan["target"])
    target.parent.mkdir(parents=True, exist_ok=True)
    manifest = {"format": 1, "status": "applying", "stage": "link",
                "id": plan["id"], "agent": agent, "name": plan["name"],
                "source": plan["source"], "target": plan["target"],
                "method": method, "source_tree": None,
                "link_kind": None, "created_at_epoch": time.time()}
    created = False
    try:
        if method == "copy":
            import shutil as _shutil
            resolved = src.resolve()
            _shutil.copytree(resolved, target)
            created = True
            manifest["source_tree"] = _tree_hash(resolved)
            if manifest["source_tree"] is None or _tree_hash(target) != manifest["source_tree"]:
                raise OSError(f"activation verify failed: {target}")
        else:
            try:
                os.symlink(src, target, target_is_directory=True)
                created = True
            except FileExistsError:
                if _points_at(target, src):
                    _adopt_activation_manifest(library, manifest_path, plan)
                    return {"status": "already_active", "id": plan["id"], "agent": agent, "path": plan["target"]}
                raise FileExistsError(f"refusing to overwrite existing skill: {target}")
            except OSError:
                if os.name == "nt":
                    _junction_fallback(target.parent, plan["name"], src, target)
                    created = True
                else:
                    raise
            if created and not _points_at(target, src):
                raise OSError(f"activation verify failed: {target}")
        manifest["status"] = "applied"
        manifest["stage"] = "verified"
        manifest["link_kind"] = "copy" if method == "copy" else _managed_link_kind(target)
        _write_activation_manifest(manifest_path, manifest)
        _upsert_lock(library, {"id": plan["id"], "revision": plan.get("revision"),
                               "agent": agent, "path": plan["target"],
                               "method": manifest["link_kind"],
                               "installed_at_epoch": time.time()})
    except Exception as exc:
        manifest["status"] = "failed"
        manifest["error"] = str(exc)
        manifest["rolled_back"] = False
        if created and os.path.lexists(target) and (method == "copy" or _points_at(target, src)):
            try:
                if method == "copy" and _managed_link_kind(target) == "directory":
                    import shutil as _shutil
                    _shutil.rmtree(target)
                else:
                    _remove_managed_link(target, _managed_link_kind(target))
                manifest["rolled_back"] = True
            except OSError as rollback_exc:
                manifest["rollback_error"] = str(rollback_exc)
        _write_activation_manifest(manifest_path, manifest)
        raise
    refresh_index(home=home, library=library)
    return {"status": "activated", "id": plan["id"], "agent": agent, "path": plan["target"]}


def load_skill(capability_id: str, revision: str | None = None,
             home: Path | None = None, library: Path | None = None) -> dict:
    """Read exactly one skill's SKILL.md by exact ID. Never executes anything."""
    home, library = _paths(home, library)
    item = show(capability_id, home, library)
    if item is None or item["type"] != "skill":
        raise CapError("NOT_FOUND", f"unknown skill ID: {capability_id}")
    current = item.get("content_hash")
    if revision is not None and revision != current:
        raise CapError("STALE_REVISION",
                       f"skill {item['id']} changed since revision {revision}")
    try:
        content = Path(item["source_path"]).read_text(encoding="utf-8")
    except OSError as exc:
        raise CapError("NOT_FOUND", f"skill source unreadable: {item['source_path']}") from exc
    return {"id": item["id"], "name": item["name"], "source_path": item["source_path"],
            "revision": current, "trust_status": item.get("trust_status", "unscanned"),
            "content": content}


def deactivate_skill(capability_id: str, agent: str, home: Path | None = None, library: Path | None = None) -> dict:
    """Remove only a managed activation link. Never touches the source or real directories."""
    home, library = _paths(home, library)
    item = show(capability_id, home, library)
    if item is None or item["type"] != "skill":
        raise CapError("NOT_FOUND", "deactivate requires an exact skill ID from search")
    if agent not in AGENT_DIRS:
        raise CapError("UNSUPPORTED_AGENT", f"unknown agent: {agent}")
    name = _validate_skill_name(item["name"])
    manifest_path = _activation_manifest_path(library, agent, name)
    manifest = _read_json(manifest_path)
    target = home / AGENT_DIRS[agent] / name
    result = {"status": "deactivated", "id": item["id"], "agent": agent, "path": str(target)}
    if not os.path.lexists(target):
        if isinstance(manifest, dict):
            manifest_path.unlink(missing_ok=True)
        _remove_lock(library, (item["id"], *item.get("legacy_ids", [])), agent)
        result["status"] = "already_removed"
        return result
    if not isinstance(manifest, dict) or manifest.get("status") not in ("applied", "failed"):
        raise FileExistsError(f"refusing to remove unmanaged skill: {target}")
    kind = _managed_link_kind(target)
    if manifest.get("method") == "copy" or manifest.get("link_kind") == "copy":
        if kind != "directory":
            raise FileExistsError(f"activation drift: {target} is no longer the managed copy")
        if _tree_hash(target) != manifest.get("source_tree"):
            raise FileExistsError(f"activation drift: managed copy changed: {target}")
        import shutil as _shutil
        _shutil.rmtree(target)
    else:
        if kind == "directory":
            raise FileExistsError(f"refusing to remove unmanaged directory: {target}")
        recorded = manifest.get("source")
        if recorded and _points_at(target, Path(recorded)):
            _remove_managed_link(target, kind)
        elif manifest.get("status") == "failed":
            _remove_managed_link(target, kind)
        else:
            raise FileExistsError(f"activation drift: {target} no longer points at the recorded source")
    manifest_path.unlink(missing_ok=True)
    _remove_lock(library, (item["id"], *item.get("legacy_ids", [])), agent)
    refresh_index(home=home, library=library)
    return result


VALID_STATUSES = ("approved", "unscanned", "needs_review", "blocked")

_F_SECRET = re.compile(r"(?i)\b[\w-]*(api[_-]?key|access[_-]?token|auth[_-]?token|secret|password|personal[_-]?access[_-]?token)[\w-]*")
_F_SEND = re.compile(r"(?i)\b(requests\.(post|put)|urllib|httpx|curl|wget|Invoke-WebRequest|Invoke-RestMethod|fetch\()\b")
_F_REMOTE_EXEC = re.compile(r"(?i)(curl|wget)[^\n]*\|\s*(sh|bash)|Invoke-Expression|DownloadString|\biex\s*\(")
_F_EMBEDDED_SECRET = re.compile(r"\b(sk-[A-Za-z0-9-]{8,}|AKIA[0-9A-Z]{16}|ghp_[A-Za-z0-9]{8,}|xox[bpas]-[A-Za-z0-9-]{8,})\b")
_F_TRAVERSAL = re.compile(r"\.\./")
_F_WRITE = re.compile(r"(?i)\b(open\s*\(|fopen\s*\(|write\b|copy\b|move\b|shutil\b)")
_F_URL = re.compile(r"https?://[^\s)\"'<>]+")


def _trust_overrides_path(library: Path) -> Path:
    return library / "trust-overrides.json"


def _trust_overrides(library: Path) -> dict:
    data = _read_json(_trust_overrides_path(library))
    if isinstance(data, dict) and isinstance(data.get("overrides"), dict):
        return data["overrides"]
    return {}


def _write_trust_overrides(library: Path, overrides: dict) -> None:
    dest = _trust_overrides_path(library)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=dest.parent,
                                     prefix=".trust-overrides-", suffix=".json", delete=False) as fh:
        json.dump({"schema_version": 1, "overrides": overrides}, fh, ensure_ascii=False, indent=2)
        tmp = Path(fh.name)
    os.replace(tmp, dest)


def set_trust_override(capability_id: str, status: str, reason: str = "",
                       home: Path | None = None, library: Path | None = None) -> dict:
    """Record a manual trust decision with an audit trail. `clear` removes it."""
    home, library = _paths(home, library)
    item = show(capability_id, home, library)
    if item is None or item["type"] != "skill":
        raise CapError("NOT_FOUND", f"unknown skill ID: {capability_id}")
    if status not in (*VALID_STATUSES, "clear"):
        raise ValueError(f"status must be one of {(*VALID_STATUSES, 'clear')}")
    overrides = _trust_overrides(library)
    if status == "clear":
        overrides.pop(item["id"], None)
    else:
        overrides[item["id"]] = {"status": status, "reason": reason,
                                 "decided_by": "cli-operator",
                                 "decided_at_epoch": time.time(),
                                 "last_scanned_revision": item.get("content_hash")}
    _write_trust_overrides(library, overrides)
    if status == "clear":
        item = show(capability_id, home, library)
        return {"id": item["id"], "trust_status": item.get("trust_status")}
    return {"id": item["id"], "trust_status": status}


def _scanner_info() -> dict:
    """Detect an optional third-party skill scanner without running any scan."""
    probe = shutil.which("skillspector")
    if not probe:
        return {"name": "skillspector", "available": False}
    try:
        result = subprocess.run([probe, "--version"], capture_output=True, text=True,
                                timeout=15, encoding="utf-8", errors="replace")
        output = (result.stdout or result.stderr or "").strip().splitlines()
        version = output[0].strip() if output else "unknown"
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"name": "skillspector", "available": False, "error": str(exc)}
    return {"name": "skillspector", "available": True, "version": version,
            "probed_at_epoch": time.time()}


def _scan_texts(root: Path) -> list[tuple[str, str]]:
    """Read SKILL.md plus adjacent scripts/configs, read-only, size-capped."""
    texts = []
    candidates = [root / "SKILL.md"]
    try:
        entries = sorted(root.rglob("*"))[:200]
    except OSError:
        entries = []
    for entry in entries:
        if entry.is_file() and not entry.is_symlink() and entry.suffix.lower() in (
                ".md", ".sh", ".ps1", ".py", ".js", ".ts", ".yaml", ".yml",
                ".json", ".toml", ".txt"):
            candidates.append(entry)
    seen = set()
    for path in candidates:
        try:
            key = path.resolve()
        except OSError:
            continue
        if key in seen:
            continue
        seen.add(key)
        try:
            if path.stat().st_size > 1024 * 1024:
                continue
            texts.append((str(path.relative_to(root)), path.read_text(encoding="utf-8", errors="replace")))
        except OSError:
            continue
    return texts


def validate_skill(capability_id: str, home: Path | None = None, library: Path | None = None,
                   apply_trust: bool = False) -> dict:
    """Lint one skill and run heuristic supply-chain checks. Never executes anything."""
    home, library = _paths(home, library)
    item = show(capability_id, home, library)
    if item is None or item["type"] != "skill":
        raise CapError("NOT_FOUND", f"unknown skill ID: {capability_id}")
    findings: list[dict] = []
    if not item.get("description"):
        findings.append({"level": "medium", "code": "MISSING_DESCRIPTION",
                         "detail": "frontmatter has no usable description"})
    if item.get("version") is None:
        findings.append({"level": "info", "code": "VERSION_MISSING",
                         "detail": "no version declared in frontmatter"})
    root = Path(item["source_path"]).parent
    if Path(item["name"]).name != root.name:
        findings.append({"level": "medium", "code": "NAME_MISMATCH",
                         "detail": f"frontmatter name {item['name']!r} differs from folder {root.name!r}"})
    texts = _scan_texts(root)
    if not texts:
        findings.append({"level": "high", "code": "SOURCE_UNREADABLE",
                         "detail": f"cannot read skill sources under {root}"})
    scripts = [rel for rel, _ in texts[1:]]
    if scripts:
        findings.append({"level": "info", "code": "SCRIPTS_PRESENT",
                         "detail": f"ships executable-adjacent files (never auto-executed): {scripts[:5]}"})
    for rel, text in texts:
        if _F_REMOTE_EXEC.search(text):
            findings.append({"level": "high", "code": "REMOTE_EXEC",
                             "detail": f"{rel}: downloads and executes remote code"})
        if _F_SECRET.search(text) and _F_SEND.search(text):
            findings.append({"level": "high", "code": "CRED_EXFIL",
                             "detail": f"{rel}: handles credentials and sends network requests"})
        if _F_EMBEDDED_SECRET.search(text):
            findings.append({"level": "high", "code": "EMBEDDED_SECRET",
                             "detail": f"{rel}: looks like an embedded API secret"})
        if _F_TRAVERSAL.search(text) and _F_WRITE.search(text):
            findings.append({"level": "medium", "code": "PATH_TRAVERSAL",
                             "detail": f"{rel}: parent-directory reference near file writes"})
    urls = sorted({match.group(0) for _, text in texts for match in _F_URL.finditer(text)})
    if urls:
        findings.append({"level": "info", "code": "EXTERNAL_ENDPOINTS",
                         "detail": f"references {len(urls)} external URL(s): {urls[:5]}"})
    scanner = _scanner_info()
    if not scanner["available"]:
        findings.append({"level": "info", "code": "SCANNER_UNAVAILABLE",
                         "detail": "skillspector not installed; heuristic checks only"})
    applied = None
    if apply_trust and any(f["level"] == "high" for f in findings):
        record = {"status": "needs_review",
                  "reason": "validate found high-severity findings: " + ", ".join(
                      sorted({f["code"] for f in findings if f["level"] == "high"})),
                  "decided_by": "validate --apply-trust",
                  "decided_at_epoch": time.time(),
                  "last_scanned_revision": item.get("content_hash"),
                  "scanner": scanner}
        overrides = _trust_overrides(library)
        overrides[item["id"]] = record
        _write_trust_overrides(library, overrides)
        applied = "needs_review"
    return {"id": item["id"], "trust_status": applied or item.get("trust_status"),
            "revision": item.get("content_hash"), "scanner": scanner,
            "findings": findings}

METRIC_QUERIES = (
    "debug production crash",
    "deploy cloud workers",
    "validate pdf forms",
    "kubernetes manifests",
    "browser automation",
    "review pull request",
    "postgres slow query",
    "threat model stride",
)


def collect_metrics(home: Path | None = None, library: Path | None = None) -> dict:
    """Read-only telemetry snapshot: sizes, search latency, handshake cost."""
    import statistics

    home, library = _paths(home, library)
    metrics: dict = {"errors": []}
    try:
        payload = _load(home, library)
    except (FileNotFoundError, ValueError) as exc:
        return {"errors": [str(exc)]}
    capabilities = payload.get("capabilities", [])
    by_type: dict[str, int] = {}
    for item in capabilities:
        by_type[str(item.get("type"))] = by_type.get(str(item.get("type")), 0) + 1
    index_path = library / "capabilities_index.json"
    catalog_path = library / "skills_index.json"
    metrics["catalog"] = {
        "capabilities": len(capabilities),
        "by_type": by_type,
        "index_bytes": index_path.stat().st_size if index_path.is_file() else None,
        "skills_catalog_bytes": catalog_path.stat().st_size if catalog_path.is_file() else None,
        "last_indexed_in_seconds": payload.get("indexed_in_seconds"),
    }
    latencies = []
    max_bytes = 0
    for query in METRIC_QUERIES:
        start = time.perf_counter()
        try:
            rows = search(query, limit=3, home=home, library=library)
        except (OSError, ValueError) as exc:
            metrics["errors"].append(f"search {query!r}: {exc}")
            continue
        latencies.append((time.perf_counter() - start) * 1000)
        max_bytes = max(max_bytes, len(json.dumps(rows, ensure_ascii=False).encode("utf-8")))
    if latencies:
        metrics["search"] = {
            "queries": len(latencies),
            "p50_ms": round(statistics.median(latencies), 1),
            "p95_ms": round(sorted(latencies)[min(len(latencies) - 1, int(len(latencies) * 0.95))], 1),
            "max_result_bytes": max_bytes,
        }
    else:
        metrics["search"] = {"queries": 0}
    try:
        import capability_mcp as mcp

        start = time.perf_counter()
        initialized = mcp._dispatch({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                     "params": {"protocolVersion": "2025-11-25"}})
        listed = mcp._dispatch({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        metrics["mcp"] = {
            "handshake_ms": round((time.perf_counter() - start) * 1000, 1),
            "protocol": initialized.get("result", {}).get("protocolVersion"),
            "tools": len(listed.get("result", {}).get("tools", [])),
        }
    except (OSError, ValueError) as exc:
        metrics["errors"].append(f"mcp: {exc}")
    lock = _read_lock(library)
    activations: dict[str, int] = {}
    for entry in lock.get("entries", []):
        if isinstance(entry, dict):
            key = str(entry.get("agent", "?")) + ":" + str(entry.get("method", "?"))
            activations[key] = activations.get(key, 0) + 1
    metrics["activations"] = {"locked": len(lock.get("entries", [])), "by_agent_method": activations}
    findings = doctor(home, library)
    metrics["doctor"] = {"findings": len(findings),
                         "errors": sum(1 for f in findings if f.get("level") == "error")}
    metrics["note"] = ("sizes are bytes; token figures elsewhere divide chars by 4 "
                       "and are estimates, not tokenizer measurements")
    return metrics

def doctor(home: Path | None = None, library: Path | None = None) -> list[dict]:
    """Inspect index health, skill links, and activation/archive manifests."""
    home, library = _paths(home, library)
    path = library / "capabilities_index.json"
    try:
        payload = _load(home, library)
    except FileNotFoundError:
        return [{"level": "error", "code": "INDEX_MISSING",
                 "detail": f"capability index missing: {path}; run: library_catalog.py"}]
    except ValueError as exc:
        return [{"level": "error", "code": "INDEX_STALE_OR_UNREADABLE", "detail": str(exc)}]
    findings: list[dict] = []
    for item in payload.get("capabilities", []):
        if item.get("type") != "skill":
            continue
        for variant in item.get("variants", []):
            md = variant.get("skill_md", "")
            if md and not Path(md).is_file():
                findings.append({"level": "warning", "code": "BROKEN_VARIANT",
                                 "detail": f"{item.get('id')} {item.get('name')} variant missing: {md}"})
    actdir = library / "_activations"
    if actdir.is_dir():
        for entry in sorted(actdir.glob("*.json")):
            data = _read_json(entry)
            if not isinstance(data, dict):
                findings.append({"level": "warning", "code": "CORRUPT_MANIFEST",
                                 "detail": f"unreadable activation manifest: {entry}"})
                continue
            target = Path(data.get("target", ""))
            if data.get("status") == "failed":
                findings.append({"level": "error", "code": "INTERRUPTED_ACTIVATION",
                                 "detail": f"{data.get('id')} for {data.get('agent')}: {data.get('error')}; rolled_back={data.get('rolled_back')}; manifest: {entry}"})
            elif data.get("status") == "applied":
                recorded = data.get("source", "")
                if data.get("method") == "copy" or data.get("link_kind") == "copy":
                    if not target.is_dir() or _tree_hash(target) != data.get("source_tree"):
                        findings.append({"level": "warning", "code": "ACTIVATION_DRIFT",
                                         "detail": f"{data.get('id')} for {data.get('agent')}: managed copy missing or changed: {target}"})
                elif not os.path.lexists(target) or (recorded and not _points_at(target, Path(recorded))):
                    findings.append({"level": "warning", "code": "ACTIVATION_DRIFT",
                                     "detail": f"{data.get('id')} for {data.get('agent')}: target missing or moved: {target}"})
    try:
        raw_overrides = _trust_overrides_path(library).read_text(encoding="utf-8")
        corrupt = bool(raw_overrides.strip()) and not isinstance(
            json.loads(raw_overrides).get("overrides"), dict)
    except (OSError, ValueError):
        corrupt = "unreadable" if _trust_overrides_path(library).exists() else False
    if corrupt:
        findings.append({"level": "warning", "code": "CORRUPT_MANIFEST",
                         "detail": f"trust overrides file is corrupt: {_trust_overrides_path(library)}"})
    else:
        revisions = {c.get("id"): c.get("content_hash")
                     for c in payload.get("capabilities", []) if isinstance(c, dict)}
        for override_id, override in _trust_overrides(library).items():
            scanned = override.get("last_scanned_revision") if isinstance(override, dict) else None
            if scanned and override_id in revisions and revisions[override_id] != scanned:
                findings.append({"level": "warning", "code": "STALE_SCAN",
                                 "detail": f"{override_id} changed since its last validation"})
    archive = library / "on-demand"
    if archive.is_dir():
        for entry in sorted(archive.glob("*manifest.json")):
            data = _read_json(entry)
            if not isinstance(data, dict):
                findings.append({"level": "warning", "code": "CORRUPT_MANIFEST",
                                 "detail": f"unreadable archive manifest: {entry}"})
                continue
            if data.get("status") in ("applying", "archiving", "restoring", "partial_archive", "partial_restore"):
                findings.append({"level": "error", "code": "INTERRUPTED_ARCHIVE",
                                 "detail": f"archive manifest not cleanly finished ({data.get('status')}): {entry}"})
            for item_entry in data.get("entries", []):
                archived = item_entry.get("archive_path", "")
                live = [p.get("resolved_path", "") for p in item_entry.get("store_paths", []) if isinstance(p, dict)]
                if archived and not Path(archived).exists() and not any(d and Path(d).is_dir() for d in live):
                    findings.append({"level": "error", "code": "ARCHIVE_LOST",
                                     "detail": f"{item_entry.get('name')}: neither archive nor source exists (manifest: {entry})"})
    return findings
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Find installed skills, MCP tools, and plugins locally")
    sub = parser.add_subparsers(dest="command", required=True)
    refresh = sub.add_parser("refresh")
    refresh.add_argument("--probe-tools", action="store_true", help="probe local command MCP servers (5s timeout each)")
    find = sub.add_parser("search")
    find.add_argument("query")
    find.add_argument("--kind", choices=("skill", "tool", "plugin"))
    find.add_argument("--limit", type=int, default=3)
    find.add_argument("--agent", choices=sorted(AGENT_DIRS))
    find.add_argument("--project-hint", help="short optional project technology hint")
    find.add_argument("--json", action="store_true")
    detail = sub.add_parser("show")
    detail.add_argument("id")
    action = sub.add_parser("activate")
    action.add_argument("id")
    action.add_argument("--agent", required=True, choices=sorted(AGENT_DIRS))
    action.add_argument("--dry-run", action="store_true", help="print the plan without changing anything")
    action.add_argument("--yes", action="store_true", help="confirm activation of a skill that needs review")
    action.add_argument("--copy", action="store_true", help="copy the skill instead of linking it")
    deact = sub.add_parser("deactivate")
    deact.add_argument("id")
    deact.add_argument("--agent", required=True, choices=sorted(AGENT_DIRS))
    valid = sub.add_parser("validate")
    valid.add_argument("id")
    valid.add_argument("--apply-trust", action="store_true",
                       help="record needs_review when high-severity findings exist")
    trust_cmd = sub.add_parser("trust")
    trust_cmd.add_argument("id")
    trust_cmd.add_argument("status", choices=[*VALID_STATUSES, "clear"])
    trust_cmd.add_argument("--reason", default="")
    doc = sub.add_parser("doctor")
    doc.add_argument("--json", action="store_true")
    metrics_cmd = sub.add_parser("metrics")
    metrics_cmd.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "refresh":
            data = refresh_index(args.probe_tools)
            print(json.dumps({"indexed": len(data["capabilities"]), "path": str(_paths(None, None)[1] / "capabilities_index.json")}))
        elif args.command == "search":
            rows = search(args.query, args.kind, args.limit, agent=args.agent, project_hint=args.project_hint)
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
        elif args.command == "activate":
            if args.dry_run:
                print(json.dumps(preview_activation(args.id, args.agent), ensure_ascii=False, indent=2))
            else:
                print(json.dumps(activate(args.id, args.agent, confirm=args.yes,
                                        method="copy" if args.copy else "link"), ensure_ascii=False))
        elif args.command == "deactivate":
            print(json.dumps(deactivate_skill(args.id, args.agent), ensure_ascii=False))
        elif args.command == "validate":
            print(json.dumps(validate_skill(args.id, apply_trust=args.apply_trust),
                             ensure_ascii=False, indent=2))
        elif args.command == "trust":
            print(json.dumps(set_trust_override(args.id, args.status, args.reason),
                             ensure_ascii=False))
        elif args.command == "metrics":
            metrics = collect_metrics()
            if args.json:
                print(json.dumps(metrics, ensure_ascii=False, indent=2))
            else:
                print(f"capabilities: {metrics.get('catalog', {}).get('capabilities')} "
                      f"{metrics.get('catalog', {}).get('by_type')}")
                print(f"search p50/p95 ms: {metrics.get('search', {}).get('p50_ms')}/"
                      f"{metrics.get('search', {}).get('p95_ms')} "
                      f"max result bytes: {metrics.get('search', {}).get('max_result_bytes')}")
                print(f"mcp handshake ms: {metrics.get('mcp', {}).get('handshake_ms')} "
                      f"tools: {metrics.get('mcp', {}).get('tools')}")
                print(f"activations locked: {metrics.get('activations', {}).get('locked')} "
                      f"doctor findings: {metrics.get('doctor', {}).get('findings')}")
                for error in metrics.get("errors", []):
                    print(f"error: {error}")
            return 0
        else:
            findings = doctor()
            if args.json:
                print(json.dumps(findings, ensure_ascii=False, indent=2))
            else:
                for finding in findings:
                    print(f"{finding['level']}: {finding['code']}: {finding['detail']}")
                if not findings:
                    print("doctor: no problems found")
        return 0
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
