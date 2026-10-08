# Phase 3B external-source investigation

Investigated on 2026-09-27. This document records the access decision; it is not
an authorization to bypass future provider controls.

## Find Dream Offer — investigated, not supported

The public application at <https://find.dreamoffer.app/jobs/mobile-developer/>
is a meta-aggregator and exposes employer, title, location, work format,
description, source name, source URL, provider number, and timestamps in its
browser UI. Public application assets were inspected before implementation.

Findings:

- no documented public API or published authentication/rate-limit contract was
  found;
- the browser currently uses undocumented `api.dreamoffer.app` endpoints;
- the main read endpoint accepts application-generated SQL rather than a stable
  resource contract;
- search, filter, source-link, and pagination actions are deliberately guarded
  by a CAPTCHA and a 60-second wait;
- the server-rendered bootstrap contains only selected pages and is not a
  complete, stable category feed;
- job numbers and original-source URLs exist, but using the private endpoint
  would couple the collector to an undocumented implementation and automate a
  CAPTCHA-gated workflow.

Decision: no adapter. The collector does not call the private SQL endpoint,
automate the CAPTCHA, or scrape the HTML. The source can be reconsidered if
Dream Offer publishes a documented feed/API or grants explicit access.

## hh.ru — supported with official OAuth API

The official documentation is at <https://api.hh.ru/openapi/en/redoc>. The API
provides JSON vacancy search and detail endpoints, stable vacancy IDs,
pagination, precise publication timestamps, employer, area, work format,
employment, experience, salary, full HTML description, and canonical public
vacancy URLs.

Relevant current constraints:

- a meaningful `User-Agent`/`HH-User-Agent` is required;
- anonymous vacancy search is CAPTCHA-limited after the first request, and a
  live anonymous validation returned `403`;
- production collection therefore requires `JOB_SEARCH_HH_API_TOKEN` from a
  registered OAuth application;
- search pagination is zero-based, supports up to 100 items per page, and the
  API limits deep result traversal to 2,000 results;
- `429`, timeout, malformed JSON, and transient `5xx` are explicit failures;
  transient retries are bounded and respect `Retry-After` up to five seconds.

The adapter searches broad title terms (`iOS`, `Swift`, `Mobile Engineer`, and
`Mobile Developer`) over a configurable exact freshness window, de-duplicates
IDs across queries/pages, then fetches full details with bounded concurrency.
`ios-v1` remains the final relevance and eligibility decision.

Configuration:

```dotenv
JOB_SEARCH_EXTERNAL_DISCOVERY_MAX_AGE_HOURS=72
JOB_SEARCH_EXTERNAL_DISCOVERY_MAX_PAGES=5
JOB_SEARCH_HH_API_TOKEN=your-oauth-token
```

## LinkedIn Jobs — investigated, direct collection not supported

LinkedIn's public Job Posting API documentation describes an approved-partner
API for posting jobs, not a public job-search/discovery API. Access requires
LinkedIn approval and a signed API agreement. LinkedIn's User Agreement
explicitly prohibits crawlers, scraping, access-control circumvention, and
unauthorized automation.

Decision: no direct LinkedIn adapter, login automation, cookie collection,
private API, or HTML scraper. `linkedin` remains a provenance value so a
permitted external source can identify a LinkedIn-origin vacancy in the future.
There is currently no implemented indirect LinkedIn discovery because Find
Dream Offer was not safe to automate.

Primary evidence:

- <https://learn.microsoft.com/en-us/linkedin/talent/job-postings/api/overview>
- <https://www.linkedin.com/legal/user-agreement>
- <https://www.linkedin.com/legal/jobs-terms-conditions>
