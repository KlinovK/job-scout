# JobScout

JobScout is a multi-source job discovery and intelligence platform that
collects, normalizes, deduplicates, and classifies vacancies from heterogeneous
ATS platforms and job boards.

> **Status: Active Development** — the collection and deterministic iOS
> classification pipeline is implemented. Selective AI-assisted analysis and
> ranking are planned, not presented as current functionality.

## Why JobScout

Relevant vacancies are fragmented across company ATS platforms, aggregators,
and community channels. Each source exposes different schemas, identifiers,
and failure modes, while the same role may appear through several paths.
JobScout creates one normalized, provenance-aware pipeline for discovering and
evaluating those opportunities without hiding uncertain data behind a single
opaque score.

## Current Capabilities

- **Multi-source collection:** production adapters for Greenhouse, Lever,
  Ashby, Recruitee, AgileFluent, and the official hh.ru API.
- **Validated company registry:** 92 configured entries across six source
  types; 91 are enabled for the default collection workflow. The hh.ru entry is
  disabled by default and invoked explicitly when OAuth is configured.
- **Asynchronous ingestion:** one shared `httpx.AsyncClient`, bounded
  per-company concurrency, and isolated provider failures so one broken source
  does not stop the remaining collection run.
- **Bounded GET reliability:** transient transport failures, HTTP 429, and
  selected 5xx responses use two retries with deterministic exponential
  backoff; server-requested delays are validated and capped.
- **Normalized domain model:** provider DTOs are validated and translated into
  framework-independent companies, vacancies, source observations, health
  states, and classifications.
- **Durable provenance:** every discovered source identity is stored as an
  observation linked to a preferred canonical vacancy.
- **Conservative deduplication:** exact normalized application URLs and explicit
  ATS references can collapse cross-source duplicates; similar employer/title
  records with distinct URLs remain separate for review. Official ATS content
  is preferred over aggregator content when observations converge.
- **Idempotent persistence:** repeated registry imports, collections, and
  `ios-v1` classifications update existing identities instead of creating
  duplicate records.
- **Atomic collection persistence:** each successful company/source result
  commits canonical vacancies, source observations, ownership, and success
  health metadata in one transaction. A failed write rolls the whole unit
  back, while older or replayed results cannot overwrite newer persisted data.
- **Safe observation reconciliation:** authoritative complete snapshots close
  an observation only after two consecutive misses and reopen it when it
  reappears. Incomplete and rolling-window absence never closes a vacancy,
  while a valid positive row from either can reopen it.
- **Canonical lifecycle aggregation:** canonical status is derived atomically
  from every linked observation. Any active or ambiguous observation keeps the
  vacancy active; closure requires attributable, timestamped evidence from all
  observations.
- **Explainable iOS classification:** a deterministic, versioned ruleset emits
  `MATCH`, `POSSIBLE_MATCH`, or `REJECT` with structured signals, warnings, and
  rejection reasons.
- **Operational source health:** `healthy`, `empty`, `failed`, and
  `invalid_configuration` are distinct persisted outcomes. A non-empty payload
  whose records are all malformed fails the source rather than masquerading as
  a legitimate empty result; mixed payloads preserve valid records.
- **Lifecycle-aware candidate review:** active candidates are filtered in SQL
  before ordering and limits; classifications remain stored for historical
  inspection.
- **CLI workflows:** registry import and inspection, collection, external
  collection, classification, candidate review, source health, duplicate
  diagnostics, and lifecycle-aware vacancy provenance inspection.
- **Legacy Telegram integration:** a separate Telethon collector scans
  configured channels, applies its own vacancy filters, and forwards matches to
  Saved Messages. It is intentionally not yet unified with the core pipeline.

## Architecture

JobScout is a modular monolith built around pragmatic Clean/Hexagonal
Architecture. Business models and deterministic policy live in the domain;
application services coordinate use cases through protocols; infrastructure
implements HTTP, registry, and persistence boundaries; presentation provides
the CLI composition root.

```mermaid
flowchart LR
    CLI[Presentation: CLI] --> APP[Application services]
    APP --> DOMAIN[Domain models and policy]
    APP --> PORTS[Application ports]

    INFRA[Infrastructure adapters] --> PORTS
    INFRA --> DOMAIN
    INFRA --> APIs[ATS and job-board APIs]
    INFRA --> DB[(SQLite)]

    CLASSIFIER[ios-v1 classifier] --> DOMAIN
```

The source-code dependency direction is deliberately inward:

```text
Infrastructure -> Application -> Domain
Presentation   -> Application -> Domain
```

