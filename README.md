# Personal Job Search

This repository contains two intentionally separate capabilities:

1. **Job Search Core (Phases 1–3B)** — a production-oriented foundation for
   collecting, normalizing, and deterministically classifying jobs from company
   ATS platforms.
2. **Legacy Telegram collector** — the existing `main.py` workflow that scans
   Telegram channels and forwards matching messages to Saved Messages.

The new core does not integrate the two yet. This preserves the working Telegram
collector while the new core is built behind explicit architectural boundaries.

## Phase 1 scope

Implemented:

- company registry;
- normalized `JobVacancy` domain model;
- async SQLite persistence through SQLAlchemy 2.x;
- Alembic database migrations;
- `JobSource` abstraction;
- Greenhouse Job Board API adapter;
- collection application service with per-company failure isolation;
- idempotent development seed;
- CLI commands for seeding and collection;
- deterministic offline tests;
- Ruff, mypy, and pytest configuration.

## Phase 2 scope

Phase 2 adds a deterministic iOS eligibility engine:

- explicit `MATCH`, `POSSIBLE_MATCH`, and `REJECT` decisions;
- native iOS role and technology evidence;
- seniority, Objective-C, mixed-mobile, work-mode, relocation, geography, visa,
  language, and preferred-domain rules;
- structured positive signals, warnings, and rejection reasons;
- versioned classification persistence separate from `JobVacancy`;
- idempotent reclassification;
- CLI commands for classification and candidate inspection;
- a curated regression corpus covering likely false positives and negatives.

The engine is deliberately rule-based and explainable. It does not use an LLM,
embeddings, machine learning, or an opaque numerical match score.

## Phase 3A scope

Phase 3A scales direct company collection without adding job boards or scraping:

- production adapters for Greenhouse, Lever, Ashby, and Recruitee public
  job-board APIs;
- a validated JSON company registry stored outside Python code;
- 82 API-verified mobile, fintech, web3, AI, and remote-oriented companies;
- stable company identity and repeatable bulk import;
- explicit registry provenance and verification timestamps;
- bounded concurrent HTTP collection with serialized SQLite writes;
- per-provider collection reports;
- persisted source health: `healthy`, `empty`, `failed`, and
  `invalid_configuration`;
- CLI commands for registry import, inspection, and health review.

Phase 3A does not add browser scraping, LinkedIn, hh.ru, AI analysis, Telegram
delivery, contact enrichment, or automatic applications.

## Phase 3B scope

Phase 3B adds safe external discovery and deterministic cross-source
deduplication:

- official hh.ru API adapter with mandatory OAuth configuration;
- configurable 72-hour external discovery window;
- bounded pagination, detail concurrency, and transient retries;
- source observations separated from the preferred canonical vacancy;
- canonical URL and embedded ATS-reference matching;
- official-source precedence over aggregators;
- conservative employer/title normalization for diagnostics only;
- provenance and duplicate diagnostics in the CLI;
- non-destructive observation backfill for every existing vacancy.

Find Dream Offer and LinkedIn were investigated but are intentionally not
collected directly. Find Dream Offer currently exposes an undocumented,
CAPTCHA-gated browser backend. LinkedIn has no public job-search API and its
terms prohibit scraping and unauthorized automation. See
[the investigation record](docs/phase-3b-source-investigation.md).

## AgileFluent source extension

The public AgileFluent JSON API remains the 83rd configured source; the disabled
hh.ru external-discovery entry is the 84th registry entry.
Collection is deliberately bounded to jobs published in the last 24 hours and
to the user's current search profiles:

- middle/senior/lead iOS and Mobile engineering;
- intern/junior Python, Backend, and Full Stack engineering;
- junior through lead Project, Program, Delivery, Scrum, Agile, and Technical
  Program Management;
- remote roles or roles in Cyprus, Georgia, the Netherlands, Poland, and the UAE.

The provider currently applies role filters reliably but can ignore requested
grades. The adapter therefore enforces grades again locally, paginates with a
safety limit, and de-duplicates overlapping profiles by provider job ID. The
actual employer is stored separately from the aggregator source and shown in
candidate output. Apply tokens are not decoded or bypassed; stored links open
the public AgileFluent job detail page.

## Architecture

The new core is a modular monolith using pragmatic Clean/Hexagonal Architecture:

```text
presentation/cli
       |
       v
application/services ----> application/ports
       |                           ^
       v                           |
domain models + policy      infrastructure adapters
                         /                         \
 Greenhouse/Lever/Ashby/Recruitee/AgileFluent/hh.ru       SQLAlchemy/SQLite
```

