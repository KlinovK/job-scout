import asyncio
import logging
import re
from collections.abc import Sequence
from datetime import UTC, datetime
from html import unescape
from urllib.parse import quote
from uuid import uuid4

import httpx
from pydantic import ValidationError

from job_search.application.errors import (
    InvalidSourceConfigurationError,
    JobSourceError,
)
from job_search.domain.enums import RemotePolicy, VacancySource, VacancyStatus
from job_search.domain.models import Company, JobVacancy
from job_search.infrastructure.http import (
    DEFAULT_RETRY_POLICY,
    RetryPolicy,
    Sleep,
    get_with_retry,
)
from job_search.infrastructure.sources.recruitee.dto import (
    RecruiteeLocationDTO,
    RecruiteeOfferDTO,
    RecruiteeOffersEnvelopeDTO,
)


class RecruiteeSourceError(JobSourceError):
    """A Recruitee request or payload could not be processed safely."""


def _plain_text(value: str) -> str:
    without_tags = re.sub(r"<[^>]+>", " ", value)
    return " ".join(unescape(without_tags).split())


def _remote_policy(job: RecruiteeOfferDTO) -> RemotePolicy:
    if job.hybrid:
        return RemotePolicy.HYBRID
    if job.on_site:
        return RemotePolicy.ONSITE
    if job.remote or (job.location and "remote" in job.location.casefold()):
        return RemotePolicy.REMOTE
    return RemotePolicy.UNKNOWN


def _format_location(location: RecruiteeLocationDTO) -> str | None:
    parts: list[str] = []
    for value in (location.city, location.state, location.country):
        if value and value.casefold() not in {item.casefold() for item in parts}:
            parts.append(value)
    if not parts and location.name:
        parts.append(location.name)
    return ", ".join(parts) or None


def _work_location(job: RecruiteeOfferDTO) -> str | None:
    values: list[str] = []
    if job.location:
        values.append(job.location)
    for location in job.locations:
        formatted = _format_location(location)
        if formatted and formatted.casefold() not in {
            value.casefold() for value in values
        }:
            values.append(formatted)
    return "; ".join(values) or None


def _published_at(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Recruitee published_at must be timezone-aware")
    return value.astimezone(UTC)


def _salary(job: RecruiteeOfferDTO) -> str | None:
    if job.salary is None:
        return None
    amount = " - ".join(value for value in (job.salary.min, job.salary.max) if value)
    if not amount:
        return None
    suffix = " ".join(
        value for value in (job.salary.currency, job.salary.period) if value
    )
    return " ".join(value for value in (amount, suffix) if value)


class RecruiteeSource:
    _BASE_DOMAIN = "recruitee.com"

    def __init__(
        self,
        http_client: httpx.AsyncClient,
        *,
        retry_policy: RetryPolicy = DEFAULT_RETRY_POLICY,
        sleep: Sleep = asyncio.sleep,
        logger: logging.Logger | None = None,
    ) -> None:
        self._http_client = http_client
        self._retry_policy = retry_policy
        self._sleep = sleep
        self._logger = logger or logging.getLogger(__name__)

    async def collect(
        self,
        company: Company,
        observed_at: datetime,
    ) -> Sequence[JobVacancy]:
        identifier = quote(company.ats_identifier, safe="")
        url = f"https://{identifier}.{self._BASE_DOMAIN}/api/offers/"
        try:
            response = await get_with_retry(
                self._http_client,
                url,
                provider=VacancySource.RECRUITEE.value,
                policy=self._retry_policy,
                sleep=self._sleep,
                logger=self._logger,
            )
            response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise RecruiteeSourceError(
                f"Recruitee request timed out for {company.name}"
            ) from exc
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                raise InvalidSourceConfigurationError(
                    f"Unknown Recruitee site for {company.name}"
                ) from exc
            raise RecruiteeSourceError(
                f"Recruitee returned HTTP {exc.response.status_code} for {company.name}"
            ) from exc
        except httpx.RequestError as exc:
            raise RecruiteeSourceError(
                f"Recruitee request failed for {company.name}: {exc}"
            ) from exc

        try:
            envelope = RecruiteeOffersEnvelopeDTO.model_validate_json(response.content)
        except ValidationError as exc:
            raise RecruiteeSourceError(
                f"Malformed Recruitee response for {company.name}"
            ) from exc

        raw_count = len(envelope.offers)
        malformed_count = 0
        vacancies: list[JobVacancy] = []
        for index, raw_job in enumerate(envelope.offers):
            try:
                job = RecruiteeOfferDTO.model_validate(raw_job)
                published_at = _published_at(job.published_at)
                description = _plain_text(
                    "\n".join(
                        part for part in (job.description, job.requirements) if part
                    )
                )
                vacancy = JobVacancy(
                    id=uuid4(),
                    company_id=company.id,
                    title=job.title,
                    description=description,
                    company_location=company.country,
                    work_location=_work_location(job),
                    remote_policy=_remote_policy(job),
                    url=str(job.careers_url),
                    source=VacancySource.RECRUITEE,
                    source_job_id=str(job.id),
                    published_at=published_at,
                    first_seen_at=observed_at,
                    last_seen_at=observed_at,
                    status=VacancyStatus.ACTIVE,
                    employer_name=job.company_name,
                    salary=_salary(job),
                    employment=job.employment_type_code,
                    experience=job.experience_code,
                )
            except (ValidationError, ValueError) as exc:
                malformed_count += 1
                self._logger.debug(
                    "Skipping malformed Recruitee offer",
                    extra={
                        "company": company.name,
                        "source": VacancySource.RECRUITEE.value,
                        "job_index": index,
                        "error_category": type(exc).__name__,
                    },
                )
                continue
            vacancies.append(vacancy)

        if malformed_count:
            self._logger.warning(
                "Skipped %d malformed Recruitee offers for %s out of %d records",
                malformed_count,
                company.name,
                raw_count,
                extra={
                    "company": company.name,
                    "source": VacancySource.RECRUITEE.value,
                    "malformed_count": malformed_count,
                    "raw_count": raw_count,
                },
            )
        if raw_count and malformed_count == raw_count:
            raise RecruiteeSourceError(
                f"All {raw_count} Recruitee offers were malformed for {company.name}"
            )
        return tuple(vacancies)
