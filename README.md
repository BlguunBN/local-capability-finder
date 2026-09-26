# Local capability finder

Search installed agent skills, MCP tools, and Codex plugins from one cached
index. Search returns at most three short matches by default. Use an exact ID
to inspect one result or activate one skill for one agent.

This repository contains code and documentation only. Local skills, archive
folders, generated indexes, and machine configuration are excluded from Git.
By default, catalog data is stored in `~/ai-agent-library`, regardless of where
the code is cloned or installed. Agent skill folders are scanned under the
current user's home directory.

## Requirements

- Python 3.11 or newer
- Node.js 18 or newer for the optional `capfind` CLI
- Optional `PyYAML` for full YAML MCP metadata and Hermes profile discovery
  (`pip install PyYAML`); without it, the shared registry supplies server names only
- Agent CLIs for MCP registration; the installer skips clients it cannot find

## Install for agents

Run in this folder on Windows:

```powershell
python install_agents.py --dry-run
python install_agents.py
```

The installer refreshes the local index and registers the stdio MCP server with
installed Codex, Claude Code, Gemini, Antigravity, Hermes, Cursor, OpenCode, and OMP clients.
Select clients with `--agents codex claude`, or skip catalog refresh with
`--skip-refresh`. Repeating the command leaves matching registrations alone;
same-name entries pointing elsewhere or disabled are reported as conflicts.
Codex, Claude Code, Gemini, and Hermes use their installed CLIs. Cursor,
Antigravity, OpenCode, and OMP JSON configurations are backed up before an entry is added. Restart
an agent after installation so it discovers the three MCP tools.

The tools are `search_capabilities`, `get_capability`, and `activate_skill`.
The stdio server supports MCP's `initialize` handshake through protocol
revision `2025-11-25`. Clients that probe the `2026-07-28` stateless revision
need legacy fallback enabled.

### Interactive CLI

From the cloned repository, run:

```powershell
npx . add
```

`add` shows an interactive agent picker. Use arrow keys to move, space to select,
and Enter to confirm. It registers the same MCP server for the selected clients.
The CLI also works without interaction:

```powershell
npx . add --agent codex --agent cursor
npx . --version
npx . search "inspect a CAD file" --json
npx . show skill:EXACT_ID
npx . activate skill:EXACT_ID --agent codex
npx . refresh
```

Run `npx .` from the cloned repository; the package is not published to the npm
registry. Use `node bin/capfind.js` instead if preferred. `setup` is an alias
for `add`. When run from a package installation outside a Git clone, setup
copies the Python server to a stable user data directory before registering it,
so the MCP configuration does not point into a temporary package cache.
`CAPFIND_PYTHON` can select a Python 3.11+ executable when automatic detection
does not find one. `capfind --version` prints the CLI package version without
starting Python.

### Setup prompt for a new agent

Copy this prompt into a new agent's instructions after cloning the repository.
The agent should use the clone's actual absolute path and its own client name.

```text
Set up and use the local capability finder in this repository.

Location:
- Project: the clone containing install_agents.py and capability_mcp.py
- MCP server: capability_mcp.py in that clone
- MCP server name: local-capability-finder
- Optional local MCP registry: mcp_servers.yaml in that clone (it may not exist)

Find the clone's absolute path. If it is not available, ask for its location.
Check whether local-capability-finder is already connected. If it is missing
and you can edit your own MCP configuration, register it as a stdio server:
  command: python
  args: [<absolute path to this clone's capability_mcp.py>]

For Codex, Claude Code, Gemini, Antigravity, Hermes, Cursor, OpenCode, or OMP on Windows, run
this command from the clone's root and select agents in the interactive menu:
  npx . add

For automated setup, specify the client name directly:
  npx . add --agent YOUR_AGENT_NAME

Restart the agent after registration so it discovers the MCP tools. For other
MCP clients, use their own configuration format with the same server command
and argument.

When a task may need a specialized skill, MCP tool, or plugin:
1. Call search_capabilities with a short description of the need. Keep its
   default limit of three.
2. Call get_capability with the exact ID of the best match.
3. Follow its usage details. Read an on-demand skill's SKILL.md when needed.
4. Call activate_skill with that exact skill ID and your agent name only if
   your client requires an active skill folder.

Do not load the entire skill library into the conversation. Search does not
install or activate anything. If no result fits, continue with normal tools.
```

For other MCP clients, adapt [mcp_servers.example.yaml](mcp_servers.example.yaml)
to that client's configuration. The manual server command can be `python` with
the absolute path to `capability_mcp.py` as its argument; the installer records
the absolute path to the Python interpreter it uses.