The domain imports no SQLAlchemy, Pydantic, httpx, Telethon, OpenAI, or CLI
framework. Provider DTOs stay inside source adapters, and ORM records stay
inside the persistence adapter.

The implemented collection path is:

```text
Public APIs -> source adapters -> CollectJobsService -> atomic persistence -> SQLite
                                                               |
                                                               +-> source health
SQLite -> ios-v1 classifier -> persisted decisions -> CLI candidate view
```

The legacy Telegram collector is a parallel integration and does not currently
write into this normalized pipeline.

## Technology

- Python 3.12+, `asyncio`, and `httpx`
- SQLAlchemy 2.x async with SQLite and `aiosqlite`
- Alembic schema migrations
- Pydantic v2 and `pydantic-settings` at untrusted/configuration boundaries
- Telethon for the separate Telegram collector
- pytest and pytest-asyncio
- Ruff formatting and linting
- mypy in strict mode for `src/job_search`

FastAPI and an LLM provider are intentionally absent from the current stack.

## Engineering Highlights

### Explicit failure semantics

An empty source is valid data; a malformed non-empty response is not. Adapters
validate response envelopes and individual records, retain valid records from
mixed payloads, and surface all-malformed payloads as typed source failures.
This prevents upstream schema drift from silently looking like “no vacancies.”

### Bounded concurrency with isolated failures

HTTP collection runs concurrently behind an `asyncio.Semaphore`, while SQLite
writes use a short application-level lock. No network request is held inside a
database transaction. A conditional database update orders each company's
successful results by collection start time, so correctness does not depend on
the in-process lock alone. This keeps network I/O efficient, avoids unnecessary
SQLite writer contention, and records per-company failures without cancelling
unrelated work.

### Atomic collection persistence

A completed result for one company/source is the transaction boundary.
Canonical vacancies, source observations, ownership, and successful source
health commit together or roll back together. Persistence failures are reported
separately from source API failures. Exact replays are no-ops, older results are
rejected at the database write boundary, and existing lifecycle state is not
changed by non-authoritative absence.

### Observation and canonical lifecycle

Only a reconciliation-eligible `FULL_BOARD` result can advance
`last_complete_snapshot_at`. One missing complete snapshot increments an active
observation's counter; the second closes it and records that snapshot's time.
The counter then remains bounded at two. Any valid positive active row in an
accepted collection reactivates its observation and clears its counter and
closure time and evidence, including rows from incomplete or rolling-window
results.
Incomplete, malformed, filtered, and rolling-window absence—including hh.ru
and AgileFluent searches—cannot increment or close observations or advance
complete snapshot metadata. A verified adapter's explicit closed row records
`provider_explicit` evidence at the accepted collection time when the provider
supplies no timestamp. The second eligible absence records
`authoritative_absence`; historical or direct-import closure remains `unknown`.

Canonical lifecycle is derived in the same transaction from all linked
observations. Any active observation, unknown owner, missing closure timestamp,
or empty observation set keeps the canonical vacancy active. It closes only
when every observation has attributable, timestamped `provider_explicit` or
`authoritative_absence` evidence; one source disappearing cannot close a vacancy
supported elsewhere. Generic vacancy upserts cannot manufacture authoritative
evidence. Reopening
clears canonical `closed_at`, repeated closure preserves it, and stale or exact
replays are rejected before lifecycle mutation. The legacy repair migration
marks historical evidence `unknown` and reopens ambiguous closed canonical rows
without modifying observation status, timestamps, ownership, or classifications.
Lifecycle aggregation uses fixed-size batches below legacy SQLite parameter
limits while retaining the complete linked observation set for each canonical.

### Explicit retry ownership

Safe GET requests have one Infrastructure-level retry owner: one initial
attempt plus two retries for transient transport failures, HTTP 429, and HTTP
500/502/503/504. Backoff is bounded, cancellation escapes immediately, and
permanent HTTP or successful malformed-data failures are never retried. This
keeps brief provider outages from becoming false collection failures without
hiding deterministic defects. `Retry-After` supports integer delta-seconds;
malformed or date-form values fall back to local backoff, and every delay is
capped at 10 seconds.

### Provenance-first canonicalization

JobScout stores both a canonical vacancy and its source observations. It merges
only on high-confidence URL or embedded ATS identity evidence and preserves
ambiguous records. That bias favors explainability and prevents aggressive
fuzzy matching from combining unrelated roles.

### Versioned deterministic policy

