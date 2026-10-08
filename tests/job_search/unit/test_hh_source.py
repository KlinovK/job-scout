import asyncio
from datetime import UTC, datetime

import httpx
import pytest

from job_search.application.errors import (
    InvalidSourceConfigurationError,
    JobSourceError,
)
from job_search.domain.enums import ATSType, RemotePolicy, VacancySource
from job_search.infrastructure.sources.hh import HHSource, HHSourceError
from tests.job_search.factories import make_company

OBSERVED_AT = datetime(2026, 9, 27, 12, 0, tzinfo=UTC)


def _company(identifier: str = "api.hh.ru"):
    return make_company(
        name="HeadHunter External Discovery",
        ats_type=ATSType.HH,
        ats_identifier=identifier,
        enabled=False,
    )


def _search(items: list[str], *, page: int = 0, pages: int = 1) -> dict[str, object]:
    return {
        "items": [{"id": item} for item in items],
        "found": len(items),
        "pages": pages,
        "page": page,
        "per_page": 100,
    }


def _detail(job_id: str = "123") -> dict[str, object]:
    return {
        "id": job_id,
        "name": "Senior iOS Engineer",
        "description": "<p>Build apps with <strong>Swift</strong>.</p>",
        "alternate_url": f"https://hh.ru/vacancy/{job_id}?from=search&utm_source=x",
        "employer": {"id": "7", "name": "Example Employer"},
        "area": {"id": "1", "name": "Moscow"},
        "salary": {"from": 300000, "to": 400000, "currency": "RUR", "gross": True},
        "experience": {"id": "between3And6", "name": "3–6 years"},
        "employment": {"id": "full", "name": "Full time"},
        "schedule": {"id": "remote", "name": "Remote"},
        "work_format": [{"id": "REMOTE", "name": "Remote"}],
        "published_at": "2026-09-27T09:30:00+00:00",
        "archived": False,
    }


def test_hh_source_error_uses_common_source_error_taxonomy() -> None:
    assert issubclass(HHSourceError, JobSourceError)


@pytest.mark.asyncio
async def test_hh_normalizes_full_vacancy_and_deduplicates_queries() -> None:
    requests: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer token"
        requests.append(request.url.path)
        if request.url.path == "/vacancies":
            return httpx.Response(200, json=_search(["123"]))
        return httpx.Response(200, json=_detail())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        vacancies = await HHSource(client, oauth_token="token", max_retries=0).collect(
            _company(), OBSERVED_AT
        )

    assert requests.count("/vacancies") == 4
    assert requests.count("/vacancies/123") == 1
    assert len(vacancies) == 1
    vacancy = vacancies[0]
    assert vacancy.source is VacancySource.HH
    assert vacancy.original_source is VacancySource.HH
    assert vacancy.employer_name == "Example Employer"
    assert vacancy.description == "Build apps with Swift ."
    assert vacancy.remote_policy is RemotePolicy.REMOTE
    assert vacancy.salary == "from 300000 to 400000 RUR gross"
    assert vacancy.experience == "3–6 years"
    assert vacancy.canonical_url == "https://hh.ru/vacancy/123?from=search"


@pytest.mark.asyncio
async def test_hh_empty_search_is_empty() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_search([]))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        vacancies = await HHSource(
            client,
            oauth_token="token",
            max_retries=0,
        ).collect(_company(), OBSERVED_AT)

    assert vacancies == ()


