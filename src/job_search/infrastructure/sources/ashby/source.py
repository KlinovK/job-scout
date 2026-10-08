import logging
from collections.abc import Sequence
from datetime import UTC, datetime
from urllib.parse import quote, urlparse
from uuid import uuid4

import httpx
from pydantic import ValidationError

from job_search.application.errors import (
    InvalidSourceConfigurationError,
    JobSourceError,
)
from job_search.domain.enums import RemotePolicy, VacancySource, VacancyStatus
from job_search.domain.models import Company, JobVacancy
from job_search.infrastructure.sources.ashby.dto import (
    AshbyJobDTO,
    AshbyJobsEnvelopeDTO,
)


class AshbySourceError(JobSourceError):
    """An Ashby request or payload could not be processed safely."""


def _remote_policy(job: AshbyJobDTO) -> RemotePolicy:
    value = (job.workplaceType or "").casefold()
    if job.isRemote or value == "remote":
        return RemotePolicy.REMOTE
    if value == "hybrid":
        return RemotePolicy.HYBRID
    if value in {"onsite", "on-site"}:
        return RemotePolicy.ONSITE
    return RemotePolicy.UNKNOWN


def _source_job_id(job_url: str) -> str:
    identifier = urlparse(job_url).path.rstrip("/").split("/")[-1]
    if not identifier:
        raise ValueError("Ashby job URL has no stable path identifier")
    return identifier


def _published_at(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Ashby publishedAt must be timezone-aware")
    return value.astimezone(UTC)


class AshbySource:
    _BASE_URL = "https://api.ashbyhq.com/posting-api/job-board"

    def __init__(
        self,
        http_client: httpx.AsyncClient,
        *,
        logger: logging.Logger | None = None,
    ) -> None:
        self._http_client = http_client
        self._logger = logger or logging.getLogger(__name__)

    async def collect(
        self,
        company: Company,
        observed_at: datetime,
    ) -> Sequence[JobVacancy]:
        identifier = quote(company.ats_identifier, safe="")
        url = f"{self._BASE_URL}/{identifier}"
        try:
            response = await self._http_client.get(url)
            response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise AshbySourceError(
                f"Ashby request timed out for {company.name}"
            ) from exc
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                raise InvalidSourceConfigurationError(
                    f"Unknown Ashby board for {company.name}"
                ) from exc
            raise AshbySourceError(
                f"Ashby returned HTTP {exc.response.status_code} for {company.name}"
            ) from exc
        except httpx.RequestError as exc:
            raise AshbySourceError(
                f"Ashby request failed for {company.name}: {exc}"
            ) from exc

        try:
            envelope = AshbyJobsEnvelopeDTO.model_validate_json(response.content)
        except ValidationError as exc:
            raise AshbySourceError(
                f"Malformed Ashby response for {company.name}"
            ) from exc

        raw_count = len(envelope.jobs)
        malformed_count = 0
        vacancies: list[JobVacancy] = []
        for index, raw_job in enumerate(envelope.jobs):
            try:
                job = AshbyJobDTO.model_validate(raw_job)
                if not job.isListed:
                    continue
                job_url = str(job.jobUrl)
                source_job_id = job.id or _source_job_id(job_url)
                published_at = _published_at(job.publishedAt)
                vacancy = JobVacancy(
                    id=uuid4(),
                    company_id=company.id,
                    title=job.title,
                    description=job.descriptionPlain,
                    company_location=company.country,
                    work_location=job.location,
                    remote_policy=_remote_policy(job),
                    url=job_url,
                    source=VacancySource.ASHBY,
                    source_job_id=source_job_id,
                    published_at=published_at,
                    first_seen_at=observed_at,
                    last_seen_at=observed_at,
                    status=VacancyStatus.ACTIVE,
                )
            except (ValidationError, ValueError) as exc:
                malformed_count += 1
                self._logger.debug(
                    "Skipping malformed Ashby job",
                    extra={
                        "company": company.name,
                        "source": VacancySource.ASHBY.value,
                        "job_index": index,
                        "error_category": type(exc).__name__,
                    },
                )
                continue
            vacancies.append(vacancy)

        if malformed_count:
            self._logger.warning(
                "Skipped %d malformed Ashby jobs for %s out of %d records",
                malformed_count,
                company.name,
                raw_count,
                extra={
                    "company": company.name,
                    "source": VacancySource.ASHBY.value,
                    "malformed_count": malformed_count,
                    "raw_count": raw_count,
                },
            )
        if raw_count and malformed_count == raw_count:
            raise AshbySourceError(
                f"All {raw_count} Ashby jobs were malformed for {company.name}"
            )
        return tuple(vacancies)
