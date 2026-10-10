# Agent compatibility (honest status)

`install_agents.py` registers the MCP server for 8 clients by editing their
native configs (with backup + rollback). Registration is verified
functionally: the configured interpreter must launch the server and
advertise the contract tools. Nothing below claims a live skill-load test
except where stated.

| Agent | Skill root | Link mechanism | Reload needed | Status |
|---|---|---|---|---|
| codex | `.codex/skills` | symlink (junction fallback on Windows) | restart client | registration verified; live load untested |
| claude | `.claude/skills` | symlink | restart client | registration verified; live load untested |
| gemini | `.gemini/skills` | symlink | restart client | registration verified; live load untested |
| antigravity | `.gemini/config/skills` | symlink (junction fallback on Windows) | restart client | registration verified; live load untested |
| hermes | `.hermes/skills` | symlink | restart client | registration verified; live load untested |
| cursor | `.cursor/skills` | symlink (junction fallback on Windows) | restart client | registration verified; live load untested |
| opencode | `.config/opencode/skills` | symlink (junction fallback on Windows) | restart client | read/load exercised live through this project's own session |
| omp | `.omp/agent/skills` | symlink | restart client | registration verified; live load untested |

## Path handling

- Windows: symlink first, NTFS junction fallback via PowerShell; `--copy`
  (`method: copy`) when neither link type is usable. Copies are hash-verified
  at activation and never deleted after user modification.
- WSL/Linux: POSIX symlinks; same manifest/lockfile format, portable across
  all three.
- Unsupported agent names are rejected (`UNSUPPORTED_AGENT`); paths are
  never guessed.

## Read-only path

Agents without MCP support use the CLI directly (`search`/`show` reading the
cached index, `activate --dry-run` for plans). No MCP capability is claimed
without the functional launch check in `install_agents.py`.
