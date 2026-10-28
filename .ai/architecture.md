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
- hh.ru provider-explicit closure requires a strict JSON boolean `archived` value;
  coercible strings, numbers, null, and container values are malformed rows.

Adapters return `SourceCollectionResult`, which records coverage, completeness, raw and malformed counts, and pagination exhaustion. A result is reconciliation-eligible only when it is a complete full-board snapshot with no malformed entries and exhausted pagination. Empty results are valid only when the adapter has positively established an authoritative empty snapshot; failures and malformed data are not empty success.

`CollectJobsService` performs provider network work before persistence. It bounds source concurrency and serializes persistence where required; database transactions do not enclose network I/O.

## Persistence and Identity

SQLAlchemy repositories persist normalized companies, source companies, canonical vacancies, source observations, classifications, collection runs, and source health.

Important identity boundaries:

- Canonical source identity is scoped by company, source, and source job ID.
- Observations belong to an exact source-company/discovery-source/source-job identity.
- Cross-source canonicalization may use normalized job URLs where the implemented rules allow it.

Completed collection persistence is atomic. The implementation checks durable ordering before mutation, so an older completed run cannot overwrite newer state. A transaction either records the coherent collection outcome or rolls it back.

## Vacancy Lifecycle

Eligible authoritative snapshots reconcile observations owned by that exact source company:

1. Seen observations are active and their absence counter is cleared.
2. An active observation missing from the first eligible snapshot records its first miss.
3. A second consecutive eligible miss closes the observation.
4. A valid positive active row reopens its observation even when its collection
   is incomplete or rolling-window, because presence is positive evidence.
5. A verified provider's explicit closed row records `provider_explicit`
   evidence using its timestamp or the accepted collection timestamp.
6. The second eligible absence records `authoritative_absence` evidence.

Filtered subsets, rolling windows, incomplete pagination, malformed payloads,
and failed runs cannot advance absence state. `last_complete_snapshot_at` is
updated only by an eligible snapshot.

Canonical lifecycle is derived atomically from all linked observations affected
by an accepted collection. Observation `lifecycle_provenance` distinguishes
accepted collection state from non-authoritative generic repository history.
Generic observations are retained for identity and provenance but do not reopen
or close an existing canonical vacancy. Legacy `unknown` provenance remains
conservative and participates in lifecycle aggregation, so it cannot be mistaken
for an ignorable generic write. Any authoritative ACTIVE observation, NULL-owned
observation, observation without trustworthy closure time, observation whose
closure evidence is `unknown`, or empty authoritative observation set keeps the
canonical vacancy active. The canonical vacancy closes only when every
lifecycle-participating observation is closed, timestamped, and has
`provider_explicit` or `authoritative_absence` evidence, and every linked
observation is owned. Reopening clears canonical `closed_at`; repeated closure
preserves it; reclosure records a new accepted transition time without timestamp
regression. Aggregation processes affected canonical IDs in fixed SQLite-safe
batches while loading each batch's complete observation set.

Migration 0008 marks all historical observation evidence `unknown` because the
older schema did not persist authority. It reopens historical closed canonical
rows without changing observation status, timestamps, ownership, classifications,
or active canonical rows. Migration 0009 conservatively marks existing lifecycle
provenance `unknown`; it does not guess whether older ACTIVE observations came
from accepted collections or generic writes. Generic vacancy upserts preserve
metadata and source identity but cannot create lifecycle authority, close a
canonical vacancy, or reopen an authoritative closure. A later accepted positive
collection promotes the matching observation to `accepted_collection` and can
legitimately reopen the canonical vacancy.

## Classification and Queries

The current classifier is deterministic and versioned as `ios-v1`.
Classifications are persisted rather than recomputed implicitly. Active
candidate retrieval joins canonical vacancies and applies lifecycle filtering
in SQL before ordering and limits. Historical classifications remain stored,
and direct vacancy-source inspection exposes canonical and observation
lifecycle fields. Application services expose these workflows through
`src/job_search/presentation/cli.py`.

SQLite is the only supported database. Its writer serialization combines with
the per-company stale guard and atomic aggregation transaction. Lock contention
blocks or fails the collection without a partial commit; deterministic integration
tests hold one writer through aggregation while a second persistence instance
attempts its write. PostgreSQL support would require
additional canonical-row concurrency controls and is not currently implemented.

There is no FastAPI service or LLM provider in the implemented Core. These are not assumed by the current architecture.

## Invariants

- Provider failures are distinct from successful empty results.
- Only an eligible full-board snapshot can reconcile missing observations.
- Persistence of a completed collection is atomic.
- Stale runs cannot mutate newer durable collection state.
- Observation reconciliation never crosses source-company ownership.
- Canonical lifecycle derives from the complete linked observation set.
- Ambiguous or active evidence prevents canonical closure.
- Candidate limits are applied only after active lifecycle filtering.
- Network calls remain outside database transactions.
- Source provenance remains queryable after canonicalization.
- Legacy Telegram runtime state remains separate from Core persistence.
