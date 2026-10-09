import json
from datetime import UTC, datetime

import httpx
import pytest

from job_search.application.errors import InvalidSourceConfigurationError
from job_search.application.models import CollectionCoverage
from job_search.domain.enums import ATSType, RemotePolicy, VacancySource
from job_search.infrastructure.sources.agilefluent import (
    AgileFluentSearchProfile,
    AgileFluentSource,
    AgileFluentSourceError,
)
from tests.job_search.factories import make_company

OBSERVED_AT = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)
PROFILE = AgileFluentSearchProfile(
    roles=("iOS Engineer",),
    grades=("middle", "senior"),
)


def _company(identifier: str = "jobboard.agilefluent.ru"):
    return make_company(
        name="AgileFluent Job Board",
        ats_type=ATSType.AGILEFLUENT,
        ats_identifier=identifier,
    )


def _job(
    job_id: str,
    *,
    title: str = "Senior iOS Engineer",
    company_name: str = "Example Employer",
    work_format: str = "remote",
) -> dict[str, object]:
    return {
        "id": job_id,
        "companyName": company_name,
        "title": title,
        "role": "ios_engineer",
        "grade": "senior",
        "format": work_format,
        "formats": [work_format],
        "country": "nld",
        "countries": ["nld"],
        "city": "Amsterdam",
        "industry": "fintech",
        "visa": True,
        "description": "Build a native iOS application.",
        "skills": ["Swift", "SwiftUI"],
        "createdAtIso": "2026-09-24T11:30:00Z",
    }


async def _collect(response: httpx.Response):
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/jobs/search"
        payload = json.loads(request.content)
        assert payload["filters"]["roles"] == ["iOS Engineer"]
        assert payload["filters"]["grades"] == ["middle", "senior"]
        assert payload["filters"]["since"] == "24h"
        assert {"workplaces": ["remote"]} in payload["filters"]["countries_workplaces"]
        return response

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        source = AgileFluentSource(client, profiles=(PROFILE,))
        return await source.collect(_company(), OBSERVED_AT)


@pytest.mark.asyncio
async def test_successful_response_is_normalized() -> None:
    result = await _collect(
        httpx.Response(200, json={"data": [_job("26939349")], "hasMore": False})
    )

    assert result.coverage is CollectionCoverage.ROLLING_WINDOW
    assert result.complete is True
    assert result.pagination_exhausted is True
    assert result.reconciliation_eligible is False
    assert len(result.vacancies) == 1
    vacancy = result.vacancies[0]
    assert vacancy.source is VacancySource.AGILEFLUENT
    assert vacancy.source_job_id == "26939349"
    assert vacancy.employer_name == "Example Employer"
    assert vacancy.work_location == "Amsterdam, Netherlands"
    assert vacancy.remote_policy is RemotePolicy.REMOTE
    assert vacancy.url == "https://jobboard.agilefluent.ru/jobs/26939349"
    assert vacancy.published_at == datetime(2026, 9, 24, 11, 30, tzinfo=UTC)
    assert "Skills: Swift, SwiftUI." in vacancy.description
    assert "Visa sponsorship: available." in vacancy.description


@pytest.mark.asyncio
async def test_empty_response_is_empty() -> None:
    result = await _collect(httpx.Response(200, json={"data": [], "hasMore": False}))

    assert result.vacancies == ()
    assert result.complete is True
    assert result.reconciliation_eligible is False


