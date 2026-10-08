import asyncio
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime

from job_search.application.errors import InvalidSourceConfigurationError
from job_search.application.models import (
    CandidateVacancy,
    ClassificationSummary,
    CollectionFailure,
    CollectionSummary,
    CompanySeedResult,
    ProviderCollectionSummary,
    SourceHealthUpdate,
)
from job_search.application.ports import (
    ClassificationRepository,
    CompanyRepository,
    JobSourceProvider,
    VacancyClassifier,
    VacancyRepository,
)
from job_search.domain.classification import ClassificationDecision
from job_search.domain.enums import ATSType, SourceHealthStatus
from job_search.domain.models import Company, utc_now

Clock = Callable[[], datetime]


@dataclass(frozen=True, slots=True)
class _CompanyCollectionOutcome:
    ats_type: ATSType
    status: SourceHealthStatus
    jobs_fetched: int = 0
    new_jobs: int = 0
    updated_jobs: int = 0
    observations_created: int = 0
    cross_source_duplicates_collapsed: int = 0
    failure: CollectionFailure | None = None


@dataclass(slots=True)
class _ProviderAccumulator:
    checked: int = 0
    healthy: int = 0
    empty: int = 0
    failed: int = 0
    invalid_configuration: int = 0
    jobs_fetched: int = 0
    new_jobs: int = 0
    updated_jobs: int = 0
    observations_created: int = 0
    cross_source_duplicates_collapsed: int = 0


