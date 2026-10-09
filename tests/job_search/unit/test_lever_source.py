from datetime import UTC, datetime

import httpx
import pytest

from job_search.application.errors import InvalidSourceConfigurationError
from job_search.application.models import CollectionCoverage
from job_search.domain.enums import ATSType, RemotePolicy, VacancySource
from job_search.infrastructure.http import RetryPolicy
from job_search.infrastructure.sources.lever import LeverSource, LeverSourceError
from tests.job_search.factories import make_company

OBSERVED_AT = datetime(2026, 9, 17, 10, 0, tzinfo=UTC)
NO_RETRY = RetryPolicy(max_retries=0)


def _job(
    job_id: str,
    title: str = "iOS Engineer",
    workplace_type: str = "remote",
) -> dict[str, object]:
    return {
        "id": job_id,
        "text": title,
        "categories": {"location": "Europe", "allLocations": ["Europe"]},
        "descriptionPlain": "Build products with Swift.",
        "hostedUrl": f"https://jobs.lever.co/example/{job_id}",
        "applyUrl": f"https://jobs.lever.co/example/{job_id}/apply",
        "workplaceType": workplace_type,
        "createdAt": 1_789_637_400_000,
    }


async def _collect(response: httpx.Response):
    company = make_company(ats_type=ATSType.LEVER)

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v0/postings/example"
        assert request.url.params["mode"] == "json"
        assert request.url.params["limit"] == "100"
        if request.url.params["skip"] != "0":
            return httpx.Response(200, json=[])
        return response

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        return await LeverSource(client, retry_policy=NO_RETRY).collect(
            company, OBSERVED_AT
        )


@pytest.mark.asyncio
async def test_successful_response_is_normalized() -> None:
    result = await _collect(httpx.Response(200, json=[_job("job-42")]))

    assert result.coverage is CollectionCoverage.FULL_BOARD
    assert result.complete is True
    assert result.pagination_exhausted is True
    assert result.reconciliation_eligible is True
    assert len(result.vacancies) == 1
    vacancy = result.vacancies[0]
    assert vacancy.source is VacancySource.LEVER
    assert vacancy.source_job_id == "job-42"
    assert vacancy.title == "iOS Engineer"
    assert vacancy.work_location == "Europe"
    assert vacancy.remote_policy is RemotePolicy.REMOTE
    assert vacancy.published_at == datetime(2026, 9, 17, 9, 30, tzinfo=UTC)
    assert vacancy.first_seen_at == OBSERVED_AT


@pytest.mark.asyncio
async def test_multiple_and_empty_responses() -> None:
    multiple = await _collect(
        httpx.Response(200, json=[_job("one"), _job("two", "Swift Engineer")])
    )
    empty = await _collect(httpx.Response(200, json=[]))

    assert [item.source_job_id for item in multiple.vacancies] == ["one", "two"]
    assert empty.vacancies == ()


@pytest.mark.asyncio
async def test_exhaustive_pagination_uses_skip_and_limit() -> None:
    skips: list[int] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        skips.append(int(request.url.params["skip"]))
        skip = request.url.params["skip"]
        if skip == "0":
            return httpx.Response(200, json=[_job("one"), _job("two")])
        if skip == "2":
            return httpx.Response(200, json=[_job("three")])
        return httpx.Response(200, json=[])

    company = make_company(ats_type=ATSType.LEVER)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await LeverSource(
            client,
            retry_policy=NO_RETRY,
            page_size=2,
        ).collect(company, OBSERVED_AT)

    assert skips == [0, 2, 3]
    assert [item.source_job_id for item in result.vacancies] == [
        "one",
        "two",
        "three",
    ]
    assert result.raw_count == 3
    assert result.pagination_exhausted is True
    assert result.reconciliation_eligible is True


@pytest.mark.asyncio
async def test_pagination_limit_fails_instead_of_returning_truncated_result() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[_job("one")])

    company = make_company(ats_type=ATSType.LEVER)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(LeverSourceError, match="safety limit"):
            await LeverSource(
                client,
                retry_policy=NO_RETRY,
                page_size=1,
                max_pages=1,
            ).collect(company, OBSERVED_AT)


@pytest.mark.asyncio
async def test_unknown_board_is_invalid_configuration() -> None:
    with pytest.raises(InvalidSourceConfigurationError, match="Unknown Lever site"):
        await _collect(httpx.Response(404))


@pytest.mark.asyncio
async def test_http_failure_has_company_context() -> None:
    with pytest.raises(LeverSourceError, match="HTTP 503.*Example"):
        await _collect(httpx.Response(503))


@pytest.mark.asyncio
async def test_timeout_is_wrapped() -> None:
    company = make_company(ats_type=ATSType.LEVER)

    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(LeverSourceError, match="timed out.*Example"):
            await LeverSource(client, retry_policy=NO_RETRY).collect(
                company, OBSERVED_AT
            )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, content=b"{"),
        httpx.Response(200, json={"jobs": []}),
        httpx.Response(200, json="unexpected"),
    ],
)
async def test_malformed_response_is_rejected(response: httpx.Response) -> None:
    with pytest.raises(LeverSourceError, match="Malformed"):
        await _collect(response)


@pytest.mark.asyncio
async def test_malformed_individual_job_is_skipped() -> None:
    result = await _collect(httpx.Response(200, json=[{"id": "bad"}, _job("valid")]))

    assert [item.source_job_id for item in result.vacancies] == ["valid"]
    assert result.malformed_count == 1
    assert result.complete is False
    assert result.reconciliation_eligible is False


@pytest.mark.asyncio
async def test_all_malformed_jobs_fail_the_source() -> None:
    with pytest.raises(LeverSourceError, match="All 2.*malformed"):
        await _collect(httpx.Response(200, json=[{"id": "bad"}, {"text": "Broken"}]))


@pytest.mark.asyncio
async def test_remote_policy_variants() -> None:
    hybrid = _job("hybrid", workplace_type="hybrid")
    onsite = _job("onsite", workplace_type="on-site")
    unknown = _job("unknown", workplace_type="office")
    result = await _collect(httpx.Response(200, json=[hybrid, onsite, unknown]))

    assert [item.remote_policy for item in result.vacancies] == [
        RemotePolicy.HYBRID,
        RemotePolicy.ONSITE,
        RemotePolicy.UNKNOWN,
    ]