@pytest.mark.asyncio
async def test_hh_paginates_and_stops_at_provider_page_count() -> None:
    pages: list[int] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/vacancies/"):
            return httpx.Response(
                200, json=_detail(request.url.path.rsplit("/", 1)[-1])
            )
        page = int(request.url.params["page"])
        pages.append(page)
        return httpx.Response(
            200,
            json=_search([str(page + 1)], page=page, pages=2),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        vacancies = await HHSource(client, oauth_token="token", max_retries=0).collect(
            _company(), OBSERVED_AT
        )

    assert pages == [0, 1] * 4
    assert {item.source_job_id for item in vacancies} == {"1", "2"}


@pytest.mark.asyncio
async def test_hh_requires_oauth_without_making_request() -> None:
    async with httpx.AsyncClient() as client:
        with pytest.raises(InvalidSourceConfigurationError, match="HH_API_TOKEN"):
            await HHSource(client, oauth_token=None).collect(_company(), OBSERVED_AT)


@pytest.mark.asyncio
async def test_hh_rejects_unknown_source_identifier() -> None:
    async with httpx.AsyncClient() as client:
        with pytest.raises(InvalidSourceConfigurationError, match="identifier"):
            await HHSource(client, oauth_token="token").collect(
                _company("wrong.example"), OBSERVED_AT
            )


@pytest.mark.asyncio
async def test_hh_rejects_invalid_oauth_token() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(InvalidSourceConfigurationError, match="rejected"):
            await HHSource(client, oauth_token="bad", max_retries=0).collect(
                _company(), OBSERVED_AT
            )


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [429, 503])
async def test_hh_transient_http_errors_are_reported(status: int) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(HHSourceError, match=str(status)):
            await HHSource(client, oauth_token="token", max_retries=0).collect(
                _company(), OBSERVED_AT
            )


@pytest.mark.asyncio
async def test_hh_timeout_is_wrapped() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(HHSourceError, match="timed out"):
            await HHSource(client, oauth_token="token", max_retries=0).collect(
                _company(), OBSERVED_AT
            )


@pytest.mark.asyncio
async def test_hh_cancellation_is_not_swallowed() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        raise asyncio.CancelledError

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(asyncio.CancelledError):
            await HHSource(client, oauth_token="token", max_retries=0).collect(
                _company(), OBSERVED_AT
            )


@pytest.mark.asyncio
async def test_hh_malformed_response_is_rejected() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"items": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(HHSourceError, match="Malformed"):
            await HHSource(client, oauth_token="token", max_retries=0).collect(
                _company(), OBSERVED_AT
            )


@pytest.mark.asyncio
async def test_hh_malformed_detail_is_rejected_without_exception_group() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/vacancies":
            return httpx.Response(200, json=_search(["123"]))
        return httpx.Response(200, json={"id": "123"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(HHSourceError, match="vacancy response"):
            await HHSource(client, oauth_token="token", max_retries=0).collect(
                _company(), OBSERVED_AT
            )


@pytest.mark.asyncio
async def test_hh_mixed_valid_and_malformed_details_preserve_valid_job() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/vacancies":
            return httpx.Response(200, json=_search(["123", "broken"]))
        if request.url.path == "/vacancies/123":
            return httpx.Response(200, json=_detail())
        return httpx.Response(200, json={"id": "broken"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        vacancies = await HHSource(
            client,
            oauth_token="token",
            max_retries=0,
        ).collect(_company(), OBSERVED_AT)

    assert [item.source_job_id for item in vacancies] == ["123"]


@pytest.mark.asyncio
async def test_hh_retries_429_once_and_honors_retry_after() -> None:
    attempts = 0
    delays: list[float] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(429, headers={"Retry-After": "2"})
        return httpx.Response(200, json=_search([]))

    async def sleep(delay: float) -> None:
        delays.append(delay)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        vacancies = await HHSource(
            client,
            oauth_token="token",
            max_retries=1,
            sleep=sleep,
        ).collect(_company(), OBSERVED_AT)

    assert vacancies == ()
    assert attempts == 5
    assert delays == [2.0]


@pytest.mark.asyncio
async def test_hh_applies_exact_freshness_window_to_details() -> None:
    old_detail = _detail()
    old_detail["published_at"] = "2026-09-24T11:59:59+00:00"

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/vacancies":
            return httpx.Response(200, json=_search(["123"]))
        return httpx.Response(200, json=old_detail)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        vacancies = await HHSource(client, oauth_token="token", max_retries=0).collect(
            _company(), OBSERVED_AT
        )

    assert vacancies == ()
