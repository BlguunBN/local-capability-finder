# Contributing

Contributions to the CLI, MCP server, installer, and documentation are welcome.
Please keep changes focused and avoid adding generated catalogs or personal skill
libraries to the repository.

## Local development

Use Python 3.11 or newer. Node.js 18 or newer is needed for the optional CLI.
Install `PyYAML` to exercise YAML MCP configuration support:

```sh
python -m pip install PyYAML
python -m unittest discover -s tests -v
node --check bin/capfind.js
node bin/capfind.js --help
npm pack --dry-run
```

The unit tests use temporary directories. `tests/benchmark_queries.py` checks
search quality against a particular local skill catalog and requires
`tiktoken`; it is optional for contributors with a different catalog.

## Pull requests

- Describe the behavior changed and the reason for it.
- Include a focused test for a bug fix or new behavior when practical.
- Report the commands run and their results.
- Keep local paths, generated indexes, MCP credentials, agent configuration,
  archives, and skill folders out of commits. The `.gitignore` is an allowlist
  for source and documentation files.

For security concerns, follow [SECURITY.md](SECURITY.md) instead of opening a
public issue with exploit details.
