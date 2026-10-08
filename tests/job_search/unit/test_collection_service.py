import asyncio
from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

import pytest

from job_search.application.errors import (
    InvalidSourceConfigurationError,
    JobSourceError,
)
from job_search.application.models import SourceHealthUpdate, VacancyUpsertResult
from job_search.application.services import CollectJobsService
from job_search.domain.enums import ATSType, SourceHealthStatus, VacancySource
from job_search.domain.models import Company, JobVacancy
from tests.job_search.factories import NOW, make_company, make_vacancy


class FakeCompanyRepository:
    def __init__(self, companies: Sequence[Company]) -> None:
        self.companies = tuple(companies)
        self.checked: list[tuple[UUID, datetime]] = []
        self.health_updates: list[tuple[UUID, SourceHealthUpdate]] = []

    async def add(self, company: Company) -> None:
        raise NotImplementedError

    async def get(self, company_id: UUID) -> Company | None:
        return next((item for item in self.companies if item.id == company_id), None)

    async def list_enabled(self) -> Sequence[Company]:
        return tuple(item for item in self.companies if item.enabled)

    async def list_all(self) -> Sequence[Company]:
        return self.companies

    async def list_for_sources(
        self,
        sources: Sequence[ATSType],
        *,
        include_disabled: bool = False,
    ) -> Sequence[Company]:
        return tuple(
            item
            for item in self.companies
            if item.ats_type in sources and (include_disabled or item.enabled)
        )

    async def upsert_by_ats_identity(self, company: Company) -> bool:
        raise NotImplementedError

    async def mark_checked(self, company_id: UUID, checked_at: datetime) -> None:
        self.checked.append((company_id, checked_at))

    async def update_health(
        self,
        company_id: UUID,
        update: SourceHealthUpdate,
    ) -> None:
        self.checked.append((company_id, update.checked_at))
        self.health_updates.append((company_id, update))


class FakeVacancyRepository:
    def __init__(self) -> None:
        self.items: dict[tuple[VacancySource, str], JobVacancy] = {}

    async def get(self, vacancy_id: UUID) -> JobVacancy | None:
        return next(
            (item for item in self.items.values() if item.id == vacancy_id), None
        )

    async def get_by_source_identity(
        self,
        source: VacancySource,
        source_job_id: str,
    ) -> JobVacancy | None:
        return self.items.get((source, source_job_id))

    async def upsert_many(
        self,
        vacancies: Sequence[JobVacancy],
    ) -> VacancyUpsertResult:
        created = 0
        updated = 0
        for vacancy in vacancies:
            key = (vacancy.source, vacancy.source_job_id)
            if key in self.items:
                updated += 1
            else:
                created += 1
            self.items[key] = vacancy
        return VacancyUpsertResult(created=created, updated=updated)

    async def list_active(self) -> Sequence[JobVacancy]:
        return tuple(self.items.values())


class FakeSource:
    def __init__(self, jobs_by_company: dict[UUID, Sequence[JobVacancy]]) -> None:
        self.jobs_by_company = jobs_by_company

    async def collect(
        self,
        company: Company,
        observed_at: datetime,
    ) -> Sequence[JobVacancy]:
        del observed_at
        return self.jobs_by_company.get(company.id, ())


class FailingSource:
    async def collect(
        self,
        company: Company,
        observed_at: datetime,
    ) -> Sequence[JobVacancy]:
        del company, observed_at
        raise RuntimeError("source unavailable")


class InvalidSource:
    async def collect(
        self,
        company: Company,
        observed_at: datetime,
    ) -> Sequence[JobVacancy]:
        del company, observed_at
        raise InvalidSourceConfigurationError("unknown board")


class AllMalformedSource:
    async def collect(
        self,
        company: Company,
        observed_at: datetime,
    ) -> Sequence[JobVacancy]:
        del company, observed_at
        raise JobSourceError("All 2 provider records were malformed")


