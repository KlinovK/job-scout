from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from job_search.application.models import SourceHealthUpdate
from job_search.application.services import SeedCompaniesService
from job_search.domain.enums import ATSType, SourceHealthStatus, VacancySource
from job_search.infrastructure.persistence.sqlalchemy.models import JobVacancyRecord
from job_search.infrastructure.persistence.sqlalchemy.repositories import (
    SQLAlchemyCompanyRepository,
    SQLAlchemyVacancyRepository,
)
from job_search.infrastructure.seed import development_companies
from tests.job_search.factories import LATER, NOW, make_company, make_vacancy


@pytest.mark.asyncio
async def test_sqlite_foreign_keys_are_enabled(database) -> None:
    engine, _ = database

    async with engine.connect() as connection:
        enabled = await connection.scalar(text("PRAGMA foreign_keys"))

    assert enabled == 1


@pytest.mark.asyncio
async def test_company_create_read_and_enabled_filtering(database) -> None:
    _, session_factory = database
    repository = SQLAlchemyCompanyRepository(session_factory)
    enabled = make_company(name="Enabled", priority=20)
    disabled = make_company(
        name="Disabled",
        ats_identifier="disabled",
        enabled=False,
    )

    await repository.add(enabled)
    await repository.add(disabled)

    assert await repository.get(enabled.id) == enabled
    assert await repository.list_enabled() == (enabled,)


@pytest.mark.asyncio
async def test_seed_is_idempotent(database) -> None:
    _, session_factory = database
    repository = SQLAlchemyCompanyRepository(session_factory)
    service = SeedCompaniesService(repository)
    companies = development_companies(NOW)

    first = await service.seed(companies)
    second = await service.seed(companies)

    assert first.created == len(companies)
    assert first.updated == 0
    assert second.created == 0
    assert second.updated == len(companies)
    assert len(await repository.list_enabled()) == sum(
        company.enabled for company in companies
    )


@pytest.mark.asyncio
async def test_registry_update_preserves_identity_and_health(database) -> None:
    _, session_factory = database
    repository = SQLAlchemyCompanyRepository(session_factory)
    service = SeedCompaniesService(repository)
    original = make_company(name="Original")
    await service.seed([original])
    await repository.update_health(
        original.id,
        SourceHealthUpdate(
            checked_at=NOW,
            status=SourceHealthStatus.HEALTHY,
            job_count=7,
            error_category=None,
        ),
    )

    update = replace(original, id=uuid4(), name="Renamed", updated_at=LATER)
    result = await service.seed([update])
    stored = await repository.get(original.id)

    assert result.created == 0
    assert result.updated == 1
    assert stored is not None
    assert stored.id == original.id
    assert stored.name == "Renamed"
    assert stored.last_status is SourceHealthStatus.HEALTHY
    assert stored.last_job_count == 7


@pytest.mark.asyncio
async def test_vacancy_create_retrieve_and_update_preserves_identity(database) -> None:
    _, session_factory = database
    companies = SQLAlchemyCompanyRepository(session_factory)
    vacancies = SQLAlchemyVacancyRepository(session_factory)
    company = make_company()
    original = make_vacancy(company.id, employer_name="External Employer")
    await companies.add(company)

    first_result = await vacancies.upsert_many([original])
    updated_input = replace(
        original,
        id=uuid4(),
        title="Senior iOS Engineer",
        description="Updated description",
        first_seen_at=LATER,
        last_seen_at=LATER,
    )
    second_result = await vacancies.upsert_many([updated_input])
    stored = await vacancies.get_by_source_identity(
        VacancySource.GREENHOUSE,
        original.source_job_id,
    )

    assert (first_result.created, first_result.updated) == (1, 0)
    assert (second_result.created, second_result.updated) == (0, 1)
    assert stored is not None
    assert stored.id == original.id
    assert stored.first_seen_at == original.first_seen_at
    assert stored.last_seen_at == LATER
    assert stored.title == "Senior iOS Engineer"
    assert stored.employer_name == "External Employer"
    assert await vacancies.get(original.id) == stored


