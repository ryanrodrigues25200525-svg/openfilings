# Contributing

## Local quality gates

Use Python 3.11 or newer and install the locked development environment:

```bash
uv sync --locked --all-extras --dev
uv run ruff check src tests
uv run ruff format --check src tests
uv run pytest
```

Adapters must use official public sources, validate download hosts and paths,
enforce request and document limits, and have deterministic offline tests.
Network-dependent tests belong in the bounded smoke runner, not the offline
suite. Never add secrets or recorded responses containing personal data.

## Release checklist

1. Update the version and changelog.
2. Run the local quality gates on the locked environment.
3. Build both distributions with `uv build` and run `tests/smoke_package.py`
   against each artifact in an isolated environment.
4. Run the strict dependency audit and keyless live smoke suite.
5. Tag only the exact commit that passed every gate.

If live regulator checks regress after release, stop distribution, revert the
affected adapter or release commit, and publish a patch release. Cached data is
additive and source documents are not retained, so rollback does not require a
destructive database migration.