class ConcurrencyTrackingSource:
    def __init__(self, release_at: int) -> None:
        self.release_at = release_at
        self.active = 0
        self.peak = 0
        self.release = asyncio.Event()

    async def collect(
        self,
        company: Company,
        observed_at: datetime,
    ) -> Sequence[JobVacancy]:
        del company, observed_at
        self.active += 1
        self.peak = max(self.peak, self.active)
        if self.active >= self.release_at:
            self.release.set()
        await self.release.wait()
        self.active -= 1
        return ()


class FakeProvider:
    def __init__(self, source_by_type) -> None:
        self.source_by_type = source_by_type

    def get_source(self, ats_type: ATSType):
        return self.source_by_type.get(ats_type)


@pytest.mark.asyncio
async def test_collects_one_company_and_marks_it_checked() -> None:
    company = make_company()
    companies = FakeCompanyRepository([company])
    vacancies = FakeVacancyRepository()
    source = FakeSource({company.id: [make_vacancy(company.id)]})
    service = CollectJobsService(
        companies,
        vacancies,
        FakeProvider({ATSType.GREENHOUSE: source}),
        clock=lambda: NOW,
    )

    summary = await service.collect()

    assert summary.companies_checked == 1
    assert summary.jobs_fetched == 1
    assert summary.new_jobs == 1
    assert summary.updated_jobs == 0
    assert summary.failure_count == 0
    assert companies.checked == [(company.id, NOW)]
    assert companies.health_updates[0][1].status is SourceHealthStatus.HEALTHY
    assert companies.health_updates[0][1].job_count == 1


@pytest.mark.asyncio
async def test_repeated_collection_updates_instead_of_duplicating() -> None:
    company = make_company()
    companies = FakeCompanyRepository([company])
    vacancies = FakeVacancyRepository()
    source = FakeSource({company.id: [make_vacancy(company.id)]})
    service = CollectJobsService(
        companies,
        vacancies,
        FakeProvider({ATSType.GREENHOUSE: source}),
        clock=lambda: NOW,
    )

    first = await service.collect()
    second = await service.collect()

    assert (first.new_jobs, first.updated_jobs) == (1, 0)
    assert (second.new_jobs, second.updated_jobs) == (0, 1)
    assert len(vacancies.items) == 1


@pytest.mark.asyncio
async def test_one_company_failure_does_not_stop_another() -> None:
    failed = make_company(name="Failed", ats_type=ATSType.GREENHOUSE)
    working = make_company(
        name="Working",
        ats_type=ATSType.CUSTOM,
        ats_identifier="working",
    )
    companies = FakeCompanyRepository([failed, working])
    vacancies = FakeVacancyRepository()
    provider = FakeProvider(
        {
            ATSType.GREENHOUSE: FailingSource(),
            ATSType.CUSTOM: FakeSource(
                {working.id: [make_vacancy(working.id, source_job_id="working-1")]}
            ),
        }
    )

    summary = await CollectJobsService(
        companies,
        vacancies,
        provider,
        clock=lambda: NOW,
    ).collect()

    assert summary.companies_checked == 2
    assert summary.jobs_fetched == 1
    assert summary.new_jobs == 1
    assert summary.failure_count == 1
    assert summary.failures[0].company_name == "Failed"
    assert set(companies.checked) == {(failed.id, NOW), (working.id, NOW)}


@pytest.mark.asyncio
async def test_unsupported_source_is_reported() -> None:
    company = make_company(ats_type=ATSType.LEVER)
    companies = FakeCompanyRepository([company])
    summary = await CollectJobsService(
        companies,
        FakeVacancyRepository(),
        FakeProvider({}),
        clock=lambda: NOW,
    ).collect()

    assert summary.failure_count == 1
    assert summary.failures[0].error_category == "unsupported_source"
    assert summary.invalid_configuration == 1
    assert companies.health_updates[0][1].status is (
        SourceHealthStatus.INVALID_CONFIGURATION
    )


