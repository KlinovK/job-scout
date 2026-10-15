# Adversarial Review Protocol

Review the final change as if it came from an unfamiliar contributor. Passing tests are necessary evidence, not proof that the design and behavior are correct.

## Severity

- **P0 — Critical:** secret exposure, destructive data loss, remote compromise, or a guaranteed production outage. Stop; do not commit or deliver.
- **P1 — High:** incorrect core behavior, broken invariant, unsafe migration, transaction or lifecycle corruption, false success, or substantial regression. Must be fixed before delivery.
- **P2 — Medium:** meaningful edge-case failure, concurrency weakness, incomplete error handling, maintainability risk, missing regression coverage, or misleading operator behavior.
- **P3 — Low:** localized clarity, naming, documentation, or low-impact test gap. May be deferred transparently.

Severity describes impact, not effort.

## Review Procedure

1. Confirm the baseline, requested scope, and changed-file list.
2. Read the complete diff, not only the implementation file.
3. Trace inputs through domain, application ports/services, infrastructure, persistence, and presentation where relevant.
4. Challenge assumptions with empty, malformed, incomplete, stale, duplicate, concurrent, and failure cases.
5. Inspect tests for assertions that prove behavior rather than merely execute code.
6. Verify operator-visible errors and logs do not expose secrets or claim false success.
7. Check that documentation and commands describe the implemented state.
8. Re-run affected tests after any remediation.

## Read-Only Reviews

A read-only reviewer must not intentionally modify source files, other tracked files, the Git index, or external state. Before executing a verification command, inspect its configuration and expected filesystem effects. Commands that may write require containment in an isolated temporary location or explicit authorization.

Using `python -B` or `PYTHONDONTWRITEBYTECODE=1` prevents bytecode generation but does not make a test suite read-only. Pytest, mypy, and individual tests may still create caches, temporary files, logs, or databases. Follow the containment guidance in `.ai/testing.md`, never use real databases, credentials, sessions, or external services, and disclose every verification side effect that occurs.

For whitespace review, consider each Git state. `git diff --check` covers unstaged tracked changes, `git diff --cached --check` covers staged changes, and neither covers untracked files. Check each new untracked file with `git diff --no-index --check /dev/null <file>`. Exit status `1` may indicate the expected ordinary file difference; determine failure from the emitted whitespace diagnostics, not from nonzero status alone.

## JobScout-Specific Checks

- Is a provider failure still distinct from an authoritative empty snapshot?
- Is coverage/completeness propagated without being weakened or inferred?
- Can an incomplete, rolling-window, filtered, malformed, or failed run reconcile absence?
- Does all network I/O finish before the persistence transaction begins?
- Does completed-run persistence remain atomic on every exception path?
- Can a stale run overwrite newer health, vacancies, observations, classifications, or lifecycle state?
- Is reconciliation limited to the exact source-company ownership boundary?
- Are first miss, second miss/close, and reopen transitions idempotent and tested?
- Is canonical identity/provenance preserved across duplicates and source observations?
- Does an adapter isolate provider-specific schema and pagination behavior?
- Do migration tests use an isolated database and exercise the required upgrade path?
- Does legacy Telegram work avoid changing Core behavior, sessions, delivery, or state unexpectedly?

## Readiness Rules

A change is ready only when:

- the diff is scoped and understood;
- no P0 or P1 findings remain;
- any P2 that violates acceptance criteria, required behavior, or a safety guarantee is resolved; other P2 findings are disclosed and justified;
- behavior changes have proportionate regression coverage;
- required quality and migration checks pass;
- sensitive/runtime artifacts are not staged;
- commit and push actions, if any, are explicitly authorized.

Use this review summary:

```text
Findings: P0 <count>, P1 <count>, P2 <count>, P3 <count>
Resolved: <items>
Remaining: <items and rationale>
Readiness: READY | NOT READY
```