## CLI

```powershell
python library_catalog.py
python capability_finder.py search "inspect a CAD file" --json
python capability_finder.py search "browser automation" --kind tool
python capability_finder.py show skill:EXACT_ID
python capability_finder.py activate skill:EXACT_ID --agent codex
```

`library_catalog.py` writes `skills_index.json`, `skills_index.md`, and
`skills_index.tsv` with local paths. It also refreshes
`capabilities_index.json`. Searches read the index and do not scan folders or
run a model. A newer source catalog or changed indexed MCP configuration
triggers a refresh error. Skill-store directory changes require an explicit
`python library_catalog.py` refresh; routine activations and installs do not
interrupt searches. The generated Markdown
catalog is for human inspection; do not load it into an agent prompt.
`library_catalog.py --if-stale HOURS` still refreshes when an indexed source
file changed, even if the catalog is younger than the age threshold.
`capability_finder.py refresh --probe-tools`
optionally probes local command-based MCP servers for tool names, with a
five-second limit per server. Probing skips servers that declare environment
variables and package runners such as `npx` or `uvx`; those servers remain
searchable by server name. Without PyYAML, the shared YAML registry contributes
server names only and the active Hermes YAML profile is not read. Cached probed
tool names expire after seven days or when the server config or local launch
file changes. The catalog covers 21 configured activation stores, including
OpenClaw AutoClaw, plus the Codex
plugin cache, and the on-demand archive.

An archived skill has `availability: ["on-demand"]`. `show` returns its exact
`SKILL.md` path. Reading that file directly avoids adding it back to an
agent's startup skill list; `activate` is for clients that require an active
skill folder. Search never activates or installs anything.

## Token use

### Find one skill without loading the whole library

In a local snapshot with the AutoClaw and Antigravity roots included,
the catalog held **3,557 skill entries**. Their names and
descriptions totaled **1,026,935 characters** (roughly **257,000 tokens** using
the simple four-characters-per-token estimate). The combined `SKILL.md` files
occupied **35.0 MB**. These are local corpus sizes, not tokens automatically
sent to an agent.

For the query `form validation zod`, the finder returned `zod-validation-expert`
among its short matches. The compact search result was 456 characters, its
detail record was 1,749 characters, and the selected `SKILL.md` was 9,907
characters. Together that is about **12,100 characters**, or **3,000 estimated
tokens**. The agent can inspect the exact skill it needs without receiving
the other skill descriptions or files.

| Discovery approach for this example | Approximate text supplied to the agent |
| --- | ---: |
| Send every skill name and description | 1,027,000 characters (~257,000 estimated tokens) |
| Search, inspect one result, read one skill | 12,100 characters (~3,000 estimated tokens) |

That is **about 98.8% less discovery text** than sending the complete name and
description catalog for this example. The estimate uses character counts
divided by four; it is not a tokenizer measurement or a claim of 98.8% lower
billed usage. MCP tool definitions, calls, caching, conversation history, and
agent-specific startup behavior also affect token use. Registering this MCP
server does not remove skills that an agent already loads into its startup
manifest. To reduce that startup cost, trim the agent's configured skill roots
or reversibly archive specialist skills after confirming they remain
discoverable through the finder.

`skill_archive.py` supports a reversible on-demand archive. It makes a dry-run
manifest by default; `--apply` moves only its listed skills, and
`--restore --manifest NAME.json` restores them.
Archive and restore refresh the indexes. Rebuild the optional categorized
junction view afterward with `python organize_skills.py`.

## Local files

| Path | Purpose |
| --- | --- |
| `capability_finder.py` | Cached search, detail, and exact skill activation |
| `capability_mcp.py` | Thin stdio MCP adapter |
| `install_agents.py` | Idempotent MCP registration for installed clients |
| `library_catalog.py` | Local skill catalog refresh |
| `skill_archive.py` | Reversible on-demand archive |
| `organize_skills.py` | Optional categorized junction view; uses Laya only for ambiguous categories |
| `find_skills.py` | Legacy catalog search |
| `tests/` | Finder unit tests and representative query benchmark |

The index includes local paths and should stay out of Git. The repository
`.gitignore` allows only source and documentation files.

## License

Apache-2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).

See [RELEASING.md](RELEASING.md) for the release procedure.

## Contributing and security

See [CONTRIBUTING.md](CONTRIBUTING.md) for development and pull request guidance.
Report vulnerabilities privately as described in [SECURITY.md](SECURITY.md).
