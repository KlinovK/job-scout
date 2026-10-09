import asyncio
import logging
from datetime import UTC, datetime
from urllib.parse import quote
from uuid import uuid4

import httpx
from pydantic import ValidationError

from job_search.application.errors import (
    InvalidSourceConfigurationError,
    JobSourceError,
)
from job_search.application.models import (
    CollectionCoverage,
    SourceCollectionResult,
)
from job_search.domain.enums import RemotePolicy, VacancySource, VacancyStatus
from job_search.domain.models import Company, JobVacancy
from job_search.infrastructure.http import (
    DEFAULT_RETRY_POLICY,
    RetryPolicy,
    Sleep,
    get_with_retry,
)
from job_search.infrastructure.sources.lever.dto import (
    LeverPostingDTO,
    LeverPostingsEnvelopeDTO,
)


class LeverSourceError(JobSourceError):
    """A Lever request or payload could not be processed safely."""


def _remote_policy(workplace_type: str | None, location: str | None) -> RemotePolicy:
    value = (workplace_type or "").casefold()
    if value == "remote":
        return RemotePolicy.REMOTE
    if value == "hybrid":
        return RemotePolicy.HYBRID
    if value in {"on-site", "onsite"}:
        return RemotePolicy.ONSITE
    if location and "remote" in location.casefold():
        return RemotePolicy.REMOTE
    return RemotePolicy.UNKNOWN


def _published_at(milliseconds: int | None) -> datetime | None:
    if milliseconds is None:
        return None
    return datetime.fromtimestamp(milliseconds / 1000, tz=UTC)


class LeverSource:
    _BASE_URL = "https://api.lever.co/v0/postings"

    def __init__(
        self,
        http_client: httpx.AsyncClient,
        *,
        retry_policy: RetryPolicy = DEFAULT_RETRY_POLICY,
        sleep: Sleep = asyncio.sleep,
        logger: logging.Logger | None = None,
        page_size: int = 100,
        max_pages: int = 100,
    ) -> None:
        if page_size <= 0:
            raise ValueError("page_size must be greater than zero")
        if max_pages <= 0:
            raise ValueError("max_pages must be greater than zero")
        self._http_client = http_client
        self._retry_policy = retry_policy
        self._sleep = sleep
        self._logger = logger or logging.getLogger(__name__)
        self._page_size = page_size
        self._max_pages = max_pages

    async def collect(
        self,
        company: Company,
        observed_at: datetime,
    ) -> SourceCollectionResult:
        identifier = quote(company.ats_identifier, safe="")
        url = f"{self._BASE_URL}/{identifier}"

        raw_jobs: list[object] = []
        skip = 0
        for _ in range(self._max_pages):
            envelope = await self._fetch_page(company, url, skip)
            if not envelope.root:
                break
            raw_jobs.extend(envelope.root)
            skip += len(envelope.root)
        else:
            raise LeverSourceError(
                f"Lever pagination exceeded safety limit for {company.name}"
            )

        raw_count = len(raw_jobs)
        malformed_count = 0
        vacancies: list[JobVacancy] = []
        for index, raw_job in enumerate(raw_jobs):
            try:
                job = LeverPostingDTO.model_validate(raw_job)
                published_at = _published_at(job.createdAt)
                location = job.categories.location
                vacancy = JobVacancy(
                    id=uuid4(),
                    company_id=company.id,
                    title=job.text,
                    description=job.descriptionPlain,
                    company_location=company.country,
                    work_location=location,
                    remote_policy=_remote_policy(job.workplaceType, location),
                    url=str(job.hostedUrl),
                    source=VacancySource.LEVER,
                    source_job_id=job.id,
                    published_at=published_at,
                    first_seen_at=observed_at,
                    last_seen_at=observed_at,
                    status=VacancyStatus.ACTIVE,
                )
            except (ValidationError, ValueError, OSError) as exc:
                malformed_count += 1
                self._logger.debug(
                    "Skipping malformed Lever job",
                    extra={
                        "company": company.name,
                        "source": VacancySource.LEVER.value,
                        "job_index": index,
                        "error_category": type(exc).__name__,
                    },
                )
                continue
            vacancies.append(vacancy)

        if malformed_count:
            self._logger.warning(
                "Skipped %d malformed Lever jobs for %s out of %d records",
                malformed_count,
                company.name,
                raw_count,
                extra={
                    "company": company.name,
                    "source": VacancySource.LEVER.value,
                    "malformed_count": malformed_count,
                    "raw_count": raw_count,
                },
            )
        if raw_count and malformed_count == raw_count:
            raise LeverSourceError(
                f"All {raw_count} Lever jobs were malformed for {company.name}"
            )
        return SourceCollectionResult(
            vacancies=tuple(vacancies),
            coverage=CollectionCoverage.FULL_BOARD,
            complete=malformed_count == 0,
            raw_count=raw_count,
            malformed_count=malformed_count,
            pagination_exhausted=True,
        )

    async def _fetch_page(
        self,
        company: Company,
        url: str,
        skip: int,
    ) -> LeverPostingsEnvelopeDTO:
        try:
            response = await get_with_retry(
                self._http_client,
                url,
                provider=VacancySource.LEVER.value,
                params={
                    "mode": "json",
                    "skip": str(skip),
                    "limit": str(self._page_size),
                },
                policy=self._retry_policy,
                sleep=self._sleep,
                logger=self._logger,
            )
            response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise LeverSourceError(
                f"Lever request timed out for {company.name}"
            ) from exc
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                raise InvalidSourceConfigurationError(
                    f"Unknown Lever site for {company.name}"
                ) from exc
            raise LeverSourceError(
                f"Lever returned HTTP {exc.response.status_code} for {company.name}"
            ) from exc
        except httpx.RequestError as exc:
            raise LeverSourceError(
                f"Lever request failed for {company.name}: {exc}"
            ) from exc

        try:
            return LeverPostingsEnvelopeDTO.model_validate_json(response.content)
        except ValidationError as exc:
            raise LeverSourceError(
                f"Malformed Lever response for {company.name}"
            ) from exc