@pytest.mark.asyncio
async def test_persisted_timestamps_are_utc_aware(database) -> None:
    _, session_factory = database
    companies = SQLAlchemyCompanyRepository(session_factory)
    vacancies = SQLAlchemyVacancyRepository(session_factory)
    company = make_company()
    vacancy = make_vacancy(company.id)
    await companies.add(company)
    await vacancies.upsert_many([vacancy])

    stored_company = await companies.get(company.id)
    stored_vacancy = await vacancies.get(vacancy.id)

    assert stored_company is not None
    assert stored_vacancy is not None
    assert stored_company.created_at.tzinfo == UTC
    assert stored_vacancy.first_seen_at.tzinfo == UTC
    observations = await vacancies.list_observations(vacancy.id)
    assert observations[0].first_seen_at.tzinfo == UTC


@pytest.mark.asyncio
async def test_company_checked_timestamp_is_updated(database) -> None:
    _, session_factory = database
    repository = SQLAlchemyCompanyRepository(session_factory)
    company = make_company()
    await repository.add(company)

    await repository.mark_checked(company.id, LATER)
    stored = await repository.get(company.id)

    assert stored is not None
    assert stored.last_checked_at == LATER
    assert stored.updated_at == LATER


@pytest.mark.asyncio
async def test_health_failure_preserves_last_successful_count(database) -> None:
    _, session_factory = database
    repository = SQLAlchemyCompanyRepository(session_factory)
    company = make_company()
    await repository.add(company)

    await repository.update_health(
        company.id,
        SourceHealthUpdate(
            checked_at=NOW,
            status=SourceHealthStatus.HEALTHY,
            job_count=12,
            error_category=None,
        ),
    )
    await repository.update_health(
        company.id,
        SourceHealthUpdate(
            checked_at=LATER,
            status=SourceHealthStatus.FAILED,
            job_count=None,
            error_category="TimeoutError",
        ),
    )
    stored = await repository.get(company.id)

    assert stored is not None
    assert stored.last_checked_at == LATER
    assert stored.last_status is SourceHealthStatus.FAILED
    assert stored.last_error_category == "TimeoutError"
    assert stored.last_success_at == NOW
    assert stored.last_job_count == 12


@pytest.mark.asyncio
async def test_empty_collection_is_a_successful_health_check(database) -> None:
    _, session_factory = database
    repository = SQLAlchemyCompanyRepository(session_factory)
    company = make_company()
    await repository.add(company)

    await repository.update_health(
        company.id,
        SourceHealthUpdate(
            checked_at=LATER,
            status=SourceHealthStatus.EMPTY,
            job_count=0,
            error_category=None,
        ),
    )
    stored = await repository.get(company.id)

    assert stored is not None
    assert stored.last_status is SourceHealthStatus.EMPTY
    assert stored.last_success_at == LATER
    assert stored.last_job_count == 0


@pytest.mark.asyncio
async def test_database_enforces_source_identity_uniqueness(database) -> None:
    _, session_factory = database
    companies = SQLAlchemyCompanyRepository(session_factory)
    company = make_company()
    await companies.add(company)
    first = make_vacancy(company.id, source_job_id="duplicate")
    second = make_vacancy(company.id, source_job_id="duplicate")

    async with session_factory() as session:
        session.add_all(
            [
                JobVacancyRecord(
                    id=str(item.id),
                    company_id=str(item.company_id),
                    title=item.title,
                    description=item.description,
                    company_location=item.company_location,
                    work_location=item.work_location,
                    remote_policy=item.remote_policy.value,
                    url=item.url,
                    source=item.source.value,
                    source_job_id=item.source_job_id,
                    published_at=item.published_at,
                    first_seen_at=item.first_seen_at,
                    last_seen_at=item.last_seen_at,
                    status=item.status.value,
                )
                for item in (first, second)
            ]
        )
        with pytest.raises(IntegrityError):
            await session.commit()


@pytest.mark.asyncio
async def test_repository_transaction_rolls_back_whole_batch(database) -> None:
    _, session_factory = database
    companies = SQLAlchemyCompanyRepository(session_factory)
    vacancies = SQLAlchemyVacancyRepository(session_factory)
    company = make_company()
    await companies.add(company)
    shared_id = uuid4()
    first = make_vacancy(
        company.id,
        vacancy_id=shared_id,
        source_job_id="one",
    )
    second = make_vacancy(
        company.id,
        vacancy_id=shared_id,
        source_job_id="two",
    )

    with pytest.raises(IntegrityError):
        await vacancies.upsert_many([first, second])

    async with session_factory() as session:
        count = await session.scalar(select(func.count()).select_from(JobVacancyRecord))
    assert count == 0


