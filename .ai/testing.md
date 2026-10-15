# Testing and Verification

Verification must match the risk and layer of the change. Focused tests accelerate iteration; project-wide gates establish delivery readiness.

## Canonical Quality Gate

From the repository root with the project virtual environment installed:

```bash
make quality
git diff --check
```

`make quality` runs:

```bash
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/mypy
.venv/bin/pytest
```

Ruff enforces linting and formatting, mypy checks `src/job_search` in strict mode, and pytest runs the configured suite. Formatting with `.venv/bin/ruff format .` mutates files and should be used only within the approved scope.

## Read-Only Review Mode

`make quality` must not be described as read-only. Its current commands do not intentionally rewrite source files, but mypy and pytest may create caches or test artifacts, and individual tests may have additional side effects. Inspect the applicable tests and tool configuration before running them in a read-only review.

When verification must avoid repository writes, contain known tool output in a new temporary directory and disable optional caches where supported. For example, after creating a unique temporary directory outside the repository:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/ruff check --no-cache .
PYTHONDONTWRITEBYTECODE=1 .venv/bin/ruff format --check .
.venv/bin/mypy --cache-dir /private/tmp/jobscout-review-UNIQUE/mypy
PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest -p no:cacheprovider --basetemp=/private/tmp/jobscout-review-UNIQUE/pytest
```

Replace `UNIQUE` with a newly created, task-specific path. `PYTHONDONTWRITEBYTECODE=1` or `python -B` suppresses Python bytecode generation only; it does not make arbitrary tests side-effect-free. Tests may still create temporary files, logs, databases, caches, or external effects. Use synthetic data and isolated temporary databases, never the real database, credentials, sessions, or external services. Verification that cannot be safely contained requires explicit authorization, and every observed side effect must be disclosed.

## Focused Tests

Run the smallest relevant node or file during implementation, for example:

```bash
.venv/bin/pytest tests/job_search/unit/test_collection_service.py
.venv/bin/pytest tests/job_search/integration/test_atomic_collection_persistence.py
```

Choose actual test paths from the repository rather than copying examples blindly. Focused success does not replace `make quality` for a deliverable change.

## Database and Migration Safety

Never point tests or Alembic at the real `job_search.db`. Use a new disposable path outside the repository, then run both the upgrade and schema drift check:

```bash
export JOB_SEARCH_DATABASE_URL='sqlite+aiosqlite:////private/tmp/jobscout-check/database.db'
.venv/bin/alembic upgrade head
.venv/bin/alembic check
unset JOB_SEARCH_DATABASE_URL
```

Create a unique temporary directory for each run and remove it only after confirming the path. Migration changes require upgrade-path coverage in addition to `alembic check`.

## Strategy by Change Type

- Domain logic: unit tests for normal, boundary, and invalid states.
- Application services: port fakes and tests for orchestration, ordering, and failure semantics.
- Source adapters: deterministic synthetic payloads and contract tests for empty, malformed, partial, paginated, and provider-error cases; no live API calls.
- Classification: deterministic classifier regression cases that protect accepted and rejected vacancy families.
- Persistence: isolated temporary SQLite integration tests for atomicity, rollback, uniqueness, stale ordering, ownership, concurrency, and lifecycle reconciliation.
- Migrations: clean upgrade, upgrade from the prior revision where applicable, and model/schema drift check.
- CLI: parsing and service-boundary tests without live collection or real runtime state.
- Legacy Telegram collector: pure filtering/state tests with mocked clients; no real Telegram session.
- Documentation-only: `git diff --check`, path/command validation, and the full quality gate unless the task explicitly narrows it.

## Result Reporting

Classify every requested check accurately:

- **Executed:** ran to completion; report pass/fail and useful counts.
- **Skipped:** deliberately not run; give the scope-based reason.
- **Unavailable:** the environment lacks a required tool or dependency.
- **Blocked:** attempted or required, but an external condition prevented completion.

Do not describe skipped, unavailable, or blocked checks as passing. If a required gate fails, stop delivery unless the task explicitly asks for diagnosis or remediation.

## Whitespace Coverage

`git diff --check` checks unstaged changes to tracked files; it does not inspect untracked files or the staged diff. Review all three states as applicable:

```bash
git diff --check
git diff --cached --check
git diff --no-index --check /dev/null path/to/untracked-file
```

Run the no-index command separately for each newly created untracked file. Exit status `1` can mean only that the files differ, which is expected for a new file; a nonzero status alone does not prove a whitespace defect. Inspect the command's actual whitespace diagnostics. Once a new file is staged, `git diff --cached --check` covers it.
