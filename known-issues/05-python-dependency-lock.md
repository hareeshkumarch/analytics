# 05 - Python dependency lock is missing

## Current status

The incompatible direct pins have been removed. `pyproject.toml` is now the source of dependency
metadata (the old delegating `requirements.txt` was deleted as unused), and CI and Docker install
from the project metadata. Setuptools package discovery is explicitly limited to `app` so non-package
directories such as `config` and `tests` do not break Docker builds. The backend tooling targets
Python 3.11, matching the declared minimum version.

The remaining issue is reproducibility: several runtime dependencies use open `>=` ranges, and no
lock file captures the resolved transitive versions. Separate builds can therefore install different
stacks over time.

## Fix

1. Resolve the project in a clean Python 3.13 environment and generate a committed lock file from
   `pyproject.toml` using `uv lock` or an equivalent lock tool.
2. Install the lock in CI and Docker, and keep the `pyproject.toml` constraints as the supported
   dependency ranges.
3. Regenerate the lock deliberately when changing direct dependencies.

## What to verify

- `pip install -e "backend[dev]"` resolves from a clean environment.
- Docker and CI install the same locked runtime dependency versions.
- The lock supports the declared Python `>=3.11,<3.14` range or the project narrows that range.
