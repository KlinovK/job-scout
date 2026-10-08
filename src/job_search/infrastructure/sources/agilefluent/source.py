import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final
from uuid import uuid4

import httpx
from pydantic import ValidationError

from job_search.application.errors import (
    InvalidSourceConfigurationError,
    JobSourceError,
)
from job_search.domain.enums import RemotePolicy, VacancySource, VacancyStatus
from job_search.domain.models import Company, JobVacancy
from job_search.infrastructure.sources.agilefluent.dto import (
    AgileFluentJobDTO,
    AgileFluentJobsEnvelopeDTO,
)


class AgileFluentSourceError(JobSourceError):
    """An AgileFluent request or payload could not be processed safely."""


@dataclass(frozen=True, slots=True)
class AgileFluentSearchProfile:
    roles: tuple[str, ...]
    grades: tuple[str, ...]
    accepted_grades: tuple[str, ...] | None = None

    def accepts_grade(self, grade: str) -> bool:
        allowed = self.accepted_grades or self.grades
        return grade.casefold() in allowed


DEFAULT_SEARCH_PROFILES: Final = (
    AgileFluentSearchProfile(
        roles=("iOS Engineer", "Mobile Engineer"),
        grades=("middle", "senior", "lead"),
        accepted_grades=("unknown", "middle", "senior", "lead"),
    ),
    AgileFluentSearchProfile(
        roles=("Python Developer", "Backend Engineer", "Full Stack Engineer"),
        grades=("intern", "junior"),
    ),
    AgileFluentSearchProfile(
        roles=(
            "Project Manager",
            "Program Manager",
            "Delivery Manager",
            "Scrum Master",
            "Agile Coach",
            "Technical Program Manager (TPM)",
        ),
        grades=("junior", "middle", "senior", "lead"),
        accepted_grades=("unknown", "junior", "middle", "senior", "lead"),
    ),
)

_TARGET_GEOGRAPHY: Final = (
    {"workplaces": ["remote"]},
    {"countries": ["cyp", "geo", "nld", "pol", "are"]},
)
_COUNTRY_NAMES: Final = {
    "cyp": "Cyprus",
    "geo": "Georgia",
    "nld": "Netherlands",
    "pol": "Poland",
    "are": "UAE",
}


def _remote_policy(job: AgileFluentJobDTO) -> RemotePolicy:
    raw_values: Sequence[str | None] = job.formats or (job.format,)
    values = {value.casefold() for value in raw_values if value}
    if "hybrid" in values or ({"remote", "office"} <= values):
        return RemotePolicy.HYBRID
    if "remote" in values:
        return RemotePolicy.REMOTE
    if values & {"office", "on-site", "onsite"}:
        return RemotePolicy.ONSITE
    return RemotePolicy.UNKNOWN


