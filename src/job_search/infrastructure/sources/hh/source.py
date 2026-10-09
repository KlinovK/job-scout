import asyncio
import html
import logging
import re
from collections.abc import Sequence
from datetime import datetime, timedelta
from uuid import NAMESPACE_URL, uuid5

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
from job_search.domain.canonicalization import normalize_canonical_url
from job_search.domain.enums import RemotePolicy, VacancySource, VacancyStatus
from job_search.domain.models import Company, JobVacancy
from job_search.infrastructure.http import RetryPolicy, Sleep, get_with_retry
from job_search.infrastructure.sources.hh.dto import (
    HHSalaryDTO,
    HHSearchEnvelopeDTO,
    HHVacancyDTO,
)

_SEARCH_TERMS = (
    "iOS",
    "Swift",
    '"Mobile Engineer"',
    '"Mobile Developer"',
)
_TAG_PATTERN = re.compile(r"<[^>]+>")


class HHSourceError(JobSourceError):
    """The official hh.ru API could not be processed safely."""


def _plain_text(value: str) -> str:
    return " ".join(html.unescape(_TAG_PATTERN.sub(" ", value)).split())


def _remote_policy(vacancy: HHVacancyDTO) -> RemotePolicy:
    values = (
        " ".join(item.id or "" for item in vacancy.work_format)
        + " "
        + " ".join(item.name for item in vacancy.work_format)
    )
    if vacancy.schedule is not None:
        values += f" {vacancy.schedule.id or ''} {vacancy.schedule.name}"
    normalized = values.casefold()
    if any(token in normalized for token in ("remote", "удален", "дистанц")):
        return RemotePolicy.REMOTE
    if any(token in normalized for token in ("hybrid", "гибрид")):
        return RemotePolicy.HYBRID
    if any(token in normalized for token in ("office", "офис", "on-site", "onsite")):
        return RemotePolicy.ONSITE
    return RemotePolicy.UNKNOWN


def _salary(value: HHSalaryDTO | None) -> str | None:
    if value is None:
        return None
    components: list[str] = []
    if value.from_ is not None:
        components.append(f"from {value.from_:g}")
    if value.to is not None:
        components.append(f"to {value.to:g}")
    if not components:
        return None
    if value.currency:
        components.append(value.currency)
    if value.gross is not None:
        components.append("gross" if value.gross else "net")
    return " ".join(components)


