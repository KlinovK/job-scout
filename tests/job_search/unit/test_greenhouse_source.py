from datetime import UTC, datetime

import httpx
import pytest

from job_search.application.errors import InvalidSourceConfigurationError
from job_search.application.models import CollectionCoverage
from job_search.domain.enums import RemotePolicy
from job_search.infrastructure.http import RetryPolicy
from job_search.infrastructure.sources.greenhouse import (
    GreenhouseSource,
    GreenhouseSourceError,
)
from tests.job_search.factories import make_company

OBSERVED_AT = datetime(2026, 9, 17, 10, 0, tzinfo=UTC)
NO_RETRY = RetryPolicy(max_retries=0)


def _job(job_id: int, title: str = "iOS Engineer") -> dict[str, object]:
    return {
        "id": job_id,
        "title": title,
        "absolute_url": f"https://boards.greenhouse.io/example/jobs/{job_id}",
        "location": {"name": "Remote - Europe"},
        "content": "<p>Build products with <strong>Swift</strong>.</p>",
    }


async def _collect(response: httpx.Response):
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["content"] == "true"
        return response

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        return await GreenhouseSource(client, retry_policy=NO_RETRY).collect(
            make_company(), OBSERVED_AT
        )


@pytest.mark.asyncio
async def test_successful_response_is_normalized() -> None:
    result = await _collect(
        httpx.Response(200, json={"jobs": [_job(42)], "meta": {"total": 1}})
    )

    assert result.coverage is CollectionCoverage.FULL_BOARD
    assert result.complete is True
    assert result.raw_count == 1
    assert result.malformed_count == 0
    assert result.pagination_exhausted is True
    assert result.reconciliation_eligible is True
    assert len(result.vacancies) == 1
    vacancy = result.vacancies[0]
    assert vacancy.source_job_id == "42"
    assert vacancy.title == "iOS Engineer"
    assert vacancy.description == "Build products with\nSwift\n."
    assert vacancy.work_location == "Remote - Europe"
    assert vacancy.remote_policy is RemotePolicy.REMOTE
    assert vacancy.first_seen_at == OBSERVED_AT
    assert vacancy.last_seen_at == OBSERVED_AT


@pytest.mark.asyncio
async def test_html_escaped_description_is_normalized() -> None:
    job = _job(43)
    job["content"] = "&lt;p&gt;Build with Swift &amp;amp; UIKit.&lt;/p&gt;"

    result = await _collect(httpx.Response(200, json={"jobs": [job]}))

    assert result.vacancies[0].description == "Build with Swift & UIKit."


@pytest.mark.asyncio
async def test_multiple_and_empty_responses() -> None:
    multiple = await _collect(
        httpx.Response(
            200,
            json={
                "jobs": [_job(1), _job(2, "Swift Engineer")],
                "meta": {"total": 2},
            },
        )
    )
    empty = await _collect(httpx.Response(200, json={"jobs": [], "meta": {"total": 0}}))

    assert [vacancy.source_job_id for vacancy in multiple.vacancies] == ["1", "2"]
    assert empty.vacancies == ()
    assert empty.reconciliation_eligible is True


@pytest.mark.asyncio
async def test_missing_or_mismatched_total_is_conservatively_incomplete() -> None:
    missing = await _collect(httpx.Response(200, json={"jobs": [_job(1)]}))
    mismatched = await _collect(
        httpx.Response(200, json={"jobs": [_job(1)], "meta": {"total": 2}})
    )

    assert missing.complete is False
    assert missing.pagination_exhausted is False
    assert mismatched.complete is False
    assert mismatched.pagination_exhausted is False


@pytest.mark.asyncio
async def test_http_failure_has_company_context() -> None:
    with pytest.raises(GreenhouseSourceError, match="HTTP 503.*Example"):
        await _collect(httpx.Response(503))


@pytest.mark.asyncio
async def test_unknown_board_is_invalid_configuration() -> None:
    with pytest.raises(InvalidSourceConfigurationError, match="Unknown Greenhouse"):
        await _collect(httpx.Response(404))


@pytest.mark.asyncio
async def test_timeout_is_wrapped() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(GreenhouseSourceError, match="timed out.*Example"):
            await GreenhouseSource(client, retry_policy=NO_RETRY).collect(
                make_company(), OBSERVED_AT
            )