def _published_at(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("AgileFluent createdAtIso must be timezone-aware")
    return value.astimezone(UTC)


def _work_location(job: AgileFluentJobDTO) -> str | None:
    country_codes = job.countries or ([job.country] if job.country else [])
    countries = [
        _COUNTRY_NAMES.get(code.casefold(), code.upper()) for code in country_codes
    ]
    parts = [part for part in (job.city, ", ".join(dict.fromkeys(countries))) if part]
    return ", ".join(parts) or None


def _description(job: AgileFluentJobDTO) -> str:
    parts = [job.description.strip()]
    parts.append(f"Role: {job.role}. Grade: {job.grade}.")
    if job.skills:
        parts.append(f"Skills: {', '.join(job.skills)}.")
    if job.industry:
        parts.append(f"Industry: {job.industry}.")
    if job.visa:
        parts.append("Visa sponsorship: available.")
    return "\n\n".join(part for part in parts if part)


class AgileFluentSource:
    _SEARCH_URL = "https://jobboard.agilefluent.ru/api/jobs/search"
    _PUBLIC_JOB_URL = "https://jobboard.agilefluent.ru/jobs"
    _IDENTIFIER = "jobboard.agilefluent.ru"

    def __init__(
        self,
        http_client: httpx.AsyncClient,
        *,
        profiles: Sequence[AgileFluentSearchProfile] = DEFAULT_SEARCH_PROFILES,
        page_size: int = 100,
        max_pages_per_profile: int = 20,
        logger: logging.Logger | None = None,
    ) -> None:
        if not profiles:
            raise ValueError("profiles must not be empty")
        if not 1 <= page_size <= 100:
            raise ValueError("page_size must be between 1 and 100")
        if max_pages_per_profile <= 0:
            raise ValueError("max_pages_per_profile must be greater than zero")
        self._http_client = http_client
        self._profiles = tuple(profiles)
        self._page_size = page_size
        self._max_pages_per_profile = max_pages_per_profile
        self._logger = logger or logging.getLogger(__name__)

    async def collect(
        self,
        company: Company,
        observed_at: datetime,
    ) -> Sequence[JobVacancy]:
        if company.ats_identifier != self._IDENTIFIER:
            raise InvalidSourceConfigurationError(
                f"Unknown AgileFluent source identifier for {company.name}"
            )

        vacancies: list[JobVacancy] = []
        seen_ids: set[str] = set()
        raw_count = 0
        malformed_count = 0
        for profile in self._profiles:
            page = 1
            while True:
                envelope = await self._fetch_page(company, profile, page)
                raw_count += len(envelope.data)
                malformed_count += self._normalize_page(
                    company,
                    observed_at,
                    profile,
                    envelope,
                    seen_ids,
                    vacancies,
                )
                if not envelope.hasMore:
                    break
                page += 1
                if page > self._max_pages_per_profile:
                    raise AgileFluentSourceError(
                        f"AgileFluent pagination exceeded safety limit for "
                        f"{company.name}"
                    )
        if malformed_count:
            self._logger.warning(
                "Skipped %d malformed AgileFluent jobs for %s out of %d records",
                malformed_count,
                company.name,
                raw_count,
                extra={
                    "company": company.name,
                    "source": VacancySource.AGILEFLUENT.value,
                    "malformed_count": malformed_count,
                    "raw_count": raw_count,
                },
            )
        if raw_count and malformed_count == raw_count:
            raise AgileFluentSourceError(
                f"All {raw_count} AgileFluent jobs were malformed for {company.name}"
            )
        return tuple(vacancies)

    async def _fetch_page(
        self,
        company: Company,
        profile: AgileFluentSearchProfile,
        page: int,
    ) -> AgileFluentJobsEnvelopeDTO:
        payload = {
            "filters": {
                "roles": list(profile.roles),
                "grades": list(profile.grades),
                "since": "24h",
                "countries_workplaces": list(_TARGET_GEOGRAPHY),
            },
            "pagination": {"limit": self._page_size, "page": page},
        }
        try:
            response = await self._http_client.post(self._SEARCH_URL, json=payload)
            response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise AgileFluentSourceError(
                f"AgileFluent request timed out for {company.name}"
            ) from exc
        except httpx.HTTPStatusError as exc:
            raise AgileFluentSourceError(
                f"AgileFluent returned HTTP {exc.response.status_code} "
                f"for {company.name}"
            ) from exc
        except httpx.RequestError as exc:
            raise AgileFluentSourceError(
                f"AgileFluent request failed for {company.name}: {exc}"
            ) from exc

        try:
            return AgileFluentJobsEnvelopeDTO.model_validate_json(response.content)
        except ValidationError as exc:
            raise AgileFluentSourceError(
                f"Malformed AgileFluent response for {company.name}"
            ) from exc

    def _normalize_page(
        self,
        company: Company,
        observed_at: datetime,
        profile: AgileFluentSearchProfile,
        envelope: AgileFluentJobsEnvelopeDTO,
        seen_ids: set[str],
        vacancies: list[JobVacancy],
    ) -> int:
        malformed_count = 0
        for index, raw_job in enumerate(envelope.data):
            try:
                job = AgileFluentJobDTO.model_validate(raw_job)
                published_at = _published_at(job.createdAtIso)
            except (ValidationError, ValueError) as exc:
                malformed_count += 1
                self._logger.debug(
                    "Skipping malformed AgileFluent job",
                    extra={
                        "company": company.name,
                        "source": VacancySource.AGILEFLUENT.value,
                        "job_index": index,
                        "error_category": type(exc).__name__,
                    },
                )
                continue
            # The public API currently applies roles but may ignore grades.
            # Enforce the requested seniority locally before persistence.
            if not profile.accepts_grade(job.grade):
                continue
            if job.id in seen_ids:
                continue
            try:
                vacancy = JobVacancy(
                    id=uuid4(),
                    company_id=company.id,
                    title=job.title,
                    description=_description(job),
                    company_location=None,
                    work_location=_work_location(job),
                    remote_policy=_remote_policy(job),
                    url=f"{self._PUBLIC_JOB_URL}/{job.id}",
                    source=VacancySource.AGILEFLUENT,
                    source_job_id=job.id,
                    published_at=published_at,
                    first_seen_at=observed_at,
                    last_seen_at=observed_at,
                    status=VacancyStatus.ACTIVE,
                    employer_name=job.companyName,
                )
            except ValueError as exc:
                malformed_count += 1
                self._logger.debug(
                    "Skipping malformed AgileFluent job",
                    extra={
                        "company": company.name,
                        "source": VacancySource.AGILEFLUENT.value,
                        "job_index": index,
                        "error_category": type(exc).__name__,
                    },
                )
                continue
            seen_ids.add(job.id)
            vacancies.append(vacancy)
        return malformed_count