Dependencies point inward:

```text
infrastructure -> application -> domain
presentation   -> application -> domain
```

The domain imports no SQLAlchemy, Pydantic, httpx, Telegram, OpenAI, or CLI
framework. Provider DTOs remain inside their adapters, and ORM records remain
inside the persistence adapter.

## Directory structure

```text
src/job_search/
  domain/                         Business models, enums, and iOS policy
  application/                    Ports, result models, use cases
  infrastructure/
    configuration.py             Validated pydantic-settings configuration
    persistence/sqlalchemy/       ORM records and repository adapters
    registry/                     Validated bulk registry loader
    sources/                      Greenhouse, Lever, Ashby, Recruitee, AgileFluent, hh adapters
    seed.py                       Loader for data/companies.json
  presentation/cli.py             Composition root and CLI
data/companies.json               Versioned company registry
migrations/                        Alembic environment and revisions
tests/job_search/
  unit/                            Domain, classifier, source, use-case behavior
  integration/                     SQLite repositories and migrations
main.py                            Existing Telegram collector (legacy)
```

## Requirements and setup

- Python 3.12 or newer
- macOS, Linux, or another environment supported by Python and SQLite

Create and install a virtual environment:

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e '.[dev]'
```

Copy `.env.example` to `.env` and adjust settings if needed. Do not commit the
real `.env` file.

## Local security and runtime data

The project currently keeps local runtime files beside the source code for
compatibility with the CLI and the installed `launchd` job. They are not source
files and must not be committed or shared:

| Path | Classification | Expected mode |
|---|---|---|
| `.env` | secret configuration | `0600` |
| `telegram_collector.session` | sensitive Telegram session credential | `0600` |
| `collector_state.json` | private Telegram processing state | `0600` |
| `job_search.db` | private local job-search database | `0600` |
| `logs/` | private operational data | `0700` |
| `logs/*` | private Telegram metadata and diagnostics | `0600` |

On macOS, verify the current modes without reading file contents:

```bash
stat -f '%Sp %N' .env telegram_collector.session collector_state.json job_search.db logs logs/*
```

If necessary, restore the expected permissions with explicit paths:

```bash
chmod 600 .env telegram_collector.session collector_state.json job_search.db
chmod 700 logs
chmod 600 logs/collector.log logs/collector.error.log logs/matches.jsonl
```

`telegram_collector.session` is effectively a credential: copying it may give
another process access to the authenticated Telegram session. Do not put it in
cloud storage, attach it to issues, or send it with logs. `.env.example` is a
trackable template and must contain no real credentials.

`job_search.db` contains local collection and classification state. It is
ignored by Git. To make a consistent backup, ensure no collection or
classification command is writing, then use SQLite's backup command and store
the result outside the repository:

```bash
sqlite3 job_search.db ".backup '/private/path/job_search-backup.db'"
```

The source/configuration artifacts that should remain trackable include
`src/`, `migrations/`, `tests/`, `docs/`, `data/companies.json`, and
`.env.example`. This project does not assume a remote Git URL; confirm whether
an existing private repository and history need to be recovered before
initializing a copied working directory.

## Configuration

All new-core settings use the `JOB_SEARCH_` prefix:

| Variable | Default |
|---|---|
| `JOB_SEARCH_DATABASE_URL` | `sqlite+aiosqlite:///./job_search.db` |
| `JOB_SEARCH_LOG_LEVEL` | `INFO` |
| `JOB_SEARCH_HTTP_CONNECT_TIMEOUT` | `5` |
| `JOB_SEARCH_HTTP_READ_TIMEOUT` | `20` |
| `JOB_SEARCH_HTTP_WRITE_TIMEOUT` | `10` |
| `JOB_SEARCH_HTTP_POOL_TIMEOUT` | `5` |
| `JOB_SEARCH_HTTP_MAX_CONNECTIONS` | `20` |
| `JOB_SEARCH_HTTP_MAX_KEEPALIVE_CONNECTIONS` | `10` |
| `JOB_SEARCH_COLLECTION_CONCURRENCY` | `8` |
| `JOB_SEARCH_USER_AGENT` | `personal-job-search/0.5` |
| `JOB_SEARCH_EXTERNAL_DISCOVERY_MAX_AGE_HOURS` | `72` |
| `JOB_SEARCH_EXTERNAL_DISCOVERY_MAX_PAGES` | `5` |
| `JOB_SEARCH_HH_API_TOKEN` | unset; required for hh.ru only |

The core validates that the database URL uses async SQLite. The existing
`TELEGRAM_*` variables are read only by the legacy collector.

## Database migrations

Apply all migrations:

```bash
.venv/bin/alembic upgrade head
```

Alembic reads the same `JOB_SEARCH_DATABASE_URL` setting as the application.

Roll back the latest migration:

```bash
.venv/bin/alembic downgrade -1
```

Timestamps are stored as UTC ISO-8601 strings because SQLite has no native
timezone-aware timestamp type. The persistence adapter always returns aware UTC
`datetime` values.

## Company registry

The default registry is [`data/companies.json`](data/companies.json). Every entry
declares its ATS type and identifier, careers URL, priority, provenance, enabled
state, and the time its public API was verified. JSON was chosen so validation
does not require another runtime dependency and changes remain easy to review.

Import the default registry:

```bash
.venv/bin/job-search seed
```

Import another registry file and inspect the result:

```bash
.venv/bin/job-search companies import data/companies.json
.venv/bin/job-search companies list
.venv/bin/job-search companies health
```

Import validates the complete file before any database write. Duplicate company
names, duplicate ATS identities, unknown fields, unsupported providers, invalid
URLs, and timezone-naive verification timestamps reject the whole file. Company
identity is a stable UUID derived from `(ats_type, ats_identifier)` for new
entries. Repeated imports update registry metadata without resetting collection
health or changing existing database identity.

## Collect jobs

After migrating and seeding:

```bash
.venv/bin/job-search collect
```

Equivalent module form:

```bash
.venv/bin/python -m job_search collect
```

The CLI creates one shared `httpx.AsyncClient`, loads enabled sources, selects
the Greenhouse, Lever, Ashby, Recruitee, AgileFluent, or hh.ru adapter,
normalizes jobs,
upserts vacancies, updates source health, and prints separate provider summaries.

The disabled hh.ru registry entry is collected explicitly after configuring an
OAuth token:

```bash
.venv/bin/job-search collect-external --source hh
```

Anonymous fallback is not attempted because current hh.ru documentation and
live behavior make repeated anonymous search CAPTCHA-limited.

Inspect canonicalization and provenance:

```bash
.venv/bin/job-search duplicates
.venv/bin/job-search vacancy sources <canonical-vacancy-uuid>
```

Canonical matching uses only high-confidence evidence: normalized application
URL or an explicit provider/ATS identifier embedded in a known URL. Tracking
parameters and fragments are removed, but job-identifying query parameters are
preserved. Ambiguous same-employer/title cases remain separate and are reported
as possible duplicate groups. When an official ATS observation and an
aggregator observation converge, official content and apply URL win while both
observations remain inspectable.

HTTP collection is concurrent and bounded by
`JOB_SEARCH_COLLECTION_CONCURRENCY`. SQLite writes remain serialized to avoid
writer contention. A failure for one company is logged and reported without
stopping other companies. Task cancellation is not swallowed.

Health meanings:

- `healthy`: the configured source returned one or more jobs;
- `empty`: the source request succeeded and legitimately returned no jobs;
- `failed`: a transient request, response, validation, or persistence error;
- `invalid_configuration`: the ATS adapter is absent or the provider can
  distinguish an unknown board identifier.

Failed checks preserve the last successful time and job count.

## Classify vacancies

Classify every active normalized vacancy with the current `ios-v1` policy:

```bash
.venv/bin/job-search classify
```

The summary reports processed vacancies and counts for all three decisions.
Classification identity is the composite primary key:

```text
(vacancy_id, classifier_version)
```

Running the command again updates that version's result instead of inserting a
duplicate. A future ruleset can use `ios-v2` and coexist with historical `ios-v1`
results for debugging and controlled reclassification.

Inspect surviving vacancies:

```bash
.venv/bin/job-search candidates
.venv/bin/job-search candidates --limit 20
```

Output includes decision, company, title, location, work mode, positive signals,
warnings, and the application URL.

### Decision semantics

- `MATCH`: meaningful native iOS evidence, compatible seniority, and no material
  eligibility uncertainty.
- `POSSIBLE_MATCH`: native iOS relevance survives, but seniority, relocation,
  geography, authorization, mixed-mobile scope, Lead scope, or legacy
  Objective-C needs human review.
- `REJECT`: explicit incompatibility such as Junior/Intern/Staff/Principal,
  Android/Flutter/React-Native-only, no meaningful iOS evidence, primary
  Objective-C, unsupported mandatory language, or explicit onsite/hybrid without
  relocation.

Title signals take precedence over incidental description mentions. For example,
an Android title is not accepted merely because the description mentions
collaboration with an iOS team. Generic Mobile titles require multiple native
iOS/Swift signals in the description.

## Idempotency and database identity

Vacancies have independent internal UUIDs. A provider ID is stored only as
`source_job_id`. Aggregated vacancies additionally store `employer_name`, while
`company_id` continues to identify the configured collection source. The
database enforces:

```text
UNIQUE(source, source_job_id)
```

When a known vacancy is observed again, mutable source-derived fields and
`last_seen_at` are updated while internal `id` and `first_seen_at` are
preserved. The unique database constraint protects against duplicates even if
application logic regresses.

## Quality commands

```bash
make format
make lint
make typecheck
make test
make quality
```

The direct equivalents are:

```bash
.venv/bin/ruff format .
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/mypy
.venv/bin/pytest
```

The pre-existing Telegram scripts are excluded from Ruff and mypy because they
predate the Phase 1 architecture. The complete existing classifier suite still
runs under pytest, preventing regressions in legacy behavior.

## Legacy Telegram collector

The scheduled collector remains compatible:

```bash
.venv/bin/python main.py --once
```

Continuous mode remains:

```bash
.venv/bin/python main.py
```

### macOS scheduling with launchd

[`launchd/local.job-scout.collector.plist`](launchd/local.job-scout.collector.plist)
is a portable template for running the one-shot collector every six hours.
Because `launchd` does not expand `~` or shell variables in plist paths, copy
the template to `~/Library/LaunchAgents/` and replace every
`__JOBSCOUT_PROJECT_DIR__` value in that copy with the absolute path to your
checkout. Create the checkout's `logs/` directory before loading the job.

Validate and load the adapted copy with:

```bash
plutil -lint "$HOME/Library/LaunchAgents/local.job-scout.collector.plist"
launchctl bootstrap "gui/$(id -u)" \
  "$HOME/Library/LaunchAgents/local.job-scout.collector.plist"
```

The tracked template is not read by an already installed LaunchAgent, so
editing it does not modify or unload an existing local job.

Phase 1 deliberately does not call or depend on this module.

## Important design decisions

- Domain dataclasses and enums are independent from database/API DTOs.
- Pydantic v2 validates untrusted ATS, registry, and configuration boundaries.
- Repositories own sessions and explicit transaction scopes.
- No ORM record crosses the persistence boundary.
- One malformed ATS job is logged and skipped; a malformed envelope or failed
  request fails that company collection explicitly.
- No automatic retries are implemented yet; permanent failures are not hidden.
- Company location and vacancy work location are distinct fields.
- Remote policy is intentionally limited to `unknown`, `remote`, `hybrid`, and
  `onsite` in Phase 1.
- Classification policy lives in the domain because it is deterministic business
  policy over normalized vacancies and has no persistence, HTTP, ATS, or CLI
  dependencies.
- Classification evidence stores compact enum values, not duplicated vacancy
  descriptions.

## Current limitations

- Greenhouse does not provide a reliable published timestamp in its public list
  response, so `published_at` is currently `NULL`.
- Ashby's explicit public job ID is preferred; the stable final path component
  of `jobUrl` is a compatibility fallback for older payloads.
- AgileFluent is a rolling 24-hour feed. Older collected vacancies remain in the
  database because generic vacancy-closing reconciliation is not implemented.
- hh.ru requires a registered OAuth application and token. Without it,
  `collect-external` reports `invalid_configuration` and performs no anonymous
  workaround.
- Find Dream Offer and LinkedIn direct discovery are investigated but not
  supported for the reasons documented above.
- Basic remote-policy detection is intentionally shallow.
- Geographic and language detection is conservative and phrase-based; warnings
  require human review and are not legal-eligibility conclusions.
- The classifier optimizes for recall but only recognizes native iOS evidence
  expressed by its documented deterministic vocabulary.
- Registry identifiers must be re-verified if a company changes ATS.
- Closing vacancies that disappear from a source is not implemented yet.

## Later phases

Future phases may add other ATS and job-board adapters, Telegram ingestion,
advanced remote eligibility, OpenAI analysis, candidate-profile comparison,
contact enrichment, cross-source deduplication, Saved Messages delivery,
application feedback, and vacancy lifecycle tracking. None of those features
are part of Phase 3A.