@pytest.mark.asyncio
async def test_cross_source_canonical_url_dedup_prefers_official_source(
    database,
) -> None:
    _, session_factory = database
    companies = SQLAlchemyCompanyRepository(session_factory)
    vacancies = SQLAlchemyVacancyRepository(session_factory)
    official_company = make_company(name="Qonto", ats_identifier="qonto")
    aggregator = make_company(
        name="Aggregator",
        ats_type=ATSType.AGILEFLUENT,
        ats_identifier="aggregator",
    )
    await companies.add(official_company)
    await companies.add(aggregator)
    official = make_vacancy(
        official_company.id,
        source_job_id="official-1",
        employer_name="Qonto",
        url="https://boards.greenhouse.io/qonto/jobs/123?gh_src=mail",
    )
    discovered = make_vacancy(
        aggregator.id,
        source=VacancySource.AGILEFLUENT,
        source_job_id="aggregated-9",
        employer_name="Qonto",
        url="https://boards.greenhouse.io/qonto/jobs/123?utm_source=feed",
        original_source=VacancySource.GREENHOUSE,
    )

    first = await vacancies.upsert_many([official])
    second = await vacancies.upsert_many([discovered])
    canonical = await vacancies.get(official.id)
    observations = await vacancies.list_observations(official.id)

    assert first.created == 1
    assert second.created == 0
    assert second.cross_source_duplicates_collapsed == 1
    assert canonical is not None
    assert canonical.source is VacancySource.GREENHOUSE
    assert {item.discovered_via for item in observations} == {
        VacancySource.GREENHOUSE,
        VacancySource.AGILEFLUENT,
    }


@pytest.mark.asyncio
async def test_aggregator_first_is_upgraded_by_official_observation(database) -> None:
    _, session_factory = database
    companies = SQLAlchemyCompanyRepository(session_factory)
    vacancies = SQLAlchemyVacancyRepository(session_factory)
    official_company = make_company(name="Qonto", ats_identifier="qonto")
    aggregator = make_company(
        name="Aggregator",
        ats_type=ATSType.AGILEFLUENT,
        ats_identifier="aggregator",
    )
    await companies.add(official_company)
    await companies.add(aggregator)
    discovered = make_vacancy(
        aggregator.id,
        source=VacancySource.AGILEFLUENT,
        source_job_id="aggregated-9",
        employer_name="Qonto",
        url="https://boards.greenhouse.io/qonto/jobs/123?utm_source=feed",
    )
    official = make_vacancy(
        official_company.id,
        source_job_id="official-1",
        employer_name="Qonto",
        url="https://boards.greenhouse.io/qonto/jobs/123",
    )

    await vacancies.upsert_many([discovered])
    result = await vacancies.upsert_many([official])
    canonical = await vacancies.get(discovered.id)

    assert result.cross_source_duplicates_collapsed == 1
    assert canonical is not None
    assert canonical.id == discovered.id
    assert canonical.source is VacancySource.GREENHOUSE
    assert canonical.company_id == official_company.id
    assert len(await vacancies.list_observations(discovered.id)) == 2


@pytest.mark.asyncio
async def test_same_employer_and_title_with_distinct_urls_are_not_merged(
    database,
) -> None:
    _, session_factory = database
    companies = SQLAlchemyCompanyRepository(session_factory)
    vacancies = SQLAlchemyVacancyRepository(session_factory)
    company = make_company(name="Spotify")
    await companies.add(company)
    stockholm = make_vacancy(
        company.id,
        source_job_id="stockholm",
        employer_name="Spotify",
        work_location="Stockholm",
        url="https://example.com/jobs/stockholm",
    )
    london = make_vacancy(
        company.id,
        source_job_id="london",
        employer_name="Spotify",
        work_location="London",
        url="https://example.com/jobs/london",
    )

    result = await vacancies.upsert_many([stockholm, london])

    assert result.created == 2
    assert result.cross_source_duplicates_collapsed == 0
    assert len(await vacancies.list_active()) == 2


@pytest.mark.asyncio
async def test_company_registry_unique_ats_identity(database) -> None:
    _, session_factory = database
    repository = SQLAlchemyCompanyRepository(session_factory)
    first = make_company(ats_identifier="same")
    duplicate = make_company(ats_identifier="same")

    await repository.add(first)
    with pytest.raises(IntegrityError):
        await repository.add(duplicate)


def test_factory_timestamp_is_aware() -> None:
    assert NOW == datetime(2026, 9, 17, 8, 0, tzinfo=UTC)
