# Integration Contract v1 (Finder ↔ Engineering Stack)

Status: implemented in Finder 0.2.x. Stack consumes the public MCP tools and the documented source-registration CLI. Changes to the semantic contract require a major-version change; MCP transport version is separate.

## Identity and ownership

Skill IDs are stable `<source>:skill:<slug>` values (for Stack: `aes:skill:<suite-folder>`). Content changes update `content_hash`, never the canonical ID. Same-name skills from different sources remain separate IDs. Stack owns suite content and manifest; Finder owns its derived index, trust, activation manifests, and installation lifecycle.

Register an explicit source with `library_catalog.py register-source --id aes --path <stack>/suites`. Refresh the catalog afterward. This registers metadata only; it does not install or activate any skill. Source registration is available through the documented CLI, not agent-callable MCP, to keep trust decisions explicit.

## Version handshake

The MCP tool `get_contract_info` returns `{contract, version, features, registration}`. Contract v1 identifies as `local-capability-finder`, version `1`; do not infer it from the MCP transport protocol or the package version. Stack must fail clearly if the handshake is absent or required features are missing.

## MCP surface (9 tools)

Read-only: `get_contract_info`, `search_capabilities`, `get_capability`, `load_skill`, `get_tool_schema`.

State-changing/maintenance: `prepare_activation` (dry-run), `activate_skill`, `deactivate_skill`, `refresh_catalog`.

`search_capabilities` returns bounded metadata only. `load_skill(id, revision?)` returns one SKILL.md and rejects stale revisions. `get_capability` includes `trust_status`, `compatible_agents`, and content hash. `get_tool_schema` returns a captured schema or an explicit null descriptor.

## Approval and safety

`prepare_activation` makes no filesystem changes and returns a plan plus findings. Stack records an exact approval (`action`, `target`, `plan_id`, approver, reference), fingerprints the preview, and obtains a new preview immediately before calling `activate_skill`. Finder blocks `blocked` skills and requires `confirm_untrusted` for `needs_review` and `unscanned` content. Deactivation is restricted to Finder-managed targets; unmanaged files are never overwritten or removed.

Search and load do not activate or execute skills. Search results are untrusted metadata. Read-only calls and state-changing calls are audited without credentials.

## Errors

Contract failures use stable `data.code` values, including `NOT_FOUND`, `AMBIGUOUS_ID`, `UNTRUSTED`, `UNSUPPORTED_AGENT`, `CONFLICT`, `INVALID_PATH`, `STALE_REVISION`, `PERMISSION_DENIED`, and `ROLLBACK_FAILED`. Unexpected backend failures are marked `EXECUTION_FAILED`. `AMBIGUOUS_ID` is reserved: duplicate names never error, they resolve by exact ID with alternatives listed in `alternate_versions`.

## Compatible Stack

Stack 0.1.x Contract v1 adapter uses Finder 0.2.x. Its stdio integration registers all 18 Stack IDs, searches and loads one exact suite, keeps personal capabilities discoverable, and tests approved activation/deactivation in a disposable home. Hosted paired CI passed on Stack revision `6bff1d582c037f86bce365ae8a5011266eac20b2` with Finder pinned to `fcb6ce9e9f977eb553ad7b8f23d9fa6db375bb76`: https://github.com/BlguunBN/agentic-engineering-stack/actions/runs/38028044978. These review snapshots do not constitute a published release; do not depend on floating `main` branches.
