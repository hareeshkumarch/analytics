# 05 - Python dependency lock (fixed)

## Current status

Fixed. `pyproject.toml` keeps the supported dependency ranges, and two hash-pinned lock files
capture the resolved transitive versions for the declared Python `>=3.11,<3.14` range:

- `backend/requirements.lock`: runtime dependencies, installed by `infra/compose/Dockerfile.backend`.
- `backend/requirements-dev.lock`: runtime plus dev tools, installed by CI.

Both install with `pip install --require-hashes -r <lock>`, then the project itself with
`pip install --no-deps`. `pip check` then fails the build if `pyproject.toml` asks for something the
lock doesn't provide, so a dependency added without regenerating the lock is caught.

## Regenerating

Run `make lock` after changing direct dependencies, and commit the lock files with the change.

## Verified

- A clean Python 3.13 virtualenv installs `requirements-dev.lock` with `--require-hashes`, `pip check`
  passes, and the backend suite passes.