The `ios-v1` classifier is explainable and reproducible. Classification records
are keyed by `(vacancy_id, classifier_version)`, so a future policy can coexist
with historical results instead of overwriting how a previous decision was
made.

### Typed, migration-backed boundaries

Frozen domain dataclasses, application protocols, validated provider DTOs, and
explicit database migrations keep external payloads and ORM concerns from
leaking into business policy. Registry import is whole-file validated before
any database write.

## Quality

The current repository contains **302 passing tests**, including unit,
integration, migration, adapter-contract, canonicalization, concurrency, and
classification regression coverage.

The local quality gate runs:

```bash
make quality
.venv/bin/alembic check
git diff --check
```

`make quality` combines Ruff lint and format checks, strict mypy, and pytest.
There is no CI badge because CI has not been implemented yet.

## Development Status

### Implemented

- six HTTP source adapters and a validated 92-entry registry;
- async normalized collection with bounded concurrency and provider isolation;
- atomic async SQLite collection persistence and Alembic migrations;
- source observations, conservative cross-source canonicalization, and
  duplicate diagnostics;
- two-snapshot, authoritative observation reconciliation with safe reopening;
- all-observation canonical lifecycle aggregation and active-only candidate
  filtering;
- persisted source-health and malformed-source semantics;
- bounded retry/backoff for safe GET source operations;
- deterministic `ios-v1` classification and candidate inspection;
- a separate Telegram-to-Saved-Messages collection path;
- strict static analysis and a 289-test automated suite.

### In Progress / Next

- a broader query layer over normalized vacancies and classifications;
- operational hardening of scheduled collection and local runtime security.

### Planned

- FastAPI query surface;
- typed contracts and an evaluation dataset for AI-assisted analysis;
- structured LLM extraction and ranking, introduced only after evaluation;
- unified delivery, including Telegram, from the normalized pipeline;
- additional source integrations where official or permitted APIs exist.

## AI-Assisted Engineering

Coding agents are used as implementation tools inside a controlled engineering
workflow:

```text
Requirement -> repository analysis -> scoped specification -> implementation
            -> automated verification -> review -> remediation -> accepted commit
```

AI output is not treated as authority. Tests and static analysis check
mechanical correctness; architecture, scope, and product intent remain subject
to human review. Risk-sensitive iterations can add an independent adversarial
review before acceptance. Generated changes are inspected and verified before
they become a published commit.

## Running Locally

### 1. Install

Requirements: Python 3.12+ and SQLite.

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e '.[dev]'
cp .env.example .env
```

Keep `.env` local. It is ignored by Git and must contain your own credentials,
never values copied from another environment.

### 2. Initialize the database and registry

```bash
.venv/bin/alembic upgrade head
.venv/bin/job-search seed
```

The default database is `job_search.db`. The seed command validates and imports
[`data/companies.json`](data/companies.json) idempotently.

### 3. Collect, classify, and inspect

```bash
.venv/bin/job-search collect
.venv/bin/job-search classify
.venv/bin/job-search candidates --limit 20
```

Useful diagnostic commands:

```bash
.venv/bin/job-search companies list
.venv/bin/job-search companies health
.venv/bin/job-search duplicates
.venv/bin/job-search vacancy sources <canonical-vacancy-uuid>
```

The disabled hh.ru source requires a registered OAuth token in
`JOB_SEARCH_HH_API_TOKEN` and is run explicitly:

```bash
.venv/bin/job-search collect-external --source hh
```

No anonymous fallback is attempted because anonymous hh.ru vacancy search is
CAPTCHA-limited.

### Technical management discovery

JobScout discovery includes a separate `technical_management` search group.
Greenhouse, Lever, Ashby, and Recruitee still collect complete company boards,
so they require no role-specific requests. AgileFluent retains its supported
management profile (`Project Manager`, `Program Manager`, `Delivery Manager`,
`Scrum Master`, `Agile Coach`, and `Technical Program Manager (TPM)`) within
the existing 24-hour rolling window.

The hh.ru adapter adds eight bounded phrase searches for project, program,
delivery, product, product-owner, Scrum, and core Russian project-management
titles. These searches retain the 72-hour window and the configured page cap.
Broader coordinator, implementation, operations, and generic team-lead terms
are not queried separately to limit request volume and false positives.

This expands **discovery**, not classification. The `ios-v1` classifier and
the `candidates` command still represent only the iOS profile. Management
vacancies are stored in `job_vacancies`; they can be inspected with a
read-only SQLite query, followed by the existing provenance command:

```bash
sqlite3 -readonly job_search.db \
  "SELECT id, title, employer_name, url FROM job_vacancies WHERE title LIKE '%Manager%' OR title LIKE '%Owner%' OR title LIKE '%Scrum%' ORDER BY last_seen_at DESC;"