@pytest.mark.asyncio
async def test_transient_status_is_retried_by_source() -> None:
    attempts = 0
    delays: list[float] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(503)
        return httpx.Response(200, json={"jobs": [_job(42)]})

    async def sleep(delay: float) -> None:
        delays.append(delay)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await GreenhouseSource(client, sleep=sleep).collect(
            make_company(), OBSERVED_AT
        )

    assert [item.source_job_id for item in result.vacancies] == ["42"]
    assert attempts == 2
    assert delays == [0.5]


@pytest.mark.asyncio
async def test_exhausted_transport_retry_keeps_provider_error_context() -> None:
    attempts = 0
    delays: list[float] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        raise httpx.ConnectError("unavailable", request=request)

    async def sleep(delay: float) -> None:
        delays.append(delay)

    policy = RetryPolicy(max_retries=1)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(GreenhouseSourceError, match="request failed.*Example"):
            await GreenhouseSource(
                client,
                retry_policy=policy,
                sleep=sleep,
            ).collect(make_company(), OBSERVED_AT)

    assert attempts == 2
    assert delays == [0.5]


@pytest.mark.asyncio
async def test_invalid_configuration_404_is_not_retried() -> None:
    attempts = 0
    delays: list[float] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(404)

    async def sleep(delay: float) -> None:
        delays.append(delay)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(InvalidSourceConfigurationError):
            await GreenhouseSource(client, sleep=sleep).collect(
                make_company(), OBSERVED_AT
            )

    assert attempts == 1
    assert delays == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, content=b"{"),
        httpx.Response(
            200,
            json={"jobs": [{"id": "bad"}, {"title": "Broken"}]},
        ),
    ],
)
async def test_successful_malformed_data_is_not_retried(
    response: httpx.Response,
) -> None:
    attempts = 0
    delays: list[float] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return response

    async def sleep(delay: float) -> None:
        delays.append(delay)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(GreenhouseSourceError):
            await GreenhouseSource(client, sleep=sleep).collect(
                make_company(), OBSERVED_AT
            )

    assert attempts == 1
    assert delays == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, content=b"{"),
        httpx.Response(200, json={"unexpected": []}),
        httpx.Response(200, json=[]),
    ],
)
async def test_malformed_response_is_rejected(response: httpx.Response) -> None:
    with pytest.raises(GreenhouseSourceError, match="Malformed"):
        await _collect(response)


@pytest.mark.asyncio
async def test_malformed_individual_job_is_skipped() -> None:
    result = await _collect(
        httpx.Response(
            200,
            json={
                "jobs": [{"id": "not-an-integer"}, _job(2)],
                "meta": {"total": 2},
            },
        )
    )

    assert [item.source_job_id for item in result.vacancies] == ["2"]
    assert result.raw_count == 2
    assert result.malformed_count == 1
    assert result.complete is False
    assert result.reconciliation_eligible is False


@pytest.mark.asyncio
async def test_all_malformed_jobs_fail_the_source() -> None:
    with pytest.raises(GreenhouseSourceError, match="All 2.*malformed"):
        await _collect(
            httpx.Response(
                200,
                json={"jobs": [{"id": "bad"}, {"title": "Broken"}]},
            )
        )


@pytest.mark.asyncio
async def test_domain_invalid_job_counts_as_malformed() -> None:
    with pytest.raises(GreenhouseSourceError, match="All 1.*malformed"):
        await _collect(
            httpx.Response(
                200,
                json={"jobs": [_job(1, title="")]},
            )
        )


@pytest.mark.asyncio
async def test_basic_remote_policy_variants() -> None:
    hybrid = _job(1)
    hybrid["location"] = {"name": "Hybrid - Amsterdam"}
    onsite = _job(2)
    onsite["location"] = {"name": "On-site - Warsaw"}
    unknown = _job(3)
    unknown["location"] = {"name": "Athens"}

    result = await _collect(
        httpx.Response(200, json={"jobs": [hybrid, onsite, unknown]})
    )

    assert [item.remote_policy for item in result.vacancies] == [
        RemotePolicy.HYBRID,
        RemotePolicy.ONSITE,
        RemotePolicy.UNKNOWN,
    ]
