# Current state (P0 baseline, 2026-10-09)

Branch: `capfind/plan-implementation`. All measurements on an isolated
synthetic fixture unless noted. No user skill files were touched.

## Public entrypoints

- CLI: `capability_finder.py {refresh,search,show,activate}`,
  `library_catalog.py`, `skill_archive.py`, `install_agents.py`,
  `capfind auto {enable,status,disable,test}` (node `bin/capfind.js`).
- MCP tools: `search_capabilities(query, kind, limit, agent, project_hint)`,
  `get_capability(capability_id)`, `activate_skill(capability_id, agent)`.
- npm package `local-capability-finder-cli` ships the 9 `.py` files above.

## Measured baseline

- Unit tests: **42 pass** (`python -m unittest discover -s tests`).
- Portable benchmark: **top-3 9/9**, max result JSON **164 bytes**
  (`python tests/benchmark_queries.py`).
- Search response shape: `{id, type, name, reason, availability}` only.
- MCP handshake: legacy `initialize` through `2025-11-25`; stateless
  `server/discover` on `2026-07-28`; wire output ASCII-escaped.
- Live user index on this machine reports **stale** (source files moved
  since last refresh) instead of serving silently outdated results.

## Known safety risks (drive P1)

1. `skill_archive.py` anchors its vault at the **repo checkout**
   (`ROOT / "on-demand"`), while search reads the archive at
   **`~/ai-agent-library/on-demand`**. Two different roots.
2. `activate` performs a live symlink/junction with **no manifest,
   no dry-run, no rollback** (installer has rollback; activation doesn't).
3. No `doctor` diagnostic for broken links, catalog mismatch, or
   interrupted operations.
4. No shared archive-path configuration between scripts.
5. Stale-index errors are fail-closed but give no incremental path
   (full `library_catalog.py` rerun required).
