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

For interactive setup from npm:

```sh
npx local-capability-finder-cli add
```

To use a cloned checkout instead:

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

The tools are `search_capabilities`, `get_capability`, `load_skill`,
`get_tool_schema`, `prepare_activation`, `activate_skill`,
`deactivate_skill`, and `refresh_catalog` (Contract v1, documented in
`docs/integration-contract.md`). Errors carry a stable `data.code`
(`NOT_FOUND`, `CONFLICT`, `UNTRUSTED`, `UNSUPPORTED_AGENT`,
`STALE_REVISION`, …), and every call is audit-logged locally.
The stdio server supports the `2026-07-28` stateless `server/discover` flow and
the legacy `initialize` handshake through `2025-11-25`.

### Interactive CLI

From npm, run:

```powershell
npx local-capability-finder-cli add
```

`add` shows an interactive agent picker. Use arrow keys to move, space to select,
and Enter to confirm. It registers the same MCP server for the selected clients.
The CLI also works without interaction:

```powershell
npx local-capability-finder-cli add --agent codex --agent cursor
npx local-capability-finder-cli --version
npx local-capability-finder-cli search "inspect a CAD file" --json
npx local-capability-finder-cli show skill:EXACT_ID
npx local-capability-finder-cli activate skill:EXACT_ID --agent codex
npx local-capability-finder-cli refresh
```

Run `npx .` from a cloned checkout, or use `node bin/capfind.js` there.
`setup` is an alias
for `add`. When run from a package installation outside a Git clone, setup
copies the Python server to a stable user data directory before registering it,
so the MCP configuration does not point into a temporary package cache.
`CAPFIND_PYTHON` can select a Python 3.11+ executable when automatic detection
does not find one. `capfind --version` prints the CLI package version without
starting Python.

### Setup prompt for a new agent

Copy this prompt into a new agent's instructions. The agent should use its own
client name and install from npm, or use an existing checkout if available.

```text
Set up and use local-capability-finder-cli.

Location:
- npm package: local-capability-finder-cli
- MCP server name: local-capability-finder

Check whether local-capability-finder is already connected. If it is missing,
register it with the CLI. Python 3.11+ and Node.js 18+ are required.

For Codex, Claude Code, Gemini, Antigravity, Hermes, Cursor, OpenCode, or OMP,
run this command and select agents in the interactive menu:
  npx local-capability-finder-cli add

For automated setup, specify the client name directly:
  npx local-capability-finder-cli add --agent YOUR_AGENT_NAME

For automatic discovery from phrases such as "build this with the best skills":
  npx local-capability-finder-cli auto enable --agent YOUR_AGENT_NAME

Restart the agent after registration so it discovers the MCP tools. For other
MCP clients, clone the repository and configure a stdio server with the absolute
path to capability_mcp.py and a Python 3.11+ interpreter.

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

## Automatic skill discovery

`capfind auto` installs a short managed `find-best-skills` instruction. For a
matching task, the agent searches through MCP, inspects an exact result, reads
only the selected `SKILL.md`, and continues the user's task. Optional prompt
hooks search the same cached index before the agent starts and supply IDs.
Search returns at most three matches; the agent may retry once with clearer
terms and inspect at most two results. It uses one primary skill and, when
useful, one complementary skill. Search never installs or activates a skill.

```powershell
capfind auto test "Help me build a dashboard with the best skills" --json
capfind auto enable --agent codex --mode explicit --dry-run
capfind auto enable --agent codex --mode explicit
capfind auto status --agent codex
capfind auto disable --agent codex
```

`explicit` is the default and reacts to requests that mention skills. `smart`
is opt-in and also considers specialized tasks without that phrase. The local
router ignores quoted examples, trivial prompts, opt-outs, and repeated
follow-ups. `auto test` previews its trigger and search query without changing
settings. Enable and disable touch only marked finder instructions and back up
existing agent files before editing.

Claude Code and Gemini CLI can use a per-prompt hook:

```powershell
capfind auto enable --agent claude --mode explicit --hook
capfind auto enable --agent gemini --mode smart --hook
```

Those hooks inspect prompts locally and inject a short routing hint when the
trigger fires. Other supported agents use the managed instruction; following
it depends on the client. An MCP server alone cannot see every user prompt or
force a tool call. Restart the client after enabling automation. Routing and
search run locally without Laya or another model. Hermes and Cursor use a
managed block in the home directory's `AGENTS.md`; whether that file applies
outside the home tree depends on the client and workspace.

## CLI

```powershell
python library_catalog.py
python capability_finder.py search "inspect a CAD file" --json
python capability_finder.py search "browser automation" --kind tool
python capability_finder.py search "deploy Cloudflare Workers" --kind skill --agent codex --project-hint "Cloudflare Workers"
python capability_finder.py show skill:EXACT_ID
python capability_finder.py activate skill:EXACT_ID --agent codex --dry-run
python capability_finder.py activate skill:EXACT_ID --agent codex
python capability_finder.py activate skill:EXACT_ID --agent codex --copy
python capability_finder.py deactivate skill:EXACT_ID --agent codex
python capability_finder.py doctor
```

`activate --dry-run` prints the plan without changing anything. `deactivate`
removes only a finder-managed link. `doctor` reports broken links, stale
indexes, and interrupted operations. `CAPFIND_HOME`, `CAPFIND_LIBRARY`, and
`CAPFIND_ARCHIVE` override the default roots (the archive vault defaults to
`~/ai-agent-library/on-demand`).

### Skill sources and stable IDs

```powershell
python library_catalog.py register-source --id aes --path C:\path\to\agentic-engineering-stack\suites
python library_catalog.py list-sources
python library_catalog.py unregister-source --id aes
```

Skills carry stable IDs of the form `<source>:skill:<slug>` (for example
`aes:skill:systematic-debugging-suite`). Older `skill:<hash>` IDs still
resolve. Activations are recorded with their exact content revision in
`activations-lock.json`; editing a skill changes its hash, never its ID.

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

To compare active skill-list footprints before and after a batch, save two
snapshots with `python startup_inventory.py --output before.json` and
`python startup_inventory.py --output after.json`. Each snapshot records active
skill counts and token counts for a normalized `name: description` list in
every installed agent root. Token counts use `cl100k_base` when optional
`tiktoken` is installed. They are a consistent proxy; actual startup prompt
tokens depend on each agent and require that agent's own telemetry.

In one local rollout, archiving 68 audited specialist skills removed 68
top-level skill entries from each of 19 active roots. The normalized lists
fell by 4,354 `cl100k_base` tokens per root (82,726 across those roots).
Archived folders remained searchable and an exact-ID activation was checked.
These figures measure skill-list text, not billed prompt tokens.

## Local files

| Path | Purpose |
| --- | --- |
| `capability_finder.py` | Cached search, detail, and exact skill activation |
| `capability_mcp.py` | Thin stdio MCP adapter |
| `auto_skill_router.py` | Local prompt trigger, query extraction, and duplicate suppression |
| `auto_install.py` | Reversible managed agent instructions and optional hooks |
| `auto_hook.py` | Claude Code and Gemini CLI prompt-hook adapter |
| `install_agents.py` | Idempotent MCP registration for installed clients |
| `library_catalog.py` | Local skill catalog refresh |
| `skill_archive.py` | Reversible on-demand archive |
| `startup_inventory.py` | Active skill-list footprint snapshot by agent root |
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