@pytest.mark.asyncio
async def test_pagination_and_cross_page_deduplication() -> None:
    requests: list[int] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        page = payload["pagination"]["page"]
        requests.append(page)
        if page == 1:
            return httpx.Response(
                200,
                json={"data": [_job("one")], "hasMore": True},
            )
        return httpx.Response(
            200,
            json={"data": [_job("one"), _job("two")], "hasMore": False},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await AgileFluentSource(
            client,
            profiles=(PROFILE,),
            page_size=1,
        ).collect(_company(), OBSERVED_AT)

    assert requests == [1, 2]
    assert [item.source_job_id for item in result.vacancies] == ["one", "two"]
    assert result.pagination_exhausted is True


@pytest.mark.asyncio
async def test_malformed_individual_job_is_skipped() -> None:
    result = await _collect(
        httpx.Response(
            200,
            json={"data": [{"id": "broken"}, _job("valid")], "hasMore": False},
        )
    )

    assert [item.source_job_id for item in result.vacancies] == ["valid"]
    assert result.malformed_count == 1
    assert result.complete is False
    assert result.reconciliation_eligible is False


@pytest.mark.asyncio
async def test_all_malformed_jobs_fail_the_source() -> None:
    with pytest.raises(AgileFluentSourceError, match="All 2.*malformed"):
        await _collect(
            httpx.Response(
                200,
                json={
                    "data": [{"id": "broken"}, {"title": "Broken"}],
                    "hasMore": False,
                },
            )
        )


@pytest.mark.asyncio
async def test_malformed_page_then_filtered_page_produces_successful_empty() -> None:
    pages: list[int] = []
    staff = _job("staff")
    staff["grade"] = "staff"

    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        page = payload["pagination"]["page"]
        pages.append(page)
        if page == 1:
            return httpx.Response(
                200,
                json={"data": [{"id": "broken"}], "hasMore": True},
            )
        return httpx.Response(
            200,
            json={"data": [staff], "hasMore": False},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await AgileFluentSource(
            client,
            profiles=(PROFILE,),
        ).collect(_company(), OBSERVED_AT)

    assert pages == [1, 2]
    assert result.vacancies == ()
    assert result.malformed_count == 1
    assert result.complete is False


@pytest.mark.asyncio
async def test_server_grade_mismatch_is_filtered_locally() -> None:
    staff = _job("staff")
    staff["grade"] = "staff"
    filtered_only = await _collect(
        httpx.Response(
            200,
            json={"data": [staff], "hasMore": False},
        )
    )
    mixed = await _collect(
        httpx.Response(
            200,
            json={"data": [staff, _job("senior")], "hasMore": False},
        )
    )

    assert filtered_only.vacancies == ()
    assert filtered_only.complete is True
    assert filtered_only.reconciliation_eligible is False
    assert [item.source_job_id for item in mixed.vacancies] == ["senior"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, content=b"{"),
        httpx.Response(200, json={"jobs": []}),
        httpx.Response(200, json=[]),
    ],
)
async def test_malformed_envelope_is_rejected(response: httpx.Response) -> None:
    with pytest.raises(AgileFluentSourceError, match="Malformed"):
        await _collect(response)


@pytest.mark.asyncio
async def test_http_failure_is_wrapped() -> None:
    with pytest.raises(AgileFluentSourceError, match="HTTP 429.*AgileFluent"):
        await _collect(httpx.Response(429))


@pytest.mark.asyncio
async def test_timeout_is_wrapped() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(AgileFluentSourceError, match="timed out"):
            await AgileFluentSource(client, profiles=(PROFILE,)).collect(
                _company(),
                OBSERVED_AT,
            )


@pytest.mark.asyncio
async def test_invalid_source_identifier_is_rejected_before_request() -> None:
    async with httpx.AsyncClient() as client:
        with pytest.raises(InvalidSourceConfigurationError, match="identifier"):
            await AgileFluentSource(client, profiles=(PROFILE,)).collect(
                _company("wrong.example"),
                OBSERVED_AT,
            )


@pytest.mark.asyncio
async def test_pagination_safety_limit_is_enforced() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [], "hasMore": True})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        source = AgileFluentSource(
            client,
            profiles=(PROFILE,),
            max_pages_per_profile=1,
        )
        with pytest.raises(AgileFluentSourceError, match="safety limit"):
            await source.collect(_company(), OBSERVED_AT)


@pytest.mark.asyncio
async def test_remote_policy_variants() -> None:
    response = httpx.Response(
        200,
        json={
            "data": [
                _job("remote", work_format="remote"),
                _job("hybrid", work_format="hybrid"),
                _job("office", work_format="office"),
            ],
            "hasMore": False,
        },
    )
    result = await _collect(response)

    assert [item.remote_policy for item in result.vacancies] == [
        RemotePolicy.REMOTE,
        RemotePolicy.HYBRID,
        RemotePolicy.ONSITE,
    ]
