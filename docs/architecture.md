# Architecture

Local-first capability discovery. The Finder owns the catalog and
installation state; the Engineering Stack owns workflow content.
Finder indexes Stack `suites/` as an ordinary source, never imports it.

## Data flow

```text
SKILL.md files (+ MCP configs, plugin manifests)
  -> library_catalog.py  (skill catalog: skills_index.{json,md,tsv})
  -> capability_finder.refresh_index  (capabilities_index.json)
  -> search/show/activate  (CLI)  -> capability_mcp.py (stdio MCP)
```

The catalog is a cache. `SKILL.md` files are the source of truth.
Search returns lightweight metadata (default 3 rows, no bodies).
Exact-ID `show` returns paths; the agent reads the selected `SKILL.md`.
Registry presence, native-agent installation, and session loading are
three different states. Search never installs or activates anything.

## Modules

| File | Role |
|---|---|
| `library_catalog.py` | Walk agent skill roots, dedupe by real path, write skill indexes |
| `capability_finder.py` | Merge skills+MCP+plugins into capability index; weighted lexical search; exact-ID activation via symlink (junction fallback on Windows) |
| `capability_mcp.py` | Thin stdio MCP adapter (`search_capabilities`, `get_capability`, `activate_skill`); legacy `initialize` + stateless `server/discover` |
| `skill_archive.py` | Dry-run-manifest archive/restore with tree-hash verification |
| `install_agents.py` | Idempotent MCP registration for installed clients, with rollback on failure |
| `auto_skill_router.py` / `auto_install.py` / `auto_hook.py` | Opt-in prompt trigger + managed `find-best-skills` instructions |
| `startup_inventory.py` / `organize_skills.py` / `find_skills.py` | Footprint snapshots, junction view, legacy search |

## Defaults

Offline, lexical search, local metadata. No cloud embeddings, no
auto-execution of skill scripts, no unsolicited network access.
Probing MCP servers (`--probe-tools`) is explicit and refresh-only.