class HHSource:
    api_base_url = "https://api.hh.ru"

    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        oauth_token: str | None,
        max_age_hours: int = 72,
        max_pages_per_query: int = 5,
        detail_concurrency: int = 4,
        max_retries: int = 2,
        sleep: Sleep = asyncio.sleep,
        logger: logging.Logger | None = None,
    ) -> None:
        if max_age_hours <= 0:
            raise ValueError("max_age_hours must be greater than zero")
        if max_pages_per_query <= 0:
            raise ValueError("max_pages_per_query must be greater than zero")
        if detail_concurrency <= 0:
            raise ValueError("detail_concurrency must be greater than zero")
        if max_retries < 0:
            raise ValueError("max_retries must not be negative")
        self._client = client
        self._oauth_token = oauth_token.strip() if oauth_token else None
        self._max_age_hours = max_age_hours
        self._max_pages_per_query = max_pages_per_query
        self._detail_concurrency = detail_concurrency
        self._retry_policy = RetryPolicy(max_retries=max_retries)
        self._sleep = sleep
        self._logger = logger or logging.getLogger(__name__)

    async def collect(
        self,
        company: Company,
        observed_at: datetime,
    ) -> SourceCollectionResult:
        if company.ats_identifier != "api.hh.ru":
            raise InvalidSourceConfigurationError(
                f"Unknown hh.ru source identifier for {company.name}"
            )
        if self._oauth_token is None:
            raise InvalidSourceConfigurationError(
                "JOB_SEARCH_HH_API_TOKEN is required because anonymous hh.ru "
                "vacancy search is CAPTCHA-limited"
            )

        published_from = observed_at - timedelta(hours=self._max_age_hours)
        vacancy_ids, pagination_exhausted = await self._search_ids(published_from)
        details, malformed_count = await self._fetch_details(vacancy_ids)
        raw_count = len(vacancy_ids)
        vacancies: list[JobVacancy] = []
        for detail in details:
            if detail.alternate_url is None:
                self._logger.warning(
                    "Skipping hh.ru vacancy without public URL",
                    extra={"vacancy_id": detail.id},
                )
                continue
            published_at = detail.published_at.astimezone(observed_at.tzinfo)
            if published_at < published_from:
                continue
            try:
                canonical_url = normalize_canonical_url(detail.alternate_url)
                vacancy = JobVacancy(
                    id=uuid5(NAMESPACE_URL, f"hh:{detail.id}"),
                    company_id=company.id,
                    title=detail.name,
                    description=_plain_text(detail.description),
                    employer_name=detail.employer.name,
                    company_location=None,
                    work_location=detail.area.name if detail.area else None,
                    remote_policy=_remote_policy(detail),
                    url=detail.alternate_url,
                    canonical_url=canonical_url,
                    source=VacancySource.HH,
                    original_source=VacancySource.HH,
                    source_job_id=detail.id,
                    published_at=published_at,
                    first_seen_at=observed_at,
                    last_seen_at=observed_at,
                    status=(
                        VacancyStatus.CLOSED
                        if detail.archived
                        else VacancyStatus.ACTIVE
                    ),
                    salary=_salary(detail.salary),
                    employment=(detail.employment.name if detail.employment else None),
                    experience=(detail.experience.name if detail.experience else None),
                )
            except ValueError as exc:
                malformed_count += 1
                self._logger.debug(
                    "Skipping malformed hh.ru vacancy",
                    extra={
                        "company": company.name,
                        "source": VacancySource.HH.value,
                        "vacancy_id": detail.id,
                        "error_category": type(exc).__name__,
                    },
                )
                continue
            vacancies.append(vacancy)

        if malformed_count:
            self._logger.warning(
                "Skipped %d malformed hh.ru vacancies for %s out of %d records",
                malformed_count,
                company.name,
                raw_count,
                extra={
                    "company": company.name,
                    "source": VacancySource.HH.value,
                    "malformed_count": malformed_count,
                    "raw_count": raw_count,
                },
            )
        if raw_count and malformed_count == raw_count:
            raise HHSourceError(
                f"All {raw_count} hh.ru vacancy responses were malformed "
                f"for {company.name}"
            )
        return SourceCollectionResult(
            vacancies=tuple(vacancies),
            coverage=CollectionCoverage.ROLLING_WINDOW,
            complete=pagination_exhausted and malformed_count == 0,
            raw_count=raw_count,
            malformed_count=malformed_count,
            pagination_exhausted=pagination_exhausted,
        )

    async def _search_ids(
        self,
        published_from: datetime,
    ) -> tuple[tuple[str, ...], bool]:
        found: dict[str, None] = {}
        pagination_exhausted = True
        for term in _SEARCH_TERMS:
            for page in range(self._max_pages_per_query):
                payload = await self._get_json(
                    "/vacancies",
                    params={
                        "text": term,
                        "search_field": "name",
                        "date_from": published_from.isoformat(),
                        "order_by": "publication_time",
                        "per_page": "100",
                        "page": str(page),
                    },
                )
                try:
                    envelope = HHSearchEnvelopeDTO.model_validate(payload)
                except ValidationError as exc:
                    raise HHSourceError("Malformed hh.ru search response") from exc
                for item in envelope.items:
                    found.setdefault(item.id, None)
                if page + 1 >= envelope.pages or not envelope.items:
                    break
            else:
                pagination_exhausted = False
                self._logger.warning(
                    "hh.ru pagination reached safety limit",
                    extra={"term": term, "max_pages": self._max_pages_per_query},
                )
        return tuple(found), pagination_exhausted

    async def _fetch_details(
        self,
        vacancy_ids: Sequence[str],
    ) -> tuple[tuple[HHVacancyDTO, ...], int]:
        results: list[HHVacancyDTO | None] = [None] * len(vacancy_ids)
        errors: list[Exception] = []
        malformed_count = 0
        semaphore = asyncio.Semaphore(self._detail_concurrency)

        async def fetch_one(index: int, vacancy_id: str) -> None:
            nonlocal malformed_count
            async with semaphore:
                try:
                    payload = await self._get_json(f"/vacancies/{vacancy_id}")
                    results[index] = HHVacancyDTO.model_validate(payload)
                except ValidationError as exc:
                    malformed_count += 1
                    self._logger.debug(
                        "Skipping malformed hh.ru vacancy response",
                        extra={
                            "source": VacancySource.HH.value,
                            "vacancy_id": vacancy_id,
                            "error_category": type(exc).__name__,
                        },
                    )
                except Exception as exc:
                    errors.append(exc)

        async with asyncio.TaskGroup() as group:
            for index, vacancy_id in enumerate(vacancy_ids):
                group.create_task(fetch_one(index, vacancy_id))
        if errors:
            raise errors[0]
        return tuple(item for item in results if item is not None), malformed_count

    async def _get_json(
        self,
        path: str,
        *,
        params: dict[str, str] | None = None,
    ) -> object:
        headers = {"Authorization": f"Bearer {self._oauth_token}"}
        try:
            response = await get_with_retry(
                self._client,
                f"{self.api_base_url}{path}",
                provider=VacancySource.HH.value,
                params=params,
                headers=headers,
                policy=self._retry_policy,
                sleep=self._sleep,
                logger=self._logger,
            )
        except httpx.TimeoutException as exc:
            raise HHSourceError("hh.ru request timed out") from exc
        except httpx.RequestError as exc:
            raise HHSourceError(f"hh.ru request failed: {exc}") from exc

        if response.status_code == 401:
            raise InvalidSourceConfigurationError(
                "hh.ru rejected JOB_SEARCH_HH_API_TOKEN"
            )
        if response.status_code == 429:
            raise HHSourceError("hh.ru rate limit exceeded (HTTP 429)")
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise HHSourceError(f"hh.ru returned HTTP {response.status_code}") from exc
        try:
            return response.json()
        except ValueError as exc:
            raise HHSourceError("Malformed hh.ru JSON response") from exc
