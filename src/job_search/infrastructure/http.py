import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass

import httpx

Sleep = Callable[[float], Awaitable[None]]

_RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """Bounded retry policy where max_retries excludes the initial attempt."""

    max_retries: int = 2
    base_delay_seconds: float = 0.5
    max_delay_seconds: float = 10.0

    def __post_init__(self) -> None:
        if self.max_retries < 0:
            raise ValueError("max_retries must not be negative")
        if self.base_delay_seconds <= 0:
            raise ValueError("base_delay_seconds must be greater than zero")
        if self.max_delay_seconds <= 0:
            raise ValueError("max_delay_seconds must be greater than zero")

    @property
    def max_attempts(self) -> int:
        return self.max_retries + 1

    def retry_delay(self, retry_number: int, retry_after: str | None = None) -> float:
        if retry_number <= 0:
            raise ValueError("retry_number must be greater than zero")
        fallback = min(
            self.base_delay_seconds * (2.0 ** (retry_number - 1)),
            self.max_delay_seconds,
        )
        if retry_after is None:
            return fallback
        try:
            server_delay = int(retry_after.strip())
        except ValueError:
            return fallback
        if server_delay < 0:
            return fallback
        return min(float(server_delay), self.max_delay_seconds)


DEFAULT_RETRY_POLICY = RetryPolicy()


async def get_with_retry(
    client: httpx.AsyncClient,
    url: str,
    *,
    provider: str,
    params: Mapping[str, str] | None = None,
    headers: Mapping[str, str] | None = None,
    policy: RetryPolicy = DEFAULT_RETRY_POLICY,
    sleep: Sleep = asyncio.sleep,
    logger: logging.Logger | None = None,
) -> httpx.Response:
    """Execute an idempotent GET with one explicit, bounded retry owner."""

    retry_logger = logger or logging.getLogger(__name__)
    for attempt in range(1, policy.max_attempts + 1):
        try:
            response = await client.get(url, params=params, headers=headers)
        except httpx.TransportError as exc:
            if not _is_retryable_transport(exc) or attempt >= policy.max_attempts:
                raise
            delay = policy.retry_delay(attempt)
            _log_retry(
                retry_logger,
                provider=provider,
                attempt=attempt,
                max_attempts=policy.max_attempts,
                failure_category=type(exc).__name__,
                status_code=None,
                delay=delay,
            )
            await sleep(delay)
            continue

        if (
            response.status_code not in _RETRYABLE_STATUS_CODES
            or attempt >= policy.max_attempts
        ):
            return response

        retry_after = (
            response.headers.get("Retry-After") if response.status_code == 429 else None
        )
        delay = policy.retry_delay(attempt, retry_after)
        _log_retry(
            retry_logger,
            provider=provider,
            attempt=attempt,
            max_attempts=policy.max_attempts,
            failure_category="http_status",
            status_code=response.status_code,
            delay=delay,
        )
        await response.aclose()
        await sleep(delay)

    raise AssertionError("unreachable")


def _is_retryable_transport(error: httpx.TransportError) -> bool:
    return not isinstance(
        error,
        (httpx.LocalProtocolError, httpx.UnsupportedProtocol),
    )


def _log_retry(
    logger: logging.Logger,
    *,
    provider: str,
    attempt: int,
    max_attempts: int,
    failure_category: str,
    status_code: int | None,
    delay: float,
) -> None:
    logger.warning(
        "Retrying %s GET after %s (attempt %d/%d, delay %.2fs)",
        provider,
        failure_category,
        attempt,
        max_attempts,
        delay,
        extra={
            "provider": provider,
            "attempt": attempt,
            "max_attempts": max_attempts,
            "failure_category": failure_category,
            "http_status": status_code,
            "retry_delay_seconds": delay,
        },
    )
