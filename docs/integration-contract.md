# Integration contract v1 (Finder ↔ Engineering Stack)

Status: implemented in Finder for skills; Stack S5 stays mock-only until it
adopts this document. Changes here require a joint version bump.

## Record schema (`schema_version: 1`)

Skills carry stable IDs `<source>:skill:<slug>` (e.g.
`aes:skill:systematic-debugging-suite`). Previous `skill:<hash>` IDs keep
resolving via `legacy_ids`. Content revisions change `content_hash`
(`sha256:…`), never the stable ID. Same-name records from different sources
keep distinct IDs and cross-link through `alternate_versions`.

Fields: `id, kind(skill), name, description, source_id, source_path,
version|null, tags[], compatible_agents[], requirements[], content_hash,
trust_status, installation_state, managed_targets[], legacy_ids[]`.

- `trust_status`: `approved | unscanned | needs_review | blocked`.
  `approved` records a specific decision (first-party local roots, or an
  explicit source registration), not a safety guarantee.
- `installation_state`: `available | archived | broken` (`broken` is
  evaluated at read time when the source file is gone).
- `managed_targets[]`: `{agent, path, method, revision|null}`; `method` is
  the link kind for Finder-managed activations, `unmanaged` for
  pre-existing native installs the Finder did not create.

Tools and plugins keep their existing hash IDs in v1; skill coverage is
normative, tool/plugin canonicalization is deferred.

## MCP surface (8 tools, bounded `tools/list`)

Read-only (never install, mutate, download, or execute):

| Tool | Inputs | Output | Rules |
|---|---|---|---|
| `search_capabilities` | `query`, `kind?`, `limit?` (1–20, default 3), `agent?`, `project_hint?` (≤200 chars) | `{id, type, name, reason, availability, matched}[]` | never full content |
| `get_capability` | `capability_id` | full record + `usage` | no execution |
| `load_skill` | `capability_id`, `revision?` | `{id, name, source_path, revision, trust_status, content}` (SKILL.md only) | `STALE_REVISION` on mismatch |
| `get_tool_schema` | `capability_id` | `{id, server, tool, description, schema\|null, note?}` | stored schemas only (captured by explicit `--probe-tools` refresh) |

State-changing (explicit; idempotent where safe):

| Tool | Behavior |
|---|---|
| `prepare_activation` | dry-run plan + `findings[]` + `approval_required`; zero filesystem changes |
| `activate_skill` | applies an approved plan, writes manifest + lockfile; `already_active` when present; `confirm_untrusted` required for `needs_review`/`unscanned`; `blocked` always refused |
| `deactivate_skill` | removes only a managed link (`already_removed` when absent; refuses unmanaged paths) |
| `refresh_catalog` | rebuilds the index; `{indexed, path}` |

Every tool call appends one JSON line to `<library>/_audit.log`
(tool, status, scalar arguments, error code). No credentials are logged.

## Errors

JSON-RPC `-32602` with `data: {code}` for contract failures:

`NOT_FOUND, AMBIGUOUS_ID, UNTRUSTED, UNSUPPORTED_AGENT, CONFLICT,
INVALID_PATH, STALE_REVISION, PERMISSION_DENIED, ROLLBACK_FAILED`
(+ `EXECUTION_FAILED` for unexpected backend failures).
`activate_skill` on an occupied target returns `CONFLICT`, never overwrites.

## Approval rule

Durable activation requires approval: an explicit `confirm_untrusted`
(or CLI `--yes`) for anything not `approved`; interactive CLI use counts as
its own approval channel for `approved` skills. Hosts without a reliable
approval channel must fail closed via `prepare_activation` findings.

## Compatibility

| Finder | Stack | Notes |
|---|---|---|
| 0.1.x + P1–P4 (this branch) | any Stack reading only `source_path` + SKILL.md | works today; no Stack changes needed |
| Contract v1 final | Stack S5+ (adopts `load_skill`/registration/approval) | joint sign-off required |

Pinned verification (this session): Finder branch
`capfind/plan-implementation` at P8 registers the companion checkout and
resolves all 18 suites as `aes:skill:*` with no native installation; joint
fixture suite `tests/test_p8_joint.py` (10 scenarios) passes. No
`main`-coupling: releases pin a Finder range against a Stack range.

Minor releases preserve read-only v1 compatibility. The Finder never imports
Stack code; the Stack must use this public surface. No `main`-coupling:
pin a Finder release range against a Stack release range at ship time.
