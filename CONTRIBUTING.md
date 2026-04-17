# Contributing

## Development setup

```bash
git clone https://github.com/oliveres/spark-mcp-tui
cd spark-mcp-tui

cd spark-mcp
uv venv --python 3.11
source .venv/bin/activate
uv pip install -e ".[dev]"

cd ../spark-tui
uv venv --python 3.11
source .venv/bin/activate
uv pip install -e ".[dev]"
```

## Running checks

```bash
# In each package directory:
ruff check src tests
ruff format --check src tests
mypy src
pytest                      # unit tests only
pytest -m integration       # real cluster required (env vars set)
```

CI runs the same commands on Python 3.11, 3.12, and 3.13. Minimum
coverage gate: 80% line + branch on `spark-mcp/src/spark_mcp/`
(server.py is excluded because its tool bodies are integration-tested).

## Coding rules

- **English everywhere.** Code, comments, docstrings, error messages,
  commit messages. User-facing docs may be localized later.
- **No hardcoded values.** All cluster and behavioral knobs go through
  TOML config. Tests use fixtures, not inline strings.
- **Async first.** FastMCP tools, Cluster.run, httpx clients — all async.
- **Type hints everywhere.** mypy runs in `strict` mode.
- **Nine modules in `src/spark_mcp/`.** The cap is intentional; if you
  need a tenth, something should merge first.

## Security-sensitive areas

Changes touching any of these require extra care — they are the hot
paths that went through three review iterations:

- `spark_mcp.cluster.AsyncSshRunner.run` — remote argv is shell-escaped
  via `shlex.join`; never replace with a raw `" ".join(argv)` join.
- `spark_mcp.cluster._verify_key_permissions` — never remove; it is the
  fail-fast for insecure SSH keys.
- `spark_mcp.recipes.RecipeStore._path_for` — the regex + resolve check
  is the path-traversal guard; never soften.
- `spark_mcp.server.BearerAuthMiddleware` — the `secrets.compare_digest`
  path; never replace with `==`.
- `spark_mcp.cli._ssh_trust` — hostname regex and argv-list subprocess;
  never switch to `shell=True`.

## Pull requests

- Use conventional commits (`feat(spark-mcp): ...`, `fix(spark-tui): ...`,
  `docs: ...`, `test: ...`, `ci: ...`, `chore: ...`).
- Link to a GitHub issue when the change fixes or addresses one.
- If touching security-sensitive areas, tag the PR with `needs-security-review`.
- Update `CHANGELOG.md` under `[Unreleased]`.

## Release process

1. Update `CHANGELOG.md`: move `[Unreleased]` entries under a new
   `[x.y.z] - YYYY-MM-DD` heading.
2. Ensure every package's `pyproject.toml` version matches.
3. Commit and `git tag -s vX.Y.Z -m "vX.Y.Z"`.
4. Push tag: `git push origin main --tags`.
5. GitHub Actions builds wheels, publishes to PyPI, and drafts a release.
