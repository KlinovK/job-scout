# JobScout Architecture

This document records the implemented architecture. The code, migrations, and regression tests remain the source of truth when documentation becomes stale.

## Two Runtime Areas

JobScout Core is the normalized vacancy pipeline under `src/job_search/`. It collects structured job data, persists provenance and source health, classifies iOS relevance, and exposes operator commands through the CLI.

The legacy Telegram collector is `main.py`. It monitors configured Telegram channels, performs keyword and marker filtering, deduplicates messages, and forwards matches to Saved Messages. It has its own state and is not an adapter in the normalized JobScout Core pipeline.

Changes must not accidentally couple these two runtime areas.

## Layers and Dependency Direction

- `src/job_search/domain/`: entities, enums, canonicalization, and the deterministic `ios-v1` classifier.
- `src/job_search/application/`: typed input/output models, ports, and orchestration services.
- `src/job_search/infrastructure/`: settings, HTTP behavior, provider adapters, registry composition, and SQLAlchemy persistence.
- `src/job_search/presentation/`: CLI commands and presentation concerns.
- `migrations/`: Alembic schema history.
- `tests/`: unit, adapter, integration, migration, and regression coverage.

Allowed direction:

```text
Infrastructure ──> Application ──> Domain
Presentation ────> Application ──> Domain
```

The domain does not know about SQLAlchemy, Pydantic, httpx, Telethon, provider payloads, or the CLI. Application services depend on ports; infrastructure supplies implementations.

## Source Adapters and Collection Results

Implemented provider adapters include Greenhouse, Lever, Ashby, Recruitee, hh.ru, and AgileFluent.

- Greenhouse, Lever, Ashby, and Recruitee can represent full-board snapshots.
- hh.ru and AgileFluent are rolling-window sources and are not authoritative for absence reconciliation.

Adapters return `SourceCollectionResult`, which records coverage, completeness, raw and malformed counts, and pagination exhaustion. A result is reconciliation-eligible only when it is a complete full-board snapshot with no malformed entries and exhausted pagination. Empty results are valid only when the adapter has positively established an authoritative empty snapshot; failures and malformed data are not empty success.

`CollectJobsService` performs provider network work before persistence. It bounds source concurrency and serializes persistence where required; database transactions do not enclose network I/O.

## Persistence and Identity

SQLAlchemy repositories persist normalized companies, source companies, canonical vacancies, source observations, classifications, collection runs, and source health.

Important identity boundaries:

- Canonical source identity is scoped by company, source, and source job ID.
- Observations belong to an exact source-company/discovery-source/source-job identity.
- Cross-source canonicalization may use normalized job URLs where the implemented rules allow it.

Completed collection persistence is atomic. The implementation checks durable ordering before mutation, so an older completed run cannot overwrite newer state. A transaction either records the coherent collection outcome or rolls it back.

## Observation Lifecycle Through C.2C.2

Eligible authoritative snapshots reconcile observations owned by that exact source company:

1. Seen observations are active and their absence counter is cleared.
2. An active observation missing from the first eligible snapshot records its first miss.
3. A second consecutive eligible miss closes the observation.
4. Seeing a closed observation again reopens it.

Filtered subsets, rolling windows, incomplete pagination, malformed payloads, and failed runs cannot advance absence state. `last_complete_snapshot_at` is updated only by an eligible snapshot. Canonical vacancy lifecycle is not yet an aggregate of multiple observation states, and candidate filtering by observation lifecycle is not yet implemented. Consumers must not infer either capability.

## Classification and Queries

The current classifier is deterministic and versioned as `ios-v1`. Classifications are persisted rather than recomputed implicitly. Application services expose collection, classification, candidates, company import/list/health, duplicate inspection, and vacancy-source inspection through `src/job_search/presentation/cli.py`.

There is no FastAPI service or LLM provider in the implemented Core. These are not assumed by the current architecture.

## Invariants

- Provider failures are distinct from successful empty results.
- Only an eligible full-board snapshot can reconcile missing observations.
- Persistence of a completed collection is atomic.
- Stale runs cannot mutate newer durable collection state.
- Observation reconciliation never crosses source-company ownership.
- Network calls remain outside database transactions.
- Source provenance remains queryable after canonicalization.
- Legacy Telegram runtime state remains separate from Core persistence.
