from datetime import UTC, datetime

import httpx
import pytest

from job_search.application.errors import InvalidSourceConfigurationError
from job_search.application.models import CollectionCoverage
from job_search.domain.enums import ATSType, RemotePolicy, VacancySource
from job_search.infrastructure.http import RetryPolicy
from job_search.infrastructure.sources.ashby import AshbySource, AshbySourceError
from tests.job_search.factories import make_company

OBSERVED_AT = datetime(2026, 9, 17, 10, 0, tzinfo=UTC)
NO_RETRY = RetryPolicy(max_retries=0)


def _job(
    job_id: str,
    title: str = "iOS Engineer",
    workplace_type: str = "Remote",
    *,
    is_remote: bool | None = True,
    is_listed: bool = True,
    explicit_id: bool = False,
) -> dict[str, object]:
    job: dict[str, object] = {
        "title": title,
        "location": "Europe",
        "isListed": is_listed,
        "isRemote": is_remote,
        "workplaceType": workplace_type,
        "descriptionPlain": "Build products with Swift.",
        "publishedAt": "2026-09-17T10:50:00+00:00",
        "jobUrl": f"https://jobs.ashbyhq.com/example/{job_id}",
        "applyUrl": f"https://jobs.ashbyhq.com/example/{job_id}/application",
    }
    if explicit_id:
        job["id"] = f"explicit-{job_id}"
    return job


async def _collect(response: httpx.Response):
    company = make_company(ats_type=ATSType.ASHBY)

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/posting-api/job-board/example"
        return response

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        return await AshbySource(client, retry_policy=NO_RETRY).collect(
            company, OBSERVED_AT
        )


@pytest.mark.asyncio
async def test_successful_response_is_normalized() -> None:
    result = await _collect(
        httpx.Response(200, json={"apiVersion": "1", "jobs": [_job("job-42")]})
    )

    assert result.coverage is CollectionCoverage.FULL_BOARD
    assert result.complete is True
    assert result.pagination_exhausted is True
    assert result.reconciliation_eligible is True
    assert len(result.vacancies) == 1
    vacancy = result.vacancies[0]
    assert vacancy.source is VacancySource.ASHBY
    assert vacancy.source_job_id == "job-42"
    assert vacancy.remote_policy is RemotePolicy.REMOTE
    assert vacancy.published_at == datetime(2026, 9, 17, 10, 50, tzinfo=UTC)
    assert vacancy.first_seen_at == OBSERVED_AT


@pytest.mark.asyncio
async def test_multiple_empty_and_unlisted_responses() -> None:
    multiple = await _collect(
        httpx.Response(
            200,
            json={
                "apiVersion": "1",
                "jobs": [_job("one"), _job("hidden", is_listed=False), _job("two")],
            },
        )
    )
    empty = await _collect(httpx.Response(200, json={"apiVersion": "1", "jobs": []}))
    unlisted_only = await _collect(
        httpx.Response(
            200,
            json={
                "apiVersion": "1",
                "jobs": [_job("hidden", is_listed=False)],
            },
        )
    )

    assert [item.source_job_id for item in multiple.vacancies] == ["one", "two"]
    assert multiple.raw_count == 3
    assert multiple.complete is True
    assert multiple.reconciliation_eligible is True
    assert empty.vacancies == ()
    assert empty.reconciliation_eligible is True
    assert unlisted_only.vacancies == ()
    assert unlisted_only.raw_count == 1
    assert unlisted_only.reconciliation_eligible is True


@pytest.mark.asyncio
async def test_explicit_id_is_preferred_and_null_remote_is_supported() -> None:
    result = await _collect(
        httpx.Response(
            200,
            json={
                "apiVersion": "1",
                "jobs": [_job("url-id", is_remote=None, explicit_id=True)],
            },
        )
    )

    assert result.vacancies[0].source_job_id == "explicit-url-id"
    assert result.vacancies[0].remote_policy is RemotePolicy.REMOTE


@pytest.mark.asyncio
async def test_unknown_board_is_invalid_configuration() -> None:
    with pytest.raises(InvalidSourceConfigurationError, match="Unknown Ashby board"):
        await _collect(httpx.Response(404))


@pytest.mark.asyncio
async def test_http_failure_has_company_context() -> None:
    with pytest.raises(AshbySourceError, match="HTTP 503.*Example"):
        await _collect(httpx.Response(503))


@pytest.mark.asyncio
async def test_timeout_is_wrapped() -> None:
    company = make_company(ats_type=ATSType.ASHBY)

    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(AshbySourceError, match="timed out.*Example"):
            await AshbySource(client, retry_policy=NO_RETRY).collect(
                company, OBSERVED_AT
            )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, content=b"{"),
        httpx.Response(200, json={"jobs": []}),
        httpx.Response(200, json=[]),
    ],
)
async def test_malformed_response_is_rejected(response: httpx.Response) -> None:
    with pytest.raises(AshbySourceError, match="Malformed"):
        await _collect(response)


@pytest.mark.asyncio
async def test_malformed_individual_job_is_skipped() -> None:
    result = await _collect(
        httpx.Response(
            200,
            json={"apiVersion": "1", "jobs": [{"title": "Broken"}, _job("valid")]},
        )
    )

    assert [item.source_job_id for item in result.vacancies] == ["valid"]
    assert result.malformed_count == 1
    assert result.complete is False
    assert result.reconciliation_eligible is False


@pytest.mark.asyncio
async def test_all_malformed_jobs_fail_the_source() -> None:
    with pytest.raises(AshbySourceError, match="All 2.*malformed"):
        await _collect(
            httpx.Response(
                200,
                json={
                    "apiVersion": "1",
                    "jobs": [{"title": "Broken"}, {"isListed": True}],
                },
            )
        )


@pytest.mark.asyncio
async def test_malformed_and_unlisted_jobs_produce_successful_empty() -> None:
    result = await _collect(
        httpx.Response(
            200,
            json={
                "apiVersion": "1",
                "jobs": [
                    {"title": "Broken"},
                    _job("hidden", is_listed=False),
                ],
            },
        )
    )

    assert result.vacancies == ()
    assert result.raw_count == 2
    assert result.malformed_count == 1
    assert result.complete is False
    assert result.reconciliation_eligible is False


@pytest.mark.asyncio
async def test_remote_policy_variants() -> None:
    hybrid = _job("hybrid", workplace_type="Hybrid", is_remote=False)
    onsite = _job("onsite", workplace_type="OnSite", is_remote=False)
    unknown = _job("unknown", workplace_type="Office", is_remote=False)
    result = await _collect(
        httpx.Response(
            200,
            json={"apiVersion": "1", "jobs": [hybrid, onsite, unknown]},
        )
    )

    assert [item.remote_policy for item in result.vacancies] == [
        RemotePolicy.HYBRID,
        RemotePolicy.ONSITE,
        RemotePolicy.UNKNOWN,
    ]
