# Finder ↔ Stack Compatibility Matrix

| Contract item | Finder 0.2.x | Stack Contract v1 adapter | Status |
|---|---|---|---|
| Version handshake | MCP `get_contract_info` returns contract/version/features | Rejects missing/unsupported versions | Tested over stdio MCP |
| Source registration | Documented `library_catalog.py register-source` CLI | Registers `aes` from validated manifest; verifies all IDs through MCP | Tested in disposable library |
| Stable IDs/revisions | `<source>:skill:<slug>`; content hash changes independently | Verifies exact IDs and requested revisions | Tested |
| Search/metadata | Bounded `search_capabilities` and exact `get_capability` | Preserves personal Tier 2 matches as metadata | Tested |
| Single-skill read | `load_skill(id, revision?)` | Loads exact selected body only, after trust/compatibility checks | Tested |
| Activation preview | `prepare_activation` is a dry-run | Fingerprints preview; requires exact approval and rechecks before mutation | Tested |
| Lifecycle | `activate_skill`, `deactivate_skill` enforce trust/ownership | Applies only approved activation and managed deactivation | Tested in disposable home |
| Finder outage/old contract | Server unavailable or no v1 handshake | Explicit incompatibility; standalone Stack routing remains available | Unit-tested |

Stack calls only Finder's documented MCP tools and source-registration CLI. It
does not import Finder Python modules or write Finder indexes, trust state,
locks, or activation manifests.

The hosted review pairing pins Finder `fcb6ce9e9f977eb553ad7b8f23d9fa6db375bb76` and Stack revision `6bff1d582c037f86bce365ae8a5011266eac20b2`. The paired Windows/Ubuntu Python 3.11/3.12 run passed: https://github.com/BlguunBN/agentic-engineering-stack/actions/runs/38028044978. This is review-snapshot evidence, not a published compatibility release; keep pins immutable and do not float on `main`.
