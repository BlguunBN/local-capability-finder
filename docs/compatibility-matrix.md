# Compatibility matrix

## Finder <-> Stack contract

| Item | Finder (this repo) | Stack expects (Contract v1 proposal) | Status |
|---|---|---|---|
| `search_capabilities` | implemented (lightweight rows) | same | compatible |
| `get_capability` | implemented (metadata + paths) | same | compatible |
| `load_skill` (exact-ID content) | **missing** (callers read `source_path`) | required | gap (P4) |
| `get_tool_schema` | missing | required if tool sources supported | gap (P4) |
| `prepare_activation` / `deactivate_skill` | missing | required | gap (P4/P5) |
| Stable IDs `<source>:<kind>:<slug>` | `skill:<hash16>`, no source part | required | gap (P2) |
| `schema_version`, trust/installation states | missing | required | gap (P2) |
| Source registration (`aes` namespace) | missing | required | gap (P2) |
| Structured errors (`NOT_FOUND`, `CONFLICT`, ...) | plain `ValueError` strings | required | gap (P4) |

Rule: Stack must use Finder's public MCP/CLI contract; Finder must stay
independently usable. No shared code imports. Versioned compatibility
table (Finder range <-> Stack range) replaces `main`-coupling at P8.

## Host adapters (honest status)

`install_agents.py` registers codex, claude, gemini, antigravity, hermes,
cursor, opencode, omp. Each path is config-file evidence only; per-host
read/load/activation verification is pending (P5). Unlisted agents are
unsupported, not guessed.
