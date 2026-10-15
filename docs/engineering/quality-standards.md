# JobScout Engineering Quality Standards

This is the authoritative checklist for planning verification and assessing commit readiness. Use it with `AGENTS.md` and the detailed policies in `.ai/`; it does not replace them.

- **Mandatory (M):** required for every change.
- **Context-dependent (C):** required only when the change touches that behavior, boundary, or risk.

Not every change requires every test category. Record non-applicable checks as such, and report skipped, unavailable, or blocked verification truthfully. A change is commit-ready only when every mandatory and applicable context-dependent item passes and `.ai/review.md` permits readiness.

## 1. Correctness and Invariants

- [ ] **M** The requested behavior, preserved behavior, assumptions, and acceptance criteria are explicit.
- [ ] **M** The implementation matches verified repository behavior; planned capabilities are not presented as implemented.
- [ ] **M** Errors, empty results, malformed data, incomplete results, and success remain distinct where the domain distinguishes them.
- [ ] **C** Changed invariants and failure paths have focused regression coverage.

## 2. Architecture and Dependencies

- [ ] **M** The diff preserves `Infrastructure -> Application -> Domain` and `Presentation -> Application -> Domain`.
- [ ] **M** Domain code remains independent of SQLAlchemy, HTTP, Telegram, CLI, and provider-specific details.
- [ ] **C** New cross-layer behavior uses an existing port or a justified typed contract rather than bypassing boundaries.
- [ ] **C** Provider-specific parsing and pagination remain inside the relevant adapter.

## 3. Python Typing and Error Handling

- [ ] **M** New or changed Core code satisfies the repository's strict mypy configuration.
- [ ] **M** Exceptions are handled at the correct boundary; failures are not silently swallowed or converted into false success.
- [ ] **C** External and persistence boundaries validate uncertain data before it reaches domain logic.

## 4. Deterministic Tests and Regressions

- [ ] **M** Verification is deterministic, isolated from live APIs, and proportionate to the change.
- [ ] **M** Executed, skipped, unavailable, and blocked checks are reported separately.
- [ ] **C** Behavior changes cover success, failure, boundary, and regression cases relevant to the change.
- [ ] **C** Adapter, classifier, integration, migration, or CLI tests are run only when those surfaces are affected.

## 5. SQLAlchemy Transactions and Rollback

- [ ] **C** Persistence changes keep each completed collection outcome atomic and prove rollback on failure.
- [ ] **C** Network I/O and provider retries remain outside database transactions.
- [ ] **C** Database and migration checks use isolated temporary databases, never the real database.
- [ ] **C** Schema changes use Alembic and verify the applicable upgrade path and model/schema consistency.

## 6. Idempotency, Ordering, and Concurrency

- [ ] **C** Repeated processing does not create duplicate durable effects.
- [ ] **C** Older completed runs cannot overwrite newer durable collection state.
- [ ] **C** Concurrency-sensitive changes test relevant ordering, locking, cancellation, and partial-failure paths.

## 7. Source Completeness and Reconciliation

- [ ] **C** Source changes preserve typed coverage, completeness, malformed-count, and pagination semantics.
- [ ] **C** Provider failure is not represented as a successful empty result.
- [ ] **C** Only an eligible complete full-board snapshot can reconcile missing observations; rolling, filtered, malformed, partial, or failed results cannot.

## 8. Ownership and Vacancy Lifecycle

- [ ] **C** Observation updates and reconciliation remain scoped to the exact source-company ownership boundary.
- [ ] **C** Lifecycle changes preserve and test active, first miss, second miss/closure, and authoritative reopen transitions.
- [ ] **C** Code does not assume canonical lifecycle aggregation or lifecycle-aware candidate filtering before those capabilities exist.

## 9. Security and External Services

- [ ] **M** Secrets, credentials, `.env`, sessions, databases, state, logs, caches, and sidecars are neither exposed nor staged.
- [ ] **M** Verification does not modify real databases, credentials, sessions, installed jobs, or external services without explicit authorization.
- [ ] **M** Logs and error reports avoid secret values and do not claim false success.
- [ ] **C** Any authorized external interaction uses the narrowest scope and reports its effects.

## 10. Legacy Telegram Isolation

- [ ] **M** Unrelated Core work does not modify the legacy Telegram collector or its runtime state.
- [ ] **C** Telegram changes remain isolated from Core persistence and use mocked clients and synthetic state for tests.
- [ ] **C** Telegram session, delivery, deduplication, and collector-state behavior are preserved unless explicitly in scope.

## 11. Scope and Git Hygiene

- [ ] **M** The diff is the smallest coherent change and preserves unrelated working-tree changes.
- [ ] **M** `git status`, the complete diff, and whitespace diagnostics have been reviewed, including staged and untracked files where applicable.
- [ ] **M** No file is staged, committed, pushed, amended, reset, stashed, or force-pushed without the required authorization.
- [ ] **M** Independent features, refactors, formatting churn, and roadmap phases are not mixed into the change.

## 12. Adversarial Review and Readiness

- [ ] **M** The reviewer traces affected execution paths and challenges failure, stale, duplicate, and concurrent cases where relevant.
- [ ] **M** Verification follows `.ai/testing.md`; filesystem effects are contained or authorized and then disclosed.
- [ ] **M** P0/P1 findings are resolved; P2/P3 handling follows `.ai/review.md`.
- [ ] **M** The final report states scope, exact verification results, side effects, limitations, risks, and an explicit readiness verdict.
- [ ] **C** Run the full canonical quality gate when required by task scope or risk; otherwise document why narrower verification is sufficient.
