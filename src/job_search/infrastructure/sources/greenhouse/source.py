import logging
import re
from collections.abc import Sequence
from datetime import datetime
from html import unescape
from html.parser import HTMLParser
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
from job_search.infrastructure.sources.greenhouse.dto import (
    GreenhouseJobDTO,
    GreenhouseJobsEnvelopeDTO,
)


class GreenhouseSourceError(JobSourceError):
    """A Greenhouse request or payload could not be processed safely."""


class _PlainTextHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []

    def handle_data(self, data: str) -> None:
        stripped = data.strip()
        if stripped:
            self._parts.append(stripped)

    def text(self) -> str:
        return "\n".join(self._parts)


def _plain_text(html: str) -> str:
    parser = _PlainTextHTMLParser()
    # Greenhouse boards vary: some return HTML, others HTML-escaped HTML.
    parser.feed(unescape(html))
    parser.close()
    return parser.text()


def _remote_policy(title: str, location: str | None, description: str) -> RemotePolicy:
    text = " ".join(part for part in (title, location, description) if part)
    if re.search(r"\bhybrid\b", text, flags=re.IGNORECASE):
        return RemotePolicy.HYBRID
    if re.search(r"\b(?:on[ -]?site|office-based)\b", text, flags=re.IGNORECASE):
        return RemotePolicy.ONSITE
    if re.search(
        r"\b(?:remote|distributed|work from anywhere)\b",
        text,
        flags=re.IGNORECASE,
    ):
        return RemotePolicy.REMOTE
    return RemotePolicy.UNKNOWN


class GreenhouseSource:
    _BASE_URL = "https://boards-api.greenhouse.io/v1/boards"

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
        url = f"{self._BASE_URL}/{identifier}/jobs"

        try:
            response = await self._http_client.get(url, params={"content": "true"})
            response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise GreenhouseSourceError(
                f"Greenhouse request timed out for {company.name}"
            ) from exc
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                raise InvalidSourceConfigurationError(
                    f"Unknown Greenhouse board for {company.name}"
                ) from exc
            raise GreenhouseSourceError(
                f"Greenhouse returned HTTP {exc.response.status_code} "
                f"for {company.name}"
            ) from exc
        except httpx.RequestError as exc:
            raise GreenhouseSourceError(
                f"Greenhouse request failed for {company.name}: {exc}"
            ) from exc

        try:
            envelope = GreenhouseJobsEnvelopeDTO.model_validate_json(response.content)
        except ValidationError as exc:
            raise GreenhouseSourceError(
                f"Malformed Greenhouse response for {company.name}"
            ) from exc

        raw_count = len(envelope.jobs)
        malformed_count = 0
        vacancies: list[JobVacancy] = []
        for index, raw_job in enumerate(envelope.jobs):
            try:
                job = GreenhouseJobDTO.model_validate(raw_job)
                description = _plain_text(job.content)
                work_location = job.location.name if job.location else None
                vacancy = JobVacancy(
                    id=uuid4(),
                    company_id=company.id,
                    title=job.title,
                    description=description,
                    company_location=company.country,
                    work_location=work_location,
                    remote_policy=_remote_policy(
                        job.title,
                        work_location,
                        description,
                    ),
                    url=str(job.absolute_url),
                    source=VacancySource.GREENHOUSE,
                    source_job_id=str(job.id),
                    published_at=None,
                    first_seen_at=observed_at,
                    last_seen_at=observed_at,
                    status=VacancyStatus.ACTIVE,
                )
            except (ValidationError, ValueError) as exc:
                malformed_count += 1
                self._logger.debug(
                    "Skipping malformed Greenhouse job",
                    extra={
                        "company": company.name,
                        "source": VacancySource.GREENHOUSE.value,
                        "operation": "normalize",
                        "job_index": index,
                        "error_category": type(exc).__name__,
                    },
                )
                continue
            vacancies.append(vacancy)

        if malformed_count:
            self._logger.warning(
                "Skipped %d malformed Greenhouse jobs for %s out of %d records",
                malformed_count,
                company.name,
                raw_count,
                extra={
                    "company": company.name,
                    "source": VacancySource.GREENHOUSE.value,
                    "malformed_count": malformed_count,
                    "raw_count": raw_count,
                },
            )
        if raw_count and malformed_count == raw_count:
            raise GreenhouseSourceError(
                f"All {raw_count} Greenhouse jobs were malformed for {company.name}"
            )

        return tuple(vacancies)
