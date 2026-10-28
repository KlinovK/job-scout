# Engineering Rules

These rules apply to all JobScout changes. `AGENTS.md` defines when to read the other guides.

## Correctness

- Prefer correctness over speed.
- Inspect current behavior and tests before proposing a change.
- State material assumptions explicitly and verify them where possible.
- Prefer explicit types, deterministic behavior, and precise failure semantics.
- Do not invent speculative behavior or present planned capability as implemented.
- Do not swallow exceptions silently; preserve or translate failure context at the correct boundary.
- Preserve established public behavior unless the task explicitly changes it.
- Handle empty, malformed, incomplete, stale, and failed inputs as distinct states where the model distinguishes them.
- Never claim a check passed unless it was executed successfully in the current working state.
- Add a regression test for every corrected defect when practical.

## Architecture

- Preserve the direction `Infrastructure -> Application -> Domain` and `Presentation -> Application -> Domain`.
- Domain code must not import SQLAlchemy, Pydantic, httpx, Telethon, CLI, or provider-specific infrastructure.
- Application services coordinate ports and domain behavior; infrastructure implements ports.
- Keep source-specific payload interpretation inside its adapter.
- Extend existing ports and value objects only when the domain needs a new contract.
- Avoid circular dependencies, service locators, hidden globals, and speculative frameworks.

## Collection and Data Integrity

- Keep source fetches and other network I/O outside database transactions.
- Represent source coverage and completeness explicitly with `SourceCollectionResult`.
- Do not reconcile absence from filtered subsets, rolling windows, malformed responses, partial pagination, or failed runs.
- Persist a completed collection atomically: vacancies, observations, classifications, source health, and eligible reconciliation must agree or roll back together.
- Reject stale completed runs before they mutate durable collection state.
- Scope observation lifecycle changes to the exact source-company ownership boundary.
- Preserve the active -> first miss -> second miss/closed lifecycle and authoritative reopen behavior.
- Derive canonical vacancy lifecycle only from the complete linked observation
  set inside accepted collection persistence; only persisted provider-explicit or
  authoritative-absence evidence permits closure, and ambiguous evidence prevents it.
- Make schema changes through Alembic, with upgrade-path tests and isolated databases.

## Secrets and Runtime State

- Never print, copy, stage, or commit secret values.
- Keep `.env`, Telegram sessions, SQLite databases and sidecars, collector state, logs, caches, and virtual environments ignored.
- Do not access the real database for tests or migration checks.
- Do not run live collection or send Telegram messages during development verification.
- Do not change credentials, installed launchd state, or local sessions without explicit authorization.

## Git Discipline

- Begin with `git status` and identify the expected baseline.
- Preserve unrelated and pre-existing user changes.
- Review `git diff` and `git diff --check`; review the staged diff before an authorized commit.
- Stage only files in the approved scope.
- Commit and push only when explicitly requested and only after required verification.
- Never force-push or rewrite published history unless the owner explicitly authorizes the exact operation.
- Never configure or change a remote unless explicitly requested.

## Scope Control

- Make the smallest coherent change that satisfies the stated goal.
- Do not mix feature work, refactoring, formatting churn, documentation redesign, or dependency upgrades without need.
- Do not change classifiers, keywords, lifecycle rules, adapters, Telegram behavior, scheduling, schema, or CI unless they are in scope.
- Reuse project conventions before introducing new abstractions or dependencies.
- Document actual behavior, not planned features.
- If an unexpected change or conflict prevents safe isolation, stop and report it.

## Completion

- Run focused tests for the changed behavior and the applicable gates in `.ai/testing.md`.
- Apply the severity and readiness rules in `.ai/review.md`.
- Report executed, skipped, unavailable, and blocked checks separately.
- State residual risks and limitations; do not conceal environmental failures.
