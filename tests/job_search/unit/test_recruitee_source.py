from datetime import UTC, datetime

import httpx
import pytest

from job_search.application.errors import InvalidSourceConfigurationError
from job_search.application.models import CollectionCoverage
from job_search.domain.enums import ATSType, RemotePolicy, VacancySource
from job_search.infrastructure.http import RetryPolicy
from job_search.infrastructure.sources.recruitee import (
    RecruiteeSource,
    RecruiteeSourceError,
)
from tests.job_search.factories import make_company

OBSERVED_AT = datetime(2026, 10, 1, 8, 0, tzinfo=UTC)
NO_RETRY = RetryPolicy(max_retries=0)


def _offer(
    offer_id: int,
    *,
    remote: bool = True,
    hybrid: bool = False,
    on_site: bool = False,
    requirements: str | None = "<p>Experience with UIKit.</p>",
) -> dict[str, object]:
    return {
        "id": offer_id,
        "title": "iOS Engineer",
        "careers_url": f"https://jobs.example.com/o/ios-engineer-{offer_id}",
        "company_name": "Example",
        "description": "<p>Build products with Swift.</p>",
        "requirements": requirements,
        "location": "Remote job" if remote else "Amsterdam",
        "locations": [
            {
                "name": "Amsterdam",
                "city": "Amsterdam",
                "state": "Noord-Holland",
                "country": "Netherlands",
            }
        ],
        "remote": remote,
        "hybrid": hybrid,
        "on_site": on_site,
        "published_at": "2026-10-01 07:30:00 UTC",
        "salary": {
            "min": "70000",
            "max": "80000",
            "currency": "EUR",
            "period": "year",
        },
        "employment_type_code": "full_time",
        "experience_code": "senior",
    }


async def _collect(response: httpx.Response):
    company = make_company(
        ats_type=ATSType.RECRUITEE,
        ats_identifier="example",
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://example.recruitee.com/api/offers/"
        return response

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        return await RecruiteeSource(client, retry_policy=NO_RETRY).collect(
            company, OBSERVED_AT
        )


@pytest.mark.asyncio
async def test_successful_response_is_normalized() -> None:
    result = await _collect(httpx.Response(200, json={"offers": [_offer(42)]}))

    assert result.coverage is CollectionCoverage.FULL_BOARD
    assert result.complete is True
    assert result.pagination_exhausted is True
    assert result.reconciliation_eligible is True
    assert len(result.vacancies) == 1
    vacancy = result.vacancies[0]
    assert vacancy.source is VacancySource.RECRUITEE
    assert vacancy.source_job_id == "42"
    assert vacancy.description == "Build products with Swift. Experience with UIKit."
    assert vacancy.work_location == "Remote job; Amsterdam, Noord-Holland, Netherlands"
    assert vacancy.remote_policy is RemotePolicy.REMOTE
    assert vacancy.published_at == datetime(2026, 10, 1, 7, 30, tzinfo=UTC)
    assert vacancy.salary == "70000 - 80000 EUR year"


@pytest.mark.asyncio
async def test_empty_response_is_empty() -> None:
    result = await _collect(httpx.Response(200, json={"offers": []}))

    assert result.vacancies == ()
    assert result.reconciliation_eligible is True


@pytest.mark.asyncio
async def test_remote_policy_variants() -> None:
    result = await _collect(
        httpx.Response(
            200,
            json={
                "offers": [
                    _offer(1, remote=False, hybrid=True),
                    _offer(2, remote=False, on_site=True),
                    _offer(3, remote=False),
                ]
            },
        )
    )

    assert [item.remote_policy for item in result.vacancies] == [
        RemotePolicy.HYBRID,
        RemotePolicy.ONSITE,
        RemotePolicy.UNKNOWN,
    ]


@pytest.mark.asyncio
async def test_null_requirements_are_supported() -> None:
    result = await _collect(
        httpx.Response(
            200,
            json={"offers": [_offer(42, requirements=None)]},
        )
    )

    assert result.vacancies[0].description == "Build products with Swift."


@pytest.mark.asyncio
async def test_unknown_site_is_invalid_configuration() -> None:
    with pytest.raises(InvalidSourceConfigurationError, match="Unknown Recruitee"):
        await _collect(httpx.Response(404))


@pytest.mark.asyncio
async def test_http_failure_has_company_context() -> None:
    with pytest.raises(RecruiteeSourceError, match="HTTP 503.*Example"):
        await _collect(httpx.Response(503))


@pytest.mark.asyncio
async def test_malformed_offer_is_skipped() -> None:
    result = await _collect(
        httpx.Response(
            200,
            json={"offers": [{"title": "Broken"}, _offer(42)]},
        )
    )

    assert [item.source_job_id for item in result.vacancies] == ["42"]
    assert result.raw_count == 2
    assert result.malformed_count == 1
    assert result.complete is False
    assert result.reconciliation_eligible is False


@pytest.mark.asyncio
async def test_all_malformed_offers_fail_the_source() -> None:
    with pytest.raises(RecruiteeSourceError, match="All 2.*malformed"):
        await _collect(
            httpx.Response(
                200,
                json={"offers": [{"title": "Broken"}, {"id": 2}]},
            )
        )
