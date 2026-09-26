# Shared Agent Library Instructions

This directory contains a portable capability finder for local AI agents.

## Read order

1. Search with `capability_finder.py` (or MCP `search_capabilities`)
2. Inspect one exact result with `show` (or MCP `get_capability`)
3. Read that result's `SKILL.md` only if it is a skill

## Find a skill before you improvise

Do not load the full catalog into context. Search the local index by intent:

```bash
python capability_finder.py search "<task>" --json
```

The output is at most three short results. Use `show <exact-id>` to get the
source path, then read only the selected instructions. Search does not activate.

Examples:

```bash
python capability_finder.py search "kubernetes manifests" --kind skill
python capability_finder.py search "browser automation" --kind tool
python capability_finder.py show skill:EXACT_ID
```

If the first search is thin, retry with different vocabulary. For maintenance
and exhaustive inspection, use the legacy finder or flat index:

```bash
python find_skills.py <keyword>
```

## Refresh the catalog

The catalog is a **cache**. The `SKILL.md` files are the source of truth.
Run a refresh after installing, removing, or editing skills:

```bash
python library_catalog.py
python library_catalog.py --if-stale 12 --quiet
```

`--if-stale HOURS` skips a young catalog only when its indexed source files are
unchanged. `--quiet` suppresses progress output.

That rewrites the three skill indexes and `capabilities_index.json`.

## Layout

| Path | Role |
| --- | --- |
| `skills/` | Optional junction to the live skills store |
| `skills_index.json` | Machine-readable catalog with real absolute paths |
| `skills_index.md` | Human-readable catalog; do not load into agent context |
| `skills_index.tsv` | One line per skill, for grep |
| `capability_finder.py` | Cached search, details, and exact skill activation |
| `capability_mcp.py` | Thin stdio MCP adapter |
| `install_agents.py` | MCP registration for supported agents |
| `skill_archive.py` | Reversible on-demand skill archive |
| `find_skills.py` | Legacy catalog search |
| `library_catalog.py` | Regenerates the skill and capability indexes |
| `mcp_servers.yaml` | MCP servers available to agents |
| `README.md` | Human-readable overview |
| `tests/` | Unit tests and query benchmark |

## Usage rule

Treat this folder as a knowledge pack. Use the files by path. The catalog covers
skills from every agent store on the machine (library, `.agents`, `.claude`,
`.openclaw`, `.codex`), deduplicated by real path, so a single search spans all
of them.

Do not assume Hermes-specific internals unless they are written here. Hermes
built-in tools are not files; they live inside Hermes.