class CollectJobsService:
    def __init__(
        self,
        company_repository: CompanyRepository,
        vacancy_repository: VacancyRepository,
        source_provider: JobSourceProvider,
        *,
        clock: Clock = utc_now,
        logger: logging.Logger | None = None,
        max_concurrency: int = 8,
    ) -> None:
        if max_concurrency <= 0:
            raise ValueError("max_concurrency must be greater than zero")
        self._companies = company_repository
        self._vacancies = vacancy_repository
        self._sources = source_provider
        self._clock = clock
        self._logger = logger or logging.getLogger(__name__)
        self._max_concurrency = max_concurrency
        self._persistence_lock = asyncio.Lock()

    async def collect(
        self,
        sources: Sequence[ATSType] | None = None,
        *,
        include_disabled: bool = False,
    ) -> CollectionSummary:
        companies = (
            await self._companies.list_for_sources(
                sources,
                include_disabled=include_disabled,
            )
            if sources is not None
            else await self._companies.list_enabled()
        )
        outcomes: list[_CompanyCollectionOutcome | None] = [None] * len(companies)
        semaphore = asyncio.Semaphore(self._max_concurrency)

        async def collect_one(index: int, company: Company) -> None:
            async with semaphore:
                outcomes[index] = await self._collect_company(company)

        async with asyncio.TaskGroup() as group:
            for index, company in enumerate(companies):
                group.create_task(collect_one(index, company))

        completed = tuple(outcome for outcome in outcomes if outcome is not None)
        accumulators: dict[ATSType, _ProviderAccumulator] = {}
        failures: list[CollectionFailure] = []
        for outcome in completed:
            accumulator = accumulators.setdefault(
                outcome.ats_type,
                _ProviderAccumulator(),
            )
            accumulator.checked += 1
            accumulator.jobs_fetched += outcome.jobs_fetched
            accumulator.new_jobs += outcome.new_jobs
            accumulator.updated_jobs += outcome.updated_jobs
            accumulator.observations_created += outcome.observations_created
            accumulator.cross_source_duplicates_collapsed += (
                outcome.cross_source_duplicates_collapsed
            )
            if outcome.status is SourceHealthStatus.HEALTHY:
                accumulator.healthy += 1
            elif outcome.status is SourceHealthStatus.EMPTY:
                accumulator.empty += 1
            elif outcome.status is SourceHealthStatus.INVALID_CONFIGURATION:
                accumulator.invalid_configuration += 1
            else:
                accumulator.failed += 1
            if outcome.failure is not None:
                failures.append(outcome.failure)

        provider_summaries = tuple(
            ProviderCollectionSummary(
                ats_type=ats_type,
                checked=value.checked,
                healthy=value.healthy,
                empty=value.empty,
                failed=value.failed,
                invalid_configuration=value.invalid_configuration,
                jobs_fetched=value.jobs_fetched,
                new_jobs=value.new_jobs,
                updated_jobs=value.updated_jobs,
                observations_created=value.observations_created,
                cross_source_duplicates_collapsed=(
                    value.cross_source_duplicates_collapsed
                ),
            )
            for ats_type, value in sorted(
                accumulators.items(),
                key=lambda item: item[0].value,
            )
        )
        return CollectionSummary(
            companies_checked=len(companies),
            jobs_fetched=sum(item.jobs_fetched for item in completed),
            new_jobs=sum(item.new_jobs for item in completed),
            updated_jobs=sum(item.updated_jobs for item in completed),
            failures=tuple(failures),
            provider_summaries=provider_summaries,
            healthy=sum(
                item.status is SourceHealthStatus.HEALTHY for item in completed
            ),
            empty=sum(item.status is SourceHealthStatus.EMPTY for item in completed),
            invalid_configuration=sum(
                item.status is SourceHealthStatus.INVALID_CONFIGURATION
                for item in completed
            ),
        )

    async def _collect_company(self, company: Company) -> _CompanyCollectionOutcome:
        checked_at = self._clock()
        source = self._sources.get_source(company.ats_type)
        if source is None:
            failure = CollectionFailure(
                company_id=company.id,
                company_name=company.name,
                ats_type=company.ats_type,
                error_category="unsupported_source",
                message=f"No source adapter for {company.ats_type.value}",
            )
            await self._record_failure_health(
                company,
                checked_at,
                SourceHealthStatus.INVALID_CONFIGURATION,
                failure.error_category,
            )
            self._logger.error(
                "Unsupported job source",
                extra={"company": company.name, "source": company.ats_type.value},
            )
            return _CompanyCollectionOutcome(
                ats_type=company.ats_type,
                status=SourceHealthStatus.INVALID_CONFIGURATION,
                failure=failure,
            )

        try:
            vacancies = await source.collect(company, checked_at)
            status = (
                SourceHealthStatus.HEALTHY if vacancies else SourceHealthStatus.EMPTY
            )
            # SQLite supports concurrent readers but only one writer. Keep HTTP
            # collection concurrent and serialize the short persistence phase.
            async with self._persistence_lock:
                upserted = await self._vacancies.upsert_many(vacancies)
                await self._companies.update_health(
                    company.id,
                    SourceHealthUpdate(
                        checked_at=checked_at,
                        status=status,
                        job_count=len(vacancies),
                        error_category=None,
                    ),
                )
        except InvalidSourceConfigurationError as exc:
            return await self._failure_outcome(
                company,
                checked_at,
                SourceHealthStatus.INVALID_CONFIGURATION,
                exc,
            )
        except Exception as exc:
            self._logger.exception(
                "Company collection failed",
                extra={"company": company.name, "source": company.ats_type.value},
            )
            return await self._failure_outcome(
                company,
                checked_at,
                SourceHealthStatus.FAILED,
                exc,
            )

        self._logger.info(
            "Company collection completed",
            extra={
                "company": company.name,
                "source": company.ats_type.value,
                "result": status.value,
                "jobs_fetched": len(vacancies),
            },
        )
        return _CompanyCollectionOutcome(
            ats_type=company.ats_type,
            status=status,
            jobs_fetched=len(vacancies),
            new_jobs=upserted.created,
            updated_jobs=upserted.updated,
            observations_created=upserted.observations_created,
            cross_source_duplicates_collapsed=(
                upserted.cross_source_duplicates_collapsed
            ),
        )

    async def _failure_outcome(
        self,
        company: Company,
        checked_at: datetime,
        status: SourceHealthStatus,
        error: Exception,
    ) -> _CompanyCollectionOutcome:
        category = type(error).__name__
        await self._record_failure_health(company, checked_at, status, category)
        failure = CollectionFailure(
            company_id=company.id,
            company_name=company.name,
            ats_type=company.ats_type,
            error_category=category,
            message=str(error),
        )
        return _CompanyCollectionOutcome(
            ats_type=company.ats_type,
            status=status,
            failure=failure,
        )

    async def _record_failure_health(
        self,
        company: Company,
        checked_at: datetime,
        status: SourceHealthStatus,
        error_category: str,
    ) -> None:
        try:
            async with self._persistence_lock:
                await self._companies.update_health(
                    company.id,
                    SourceHealthUpdate(
                        checked_at=checked_at,
                        status=status,
                        job_count=None,
                        error_category=error_category,
                    ),
                )
        except Exception:
            self._logger.exception(
                "Source health update failed",
                extra={"company": company.name, "source": company.ats_type.value},
            )


