# Current state (P0 baseline, 2026-10-09; P1–P4 landed below)

Branch: `capfind/plan-implementation`. All measurements on an isolated
synthetic fixture unless noted. No user skill files were touched.

## P4 changes (2026-10-10)

- MCP surface is now 8 tools: added `load_skill` (exact-ID SKILL.md with
  revision check), `get_tool_schema` (stored probed schemas, honest
  descriptor otherwise), `prepare_activation` (dry-run + findings +
  `approval_required`), `deactivate_skill`, `refresh_catalog`.
- Contract errors as `data.code` (`NOT_FOUND`, `CONFLICT`, `UNTRUSTED`,
  `UNSUPPORTED_AGENT`, `STALE_REVISION`, `PERMISSION_DENIED`,
  `ROLLBACK_FAILED`, `EXECUTION_FAILED`); every call audited to
  `_audit.log` without credentials.
- Trust-gated activation: `blocked` always refused; `needs_review` /
  `unscanned` require MCP `confirm_untrusted` or CLI `--yes`.
- `docs/integration-contract.md` publishes Contract v1 for the companion
  Stack. Verified end-to-end over stdio: search → get → load serves one
  SKILL.md without loading anything else.

## P7 changes (2026-10-10)

- Read-only `metrics` snapshot (CLI + `collect_metrics`): capability counts
  by kind, index/catalog bytes, warm search p50/p95 over 8 representative
  queries, max result bytes, MCP handshake cost, lockfile and doctor
  rollups. No token claims — bytes only, estimates labeled as estimates.
- Live 5,811-record snapshot: search p50 ~103ms / p95 ~233ms, max result
  1,233 bytes, handshake ~0ms, 8 tools, doctor clean.
- Notable: while P4–P5 code was being written, a concurrent agent used the
  registered Finder to activate 2 skills for codex; manifests + lockfile
  were written correctly and doctor stayed clean.

## P6 changes (2026-10-10)

- `validate <id>` lints metadata/paths/references and runs heuristic
  supply-chain checks (remote-exec, credential exfiltration, embedded
  secrets, traversal, endpoints) over SKILL.md plus adjacent scripts —
  read-only, nothing is ever executed. Findings are severity-graded and
  explicitly heuristic, not proof.
- `trust <id> <status|clear> --reason` records manual approvals/blocks in
  `trust-overrides.json` with decided-by/at audit trail; `validate
  --apply-trust` records `needs_review` on high findings. Overrides apply
  live in `show()` (activation gates effective immediately) and bake in at
  refresh. Doctor reports `STALE_SCAN` when a skill changes after review.
- Optional `skillspector` adapter: availability/version recorded, never
  auto-run.

## P5 changes (2026-10-10)

- Link-first activation with hash-verified `--copy` fallback
  (`method: link|copy` on CLI/MCP, recorded in manifest + lockfile).
  Modified copies are never deleted; doctor reports copy drift.
- `docs/agent-compatibility.md` records honest per-host status
  (registration verified; live load untested except via this session).
- Live proof: `aes:skill:systematic-debugging-suite` dry-run → activate →
  verify → deactivate → zero residue → doctor clean on `trae`;
  overwrite-refusal proven on `opencode` (pre-existing native copy kept).

## Public entrypoints

- CLI: `capability_finder.py {refresh,search,show,activate}`,
  `library_catalog.py`, `skill_archive.py`, `install_agents.py`,
  `capfind auto {enable,status,disable,test}` (node `bin/capfind.js`).
- MCP tools: `search_capabilities`, `get_capability`, `load_skill`,
  `get_tool_schema`, `prepare_activation`, `activate_skill`,
  `deactivate_skill`, `refresh_catalog` (Contract v1, see
  `docs/integration-contract.md`).
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

## P3 changes (2026-10-10, branch `capfind/plan-implementation`)

- Search is now true BM25 (k1=1.2, b=0.75, per-field tf/idf with length
  normalization, field weights name 8 / category 2 / description 1 /
  aliases 3) over the previous weighted-keyword scorer. Agent, project-hint,
  quality, and exact-name bonuses are unchanged.
- Added technical synonym expansion (`k8s`→`kubernetes`, `ts`→`typescript`,
  …), exact-ID lookup (canonical or legacy returns that record first), and a
  `matched` field per result naming the contributing fields. Rows stay
  lightweight (no bodies).
- `collect()` skips unchanged files by (mtime, size) against the previous
  `file_state`; refresh stays atomic and a failed refresh keeps the last
  good `capabilities_index.json`.
- Retrieval benchmark: 105/105 labeled cases, fixture p50 1.7ms / p95 2.8ms.
  Live 5,816-record catalog: warm p50 ~104ms / p95 ~160ms — above the
  plan's 10k/<100ms aspiration. Reaching it needs a refresh-time inverted
  index (deferred; query-time scoring is correct but linear).
- Deferred: continuous file watching (an always-on daemon is out of scope
  for the first release); staleness detection + explicit refresh + hash-skip
  is the mechanism. Embeddings stay opt-out (no benchmark justified them).

## P2 changes (2026-10-10, branch `capfind/plan-implementation`)

- Canonical skill IDs `<source>:skill:<slug>` (e.g.
  `aes:skill:systematic-debugging-suite`); previous `skill:<hash>` IDs keep
  resolving via `legacy_ids`. Content edits change `content_hash`, never ID.
- Records carry `schema_version: 1`, `source_id`, `version`, `tags`,
  `compatible_agents`, `requirements`, `content_hash`, `trust_status`,
  `installation_state`, `managed_targets`.
- `library_catalog.py register-source --id aes --path <suites>`,
  `unregister-source`, `list-sources`; registry lives in
  `<library>/sources.json` (user data, never committed).
- `activate`/`deactivate` maintain `<library>/activations-lock.json`
  (exact revision + targets); refresh cost grows by one small read per
  skill for hashing.
- Live verification: companion Stack registered as `aes`; 18/18 suites
  resolve with no native installation; pre-existing native copies appear
  as honestly-labeled `unmanaged` managed-targets / alternate versions.

## P1 changes (2026-10-10, branch `capfind/plan-implementation`)

- `activate` is now `preview → validate → apply → verify → manifest`.
  `--dry-run` prints the plan with zero filesystem changes. Failures write
  a `failed` recovery manifest (with rollback attempt) instead of vanishing.
- `deactivate <id> --agent <name>` removes only a manifest-managed link;
  real directories and unmanaged paths are refused, never deleted.
- `doctor [--json]` reports broken variants, interrupted activations or
  archives, drift, lost archives, and stale/unreadable indexes.
- Archive vault default moved from the repo checkout to
  `~/ai-agent-library/on-demand`, shared with search. `CAPFIND_ARCHIVE`
  overrides it; manifests record their own root so legacy vaults still
  restore via `--manifest <path>`.
- `skill_archive.py` no longer requires the missing specialist CSV;
  archive candidates come from `skills_index.json` plus a live root scan.
- Shared path config: `CAPFIND_HOME`, `CAPFIND_LIBRARY`, `CAPFIND_ARCHIVE`.

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