.venv/bin/job-search vacancy sources <canonical-vacancy-uuid>
```

### Configuration

Core settings use the `JOB_SEARCH_` prefix. The most useful controls are:

| Variable | Default | Purpose |
|---|---:|---|
| `JOB_SEARCH_DATABASE_URL` | `sqlite+aiosqlite:///./job_search.db` | Async SQLite database |
| `JOB_SEARCH_COLLECTION_CONCURRENCY` | `8` | Concurrent company collections |
| `JOB_SEARCH_HTTP_MAX_CONNECTIONS` | `20` | HTTP pool bound |
| `JOB_SEARCH_EXTERNAL_DISCOVERY_MAX_AGE_HOURS` | `72` | hh.ru freshness window |
| `JOB_SEARCH_EXTERNAL_DISCOVERY_MAX_PAGES` | `5` | hh.ru page bound per query |
| `JOB_SEARCH_HH_API_TOKEN` | unset | Required only for hh.ru |

See [`.env.example`](.env.example) for the complete non-secret template.

### Legacy Telegram collector

Set `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`, and `TELEGRAM_PHONE` in `.env`, then
use either a one-shot scan or continuous monitoring:

```bash
.venv/bin/python main.py --once
.venv/bin/python main.py
```

The collector keeps a local Telethon session and processing state, writes an
audit log, and sends matches to Saved Messages. Those runtime artifacts are
ignored and must not be committed.

#### macOS scheduling

[`launchd/local.job-scout.collector.plist`](launchd/local.job-scout.collector.plist)
is a portable template for the one-shot collector. Copy it to
`~/Library/LaunchAgents/`, replace every `__JOBSCOUT_PROJECT_DIR__` in the copy
with the absolute path to your checkout, and create the checkout's `logs/`
directory. The tracked schedule runs at 00:15, 06:15, 12:15, and 18:15.

```bash
plutil -lint "$HOME/Library/LaunchAgents/local.job-scout.collector.plist"
launchctl bootstrap "gui/$(id -u)" \
  "$HOME/Library/LaunchAgents/local.job-scout.collector.plist"
```

The tracked template is not read by an already installed LaunchAgent; changing
the repository file does not unload or alter an installed local job.

## Project Structure

```text
src/job_search/
  domain/                     Framework-independent models and iOS policy
  application/                Ports, use cases, and result models
  infrastructure/
    sources/                  Six external-source adapters
    persistence/sqlalchemy/   Async repository implementations
    registry/                 Validated company-registry loader
  presentation/               CLI composition root
tests/                        Unit, integration, and regression tests
migrations/                   Alembic revisions
data/companies.json           Versioned source registry
launchd/                      Portable macOS scheduling template
main.py                       Separate legacy Telegram collector
```

## Current Limitations

- Canonical lifecycle concurrency guarantees currently target SQLite. A future
  PostgreSQL backend would require canonical-row locking or equivalent controls.
- Legacy observations whose source-company ownership cannot be reconstructed
  keep their canonical vacancy active conservatively.
- Greenhouse list responses do not provide a reliable publication timestamp,
  so those records may have `published_at = NULL`.
- hh.ru requires OAuth; its adapter is disabled in the default registry.
- Retry/backoff is intentionally limited to GET requests. AgileFluent's
  read-only search endpoint uses POST and is not automatically retried.
- Remote, geography, language, and relocation decisions are conservative,
  phrase-based signals for human review—not legal eligibility conclusions.
- The current classifier targets iOS relevance only. Python, full-stack,
  project-management, and vibe-coding filters exist in the separate Telegram
  collector, not in the normalized core classifier.
- Find Dream Offer and LinkedIn are not collected directly: the former exposes
  an undocumented CAPTCHA-gated browser backend, while LinkedIn does not offer
  a suitable public job-search API for this use case. See the
  [source investigation](docs/phase-3b-source-investigation.md).
- There is no API server, LLM analysis, learned ranking, contact enrichment, or
  automatic application workflow.

## Roadmap

1. Add a broader query layer for normalized vacancies and classifications.
2. Harden the operational collection pipeline and observability.
3. Expose reviewed query use cases through FastAPI.
4. Define AI analysis contracts, fixtures, and an evaluation dataset.
5. Add structured LLM analysis and ranking behind those evaluations.
6. Unify delivery channels with the normalized pipeline.