class SeedCompaniesService:
    def __init__(self, company_repository: CompanyRepository) -> None:
        self._companies = company_repository

    async def seed(self, companies: Sequence[Company]) -> CompanySeedResult:
        created = 0
        updated = 0
        for company in companies:
            if await self._companies.upsert_by_ats_identity(company):
                created += 1
            else:
                updated += 1
        return CompanySeedResult(created=created, updated=updated)


class ClassifyVacanciesService:
    def __init__(
        self,
        vacancy_repository: VacancyRepository,
        classification_repository: ClassificationRepository,
        classifier: VacancyClassifier,
        *,
        clock: Clock = utc_now,
    ) -> None:
        self._vacancies = vacancy_repository
        self._classifications = classification_repository
        self._classifier = classifier
        self._clock = clock

    async def classify(self) -> ClassificationSummary:
        vacancies = await self._vacancies.list_active()
        classified_at = self._clock()
        classifications = tuple(
            self._classifier.classify(vacancy, classified_at) for vacancy in vacancies
        )
        persisted = await self._classifications.upsert_many(classifications)
        return ClassificationSummary(
            processed=len(classifications),
            matches=sum(
                item.decision is ClassificationDecision.MATCH
                for item in classifications
            ),
            possible_matches=sum(
                item.decision is ClassificationDecision.POSSIBLE_MATCH
                for item in classifications
            ),
            rejected=sum(
                item.decision is ClassificationDecision.REJECT
                for item in classifications
            ),
            created=persisted.created,
            updated=persisted.updated,
            classifier_version=self._classifier.version,
        )


class ListCandidateVacanciesService:
    def __init__(
        self,
        company_repository: CompanyRepository,
        vacancy_repository: VacancyRepository,
        classification_repository: ClassificationRepository,
    ) -> None:
        self._companies = company_repository
        self._vacancies = vacancy_repository
        self._classifications = classification_repository

    async def list_candidates(
        self,
        classifier_version: str,
        *,
        limit: int | None = None,
    ) -> tuple[CandidateVacancy, ...]:
        classifications = await self._classifications.list_by_decisions(
            classifier_version,
            (
                ClassificationDecision.MATCH,
                ClassificationDecision.POSSIBLE_MATCH,
            ),
            limit=limit,
        )
        candidates: list[CandidateVacancy] = []
        for classification in classifications:
            vacancy = await self._vacancies.get(classification.vacancy_id)
            if vacancy is None:
                raise LookupError(
                    f"Unknown classified vacancy: {classification.vacancy_id}"
                )
            company = await self._companies.get(vacancy.company_id)
            if company is None:
                raise LookupError(f"Unknown vacancy company: {vacancy.company_id}")
            candidates.append(
                CandidateVacancy(
                    company=company,
                    vacancy=vacancy,
                    classification=classification,
                    observations=tuple(
                        await self._vacancies.list_observations(vacancy.id)
                    ),
                )
            )
        return tuple(candidates)
