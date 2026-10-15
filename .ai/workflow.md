# Development Workflow

Use this sequence for every engineering iteration. Scale the ceremony to the risk, but do not skip evidence.

## 1. Inspect

- Read `AGENTS.md` and the relevant `.ai` guides.
- Run `git status`; establish the branch, baseline commit, and expected working-tree state.
- Inspect the affected code, tests, configuration, migrations, and recent history.
- Identify safety boundaries, architectural invariants, and pre-existing changes.

## 2. Plan

A written plan is required when the task is complex, cross-layer, data-sensitive, or explicitly requests a checkpoint. Simple isolated changes may proceed after a concise scope statement.

The plan should name:

- behavior to preserve and behavior to change;
- files or layers likely involved;
- failure modes and data risks;
- focused and project-wide verification;
- actions that require explicit owner authorization.

Do not use planning as permission to expand scope.

## 3. Implement

- Make the smallest coherent change.
- Follow existing types, ports, repositories, fixtures, and naming conventions.
- Keep provider parsing in adapters and domain decisions in the domain.
- Add or adjust regression coverage with the behavior change.
- Use Alembic for schema evolution and temporary databases for verification.
- Preserve unrelated user changes and avoid formatting churn.

## 4. Test

- Run the narrowest relevant tests while iterating.
- Run the applicable full gates from `.ai/testing.md` once the change is coherent.
- Re-run affected checks after every remediation.
- Record exact outcomes; never substitute expectation for execution.

## 5. Review

- Inspect `git diff --check`, the full diff, and the changed-file list.
- Apply `.ai/review.md` adversarially, including failure paths and boundary conditions.
- Resolve all P0 and P1 findings before delivery.
- Resolve P2 findings or explicitly report why they remain.

## 6. Verify

- Confirm the final diff contains only intended files and behavior.
- Confirm secrets and runtime artifacts are neither tracked nor staged.
- Confirm documentation matches the implementation.
- If a commit is authorized, inspect the staged diff and re-check the working tree afterward.

## 7. Deliver

Report:

- outcome and files changed;
- verification executed and exact results;
- checks skipped, unavailable, or blocked, with reasons;
- review findings and remediation;
- remaining limitations and risks;
- commit, push, and working-tree status when applicable.

Committing and pushing are separate delivery actions. Never perform either automatically; they require explicit user authorization. Do not create a “checkpoint” commit before verification.
