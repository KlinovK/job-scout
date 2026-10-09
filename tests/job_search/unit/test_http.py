import asyncio

import httpx
import pytest

from job_search.infrastructure.http import RetryPolicy, get_with_retry


@pytest.mark.asyncio
async def test_transport_error_is_retried_then_succeeds() -> None:
    attempts = 0
    delays: list[float] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise httpx.ConnectError("temporary", request=request)
        return httpx.Response(200, json={"ok": True})

    async def sleep(delay: float) -> None:
        delays.append(delay)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        response = await get_with_retry(
            client,
            "https://example.test/jobs",
            provider="test",
            sleep=sleep,
        )

    assert response.status_code == 200
    assert attempts == 2
    assert delays == [0.5]


@pytest.mark.asyncio
async def test_transport_error_exhausts_bounded_attempts() -> None:
    attempts = 0
    delays: list[float] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        raise httpx.ConnectError("still unavailable", request=request)

    async def sleep(delay: float) -> None:
        delays.append(delay)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(httpx.ConnectError, match="still unavailable"):
            await get_with_retry(
                client,
                "https://example.test/jobs",
                provider="test",
                sleep=sleep,
            )

    assert attempts == 3
    assert delays == [0.5, 1.0]


@pytest.mark.asyncio
async def test_timeout_is_retried_then_succeeds() -> None:
    attempts = 0
    delays: list[float] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise httpx.ReadTimeout("temporary timeout", request=request)
        return httpx.Response(200)

    async def sleep(delay: float) -> None:
        delays.append(delay)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        response = await get_with_retry(
            client,
            "https://example.test/jobs",
            provider="test",
            sleep=sleep,
        )

    assert response.status_code == 200
    assert attempts == 2
    assert delays == [0.5]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error_type",
    [httpx.LocalProtocolError, httpx.UnsupportedProtocol],
)
async def test_deterministic_transport_error_is_not_retried(
    error_type: type[httpx.TransportError],
) -> None:
    attempts = 0
    delays: list[float] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        raise error_type("deterministic request error", request=request)

    async def sleep(delay: float) -> None:
        delays.append(delay)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(error_type):
            await get_with_retry(
                client,
                "https://example.test/jobs",
                provider="test",
                sleep=sleep,
            )

    assert attempts == 1
    assert delays == []


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [500, 502, 503, 504])
async def test_retryable_server_status_then_success(status: int) -> None:
    attempts = 0
    delays: list[float] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(status)
        return httpx.Response(200)

    async def sleep(delay: float) -> None:
        delays.append(delay)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        response = await get_with_retry(
            client,
            "https://example.test/jobs",
            provider="test",
            sleep=sleep,
        )

    assert response.status_code == 200
    assert attempts == 2
    assert delays == [0.5]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [400, 401, 403, 404, 501])
async def test_permanent_http_status_is_not_retried(status: int) -> None:
    attempts = 0
    delays: list[float] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(status)

    async def sleep(delay: float) -> None:
        delays.append(delay)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        response = await get_with_retry(
            client,
            "https://example.test/jobs",
            provider="test",
            sleep=sleep,
        )

    assert response.status_code == status
    assert attempts == 1
    assert delays == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("retry_after", "expected_delay"),
    [("2", 2.0), ("later", 0.5), ("999", 10.0)],
)
async def test_retry_after_delta_seconds_is_validated_and_capped(
    retry_after: str,
    expected_delay: float,
) -> None:
    attempts = 0
    delays: list[float] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(429, headers={"Retry-After": retry_after})
        return httpx.Response(200)

    async def sleep(delay: float) -> None:
        delays.append(delay)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        response = await get_with_retry(
            client,
            "https://example.test/jobs",
            provider="test",
            sleep=sleep,
        )

    assert response.status_code == 200
    assert attempts == 2
    assert delays == [expected_delay]


@pytest.mark.asyncio
async def test_backoff_is_exponential_and_capped_without_real_sleep() -> None:
    attempts = 0
    delays: list[float] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(503)

    async def sleep(delay: float) -> None:
        delays.append(delay)

    policy = RetryPolicy(
        max_retries=3,
        base_delay_seconds=4.0,
        max_delay_seconds=5.0,
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        response = await get_with_retry(
            client,
            "https://example.test/jobs",
            provider="test",
            policy=policy,
            sleep=sleep,
        )

    assert response.status_code == 503
    assert attempts == 4
    assert delays == [4.0, 5.0, 5.0]


@pytest.mark.asyncio
async def test_cancellation_propagates_without_retry_or_sleep() -> None:
    attempts = 0
    delays: list[float] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        raise asyncio.CancelledError

    async def sleep(delay: float) -> None:
        delays.append(delay)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(asyncio.CancelledError):
            await get_with_retry(
                client,
                "https://example.test/jobs",
                provider="test",
                sleep=sleep,
            )

    assert attempts == 1
    assert delays == []
