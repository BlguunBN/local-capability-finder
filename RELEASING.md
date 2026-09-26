# Releasing

This repository packages the finder code only. Never add generated indexes,
skill folders, local MCP configuration, credentials, or machine-specific paths
to a release.

1. Update `package.json` version and the MCP `serverInfo.version` together.
2. Review the GitHub Actions results for Windows and Linux, Python 3.11 through
   3.13, and the npm package contents check.
3. Inspect the package with `npm pack --dry-run` and confirm it contains the
   CLI, Python runtime, README, LICENSE, and NOTICE, without local data.
4. Tag the reviewed commit and create a GitHub release.
5. Publish to npm manually only when the owner approves the package contents
   and release version. Confirm the published package and README on npm.

The package name is `local-capability-finder-cli`. Published name and version
combinations cannot be reused.