@pytest.mark.asyncio
async def test_empty_and_invalid_sources_have_distinct_health() -> None:
    empty = make_company(name="Empty", ats_type=ATSType.LEVER, ats_identifier="empty")
    invalid = make_company(
        name="Invalid",
        ats_type=ATSType.ASHBY,
        ats_identifier="invalid",
    )
    companies = FakeCompanyRepository([empty, invalid])

    summary = await CollectJobsService(
        companies,
        FakeVacancyRepository(),
        FakeProvider(
            {
                ATSType.LEVER: FakeSource({}),
                ATSType.ASHBY: InvalidSource(),
            }
        ),
        clock=lambda: NOW,
    ).collect()

    updates = {company_id: update for company_id, update in companies.health_updates}
    assert summary.empty == 1
    assert summary.invalid_configuration == 1
    assert updates[empty.id].status is SourceHealthStatus.EMPTY
    assert updates[empty.id].job_count == 0
    assert updates[invalid.id].status is SourceHealthStatus.INVALID_CONFIGURATION
    assert updates[invalid.id].job_count is None


@pytest.mark.asyncio
async def test_all_malformed_provider_data_is_failed_not_empty() -> None:
    company = make_company()
    companies = FakeCompanyRepository([company])

    summary = await CollectJobsService(
        companies,
        FakeVacancyRepository(),
        FakeProvider({ATSType.GREENHOUSE: AllMalformedSource()}),
        clock=lambda: NOW,
    ).collect()

    update = companies.health_updates[0][1]
    assert summary.failure_count == 1
    assert summary.empty == 0
    assert update.status is SourceHealthStatus.FAILED
    assert update.job_count is None
    assert update.error_category == "JobSourceError"


@pytest.mark.asyncio
async def test_provider_summary_keeps_provider_counts_separate() -> None:
    greenhouse = make_company(name="Greenhouse", ats_identifier="greenhouse")
    lever = make_company(
        name="Lever",
        ats_type=ATSType.LEVER,
        ats_identifier="lever",
    )
    summary = await CollectJobsService(
        FakeCompanyRepository([greenhouse, lever]),
        FakeVacancyRepository(),
        FakeProvider(
            {
                ATSType.GREENHOUSE: FakeSource(
                    {greenhouse.id: [make_vacancy(greenhouse.id)]}
                ),
                ATSType.LEVER: FakeSource({}),
            }
        ),
        clock=lambda: NOW,
    ).collect()

    providers = {item.ats_type: item for item in summary.provider_summaries}
    assert providers[ATSType.GREENHOUSE].healthy == 1
    assert providers[ATSType.GREENHOUSE].jobs_fetched == 1
    assert providers[ATSType.LEVER].empty == 1
    assert providers[ATSType.LEVER].jobs_fetched == 0


@pytest.mark.asyncio
async def test_explicit_external_collection_can_include_disabled_source() -> None:
    hh = make_company(
        name="HeadHunter",
        ats_type=ATSType.HH,
        ats_identifier="api.hh.ru",
        enabled=False,
    )
    source = FakeSource({hh.id: [make_vacancy(hh.id)]})

    summary = await CollectJobsService(
        FakeCompanyRepository([hh]),
        FakeVacancyRepository(),
        FakeProvider({ATSType.HH: source}),
        clock=lambda: NOW,
    ).collect((ATSType.HH,), include_disabled=True)

    assert summary.companies_checked == 1
    assert summary.jobs_fetched == 1


@pytest.mark.asyncio
async def test_collection_concurrency_is_bounded() -> None:
    companies = [
        make_company(name=f"Company {index}", ats_identifier=f"company-{index}")
        for index in range(5)
    ]
    source = ConcurrencyTrackingSource(release_at=2)

    summary = await CollectJobsService(
        FakeCompanyRepository(companies),
        FakeVacancyRepository(),
        FakeProvider({ATSType.GREENHOUSE: source}),
        clock=lambda: NOW,
        max_concurrency=2,
    ).collect()

    assert summary.companies_checked == 5
    assert summary.empty == 5
    assert source.peak == 2


def test_non_positive_collection_concurrency_is_rejected() -> None:
    with pytest.raises(ValueError, match="max_concurrency"):
        CollectJobsService(
            FakeCompanyRepository([]),
            FakeVacancyRepository(),
            FakeProvider({}),
            max_concurrency=0,
        )
