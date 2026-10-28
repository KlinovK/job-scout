import asyncio
from collections.abc import Sequence
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from job_search.application.models import (
    CollectionCoverage,
    SourceCollectionResult,
    SourceHealthUpdate,
)
from job_search.application.services import CollectJobsService
from job_search.domain.enums import (
    ATSType,
    SourceHealthStatus,
    VacancyClosureEvidence,
    VacancySource,
    VacancyStatus,
)
from job_search.domain.models import Company, JobVacancy
from job_search.infrastructure.persistence.sqlalchemy import repositories
from job_search.infrastructure.persistence.sqlalchemy.models import (
    JobVacancyRecord,
    VacancyObservationRecord,
)
from job_search.infrastructure.persistence.sqlalchemy.repositories import (
    SQLAlchemyCollectionPersistence,
    SQLAlchemyCompanyRepository,
    SQLAlchemyVacancyRepository,
)
from tests.job_search.factories import NOW, make_company, make_vacancy

T0 = NOW
T1 = NOW + timedelta(hours=1)
T2 = NOW + timedelta(hours=2)
T3 = NOW + timedelta(hours=3)


def _result(
    vacancies: Sequence[JobVacancy] = (),
    *,
    coverage: CollectionCoverage = CollectionCoverage.FULL_BOARD,
    complete: bool = True,
    malformed_count: int = 0,
    pagination_exhausted: bool = True,
) -> SourceCollectionResult:
    return SourceCollectionResult(
        vacancies=tuple(vacancies),
        coverage=coverage,
        complete=complete,
        raw_count=len(vacancies) + malformed_count,
        malformed_count=malformed_count,
        pagination_exhausted=pagination_exhausted,
    )


async def _persist(
    persistence: SQLAlchemyCollectionPersistence,
    company: Company,
    result: SourceCollectionResult,
    checked_at: datetime,
):
    return await persistence.persist_collection(
        company.id,
        result,
        SourceHealthUpdate(
            checked_at=checked_at,
            status=(
                SourceHealthStatus.HEALTHY
                if result.vacancies
                else SourceHealthStatus.EMPTY
            ),
            job_count=len(result.vacancies),
            error_category=None,
        ),
    )


async def _setup_active_observation(database):
    _, session_factory = database
    companies = SQLAlchemyCompanyRepository(session_factory)
    vacancies = SQLAlchemyVacancyRepository(session_factory)
    persistence = SQLAlchemyCollectionPersistence(session_factory)
    company = make_company()
    vacancy = make_vacancy(company.id)
    await companies.add(company)
    await _persist(persistence, company, _result((vacancy,)), T0)
    return session_factory, companies, vacancies, persistence, company, vacancy


async def _only_observation(
    vacancies: SQLAlchemyVacancyRepository,
    vacancy_id,
):
    observations = await vacancies.list_observations(vacancy_id)
    assert len(observations) == 1
    return observations[0]


@pytest.mark.asyncio
async def test_first_missing_authoritative_snapshot_increments_without_closing(
    database,
) -> None:
    (
        _,
        companies,
        vacancies,
        persistence,
        company,
        vacancy,
    ) = await _setup_active_observation(database)

    await _persist(persistence, company, _result(), T1)

    observation = await _only_observation(vacancies, vacancy.id)
    canonical = await vacancies.get(vacancy.id)
    stored_company = await companies.get(company.id)
    assert observation.status is VacancyStatus.ACTIVE
    assert observation.missing_complete_snapshots == 1
    assert observation.closed_at is None
    assert canonical is not None
    assert canonical.status is VacancyStatus.ACTIVE
    assert canonical.closed_at is None
    assert stored_company is not None
    assert stored_company.last_complete_snapshot_at == T1


@pytest.mark.asyncio
async def test_second_missing_authoritative_snapshot_closes_observation(
    database,
) -> None:
    (
        _,
        companies,
        vacancies,
        persistence,
        company,
        vacancy,
    ) = await _setup_active_observation(database)
    await _persist(persistence, company, _result(), T1)

    await _persist(persistence, company, _result(), T2)

    observation = await _only_observation(vacancies, vacancy.id)
    canonical = await vacancies.get(vacancy.id)
    stored_company = await companies.get(company.id)
    assert observation.status is VacancyStatus.CLOSED
    assert observation.closure_evidence is VacancyClosureEvidence.AUTHORITATIVE_ABSENCE
    assert observation.missing_complete_snapshots == 2
    assert observation.closed_at == T2
    assert canonical is not None
    assert canonical.status is VacancyStatus.CLOSED
    assert canonical.closed_at == T2
    assert stored_company is not None
    assert stored_company.last_complete_snapshot_at == T2


@pytest.mark.asyncio
async def test_repeated_missing_snapshot_preserves_closure_time_and_bounded_counter(
    database,
) -> None:
    _, _, vacancies, persistence, company, vacancy = await _setup_active_observation(
        database
    )
    await _persist(persistence, company, _result(), T1)
    await _persist(persistence, company, _result(), T2)

    await _persist(persistence, company, _result(), T3)

    observation = await _only_observation(vacancies, vacancy.id)
    canonical = await vacancies.get(vacancy.id)
    assert observation.status is VacancyStatus.CLOSED
    assert observation.closure_evidence is VacancyClosureEvidence.AUTHORITATIVE_ABSENCE
    assert observation.missing_complete_snapshots == 2
    assert observation.closed_at == T2
    assert canonical is not None
    assert canonical.status is VacancyStatus.CLOSED
    assert canonical.closed_at == T2


@pytest.mark.asyncio
async def test_closed_observation_reopens_in_authoritative_snapshot(database) -> None:
    _, _, vacancies, persistence, company, vacancy = await _setup_active_observation(
        database
    )
    await _persist(persistence, company, _result(), T1)
    await _persist(persistence, company, _result(), T2)
    reappeared = make_vacancy(
        company.id,
        source_job_id=vacancy.source_job_id,
        title="Reappeared",
        first_seen_at=vacancy.first_seen_at,
        last_seen_at=T3,
    )

    await _persist(persistence, company, _result((reappeared,)), T3)

    observation = await _only_observation(vacancies, vacancy.id)
    canonical = await vacancies.get(vacancy.id)
    assert observation.status is VacancyStatus.ACTIVE
    assert observation.missing_complete_snapshots == 0
    assert observation.closed_at is None
    assert observation.closure_evidence is VacancyClosureEvidence.UNKNOWN
    assert observation.title == "Reappeared"
    assert canonical is not None
    assert canonical.status is VacancyStatus.ACTIVE
    assert canonical.closed_at is None


@pytest.mark.asyncio
async def test_observation_with_one_miss_resets_when_it_reappears(database) -> None:
    _, _, vacancies, persistence, company, vacancy = await _setup_active_observation(
        database
    )
    await _persist(persistence, company, _result(), T1)
    reappeared = make_vacancy(
        company.id,
        source_job_id=vacancy.source_job_id,
        first_seen_at=vacancy.first_seen_at,
        last_seen_at=T2,
    )

    await _persist(persistence, company, _result((reappeared,)), T2)

    observation = await _only_observation(vacancies, vacancy.id)
    assert observation.status is VacancyStatus.ACTIVE
    assert observation.missing_complete_snapshots == 0
    assert observation.closed_at is None


@pytest.mark.asyncio
async def test_incomplete_snapshot_does_not_reconcile_or_advance_metadata(
    database,
) -> None:
    (
        _,
        companies,
        vacancies,
        persistence,
        company,
        vacancy,
    ) = await _setup_active_observation(database)

    await _persist(
        persistence,
        company,
        _result(complete=False, pagination_exhausted=False),
        T1,
    )

    observation = await _only_observation(vacancies, vacancy.id)
    stored_company = await companies.get(company.id)
    assert observation.status is VacancyStatus.ACTIVE
    assert observation.missing_complete_snapshots == 0
    assert stored_company is not None
    assert stored_company.last_success_at == T1
    assert stored_company.last_complete_snapshot_at == T0


@pytest.mark.asyncio
async def test_empty_malformed_snapshot_does_not_reconcile(database) -> None:
    (
        _,
        companies,
        vacancies,
        persistence,
        company,
        vacancy,
    ) = await _setup_active_observation(database)

    malformed = _result(complete=False, malformed_count=1)
    await _persist(persistence, company, malformed, T1)

    observation = await _only_observation(vacancies, vacancy.id)
    stored_company = await companies.get(company.id)
    assert malformed.reconciliation_eligible is False
    assert observation.missing_complete_snapshots == 0
    assert stored_company is not None
    assert stored_company.last_complete_snapshot_at == T0


@pytest.mark.asyncio
async def test_empty_rolling_window_result_does_not_reconcile(database) -> None:
    _, session_factory = database
    companies = SQLAlchemyCompanyRepository(session_factory)
    vacancies = SQLAlchemyVacancyRepository(session_factory)
    persistence = SQLAlchemyCollectionPersistence(session_factory)
    company = make_company(
        ats_type=ATSType.AGILEFLUENT,
        ats_identifier="agilefluent",
    )
    vacancy = make_vacancy(company.id, source=VacancySource.AGILEFLUENT)
    rolling_present = _result(
        (vacancy,),
        coverage=CollectionCoverage.ROLLING_WINDOW,
    )
    rolling_empty = _result(coverage=CollectionCoverage.ROLLING_WINDOW)
    await companies.add(company)
    await _persist(persistence, company, rolling_present, T0)

    await _persist(persistence, company, rolling_empty, T1)

    observation = await _only_observation(vacancies, vacancy.id)
    stored_company = await companies.get(company.id)
    assert observation.status is VacancyStatus.ACTIVE
    assert observation.missing_complete_snapshots == 0
    assert stored_company is not None
    assert stored_company.last_complete_snapshot_at is None


class _FailingSource:
    async def collect(
        self,
        company: Company,
        observed_at: datetime,
    ) -> SourceCollectionResult:
        del company, observed_at
        raise RuntimeError("source unavailable")


class _Provider:
    def get_source(self, ats_type: ATSType):
        assert ats_type is ATSType.GREENHOUSE
        return _FailingSource()


@pytest.mark.asyncio
async def test_failed_collection_does_not_reconcile(database) -> None:
    (
        _,
        companies,
        vacancies,
        persistence,
        company,
        vacancy,
    ) = await _setup_active_observation(database)
    service = CollectJobsService(
        companies,
        persistence,
        _Provider(),
        clock=lambda: T1,
    )

    summary = await service.collect()

    observation = await _only_observation(vacancies, vacancy.id)
    stored_company = await companies.get(company.id)
    assert summary.failure_count == 1
    assert observation.status is VacancyStatus.ACTIVE
    assert observation.missing_complete_snapshots == 0
    assert stored_company is not None
    assert stored_company.last_checked_at == T1
    assert stored_company.last_success_at == T0
    assert stored_company.last_complete_snapshot_at == T0


@pytest.mark.asyncio
async def test_stale_snapshot_cannot_change_lifecycle(database) -> None:
    (
        _,
        companies,
        vacancies,
        persistence,
        company,
        vacancy,
    ) = await _setup_active_observation(database)
    newer = await _persist(persistence, company, _result(), T2)

    stale = await _persist(persistence, company, _result(), T1)

    observation = await _only_observation(vacancies, vacancy.id)
    stored_company = await companies.get(company.id)
    assert newer.applied is True
    assert stale.applied is False
    assert observation.status is VacancyStatus.ACTIVE
    assert observation.missing_complete_snapshots == 1
    assert stored_company is not None
    assert stored_company.last_complete_snapshot_at == T2


@pytest.mark.asyncio
async def test_exact_snapshot_replay_does_not_increment_missing_count(database) -> None:
    _, _, vacancies, persistence, company, vacancy = await _setup_active_observation(
        database
    )
    first = await _persist(persistence, company, _result(), T1)

    replay = await _persist(persistence, company, _result(), T1)

    observation = await _only_observation(vacancies, vacancy.id)
    assert first.applied is True
    assert replay.applied is False
    assert observation.status is VacancyStatus.ACTIVE
    assert observation.missing_complete_snapshots == 1


@pytest.mark.asyncio
async def test_duplicate_source_ids_in_one_snapshot_are_idempotent(database) -> None:
    _, _, vacancies, persistence, company, vacancy = await _setup_active_observation(
        database
    )
    duplicate = make_vacancy(
        company.id,
        source_job_id=vacancy.source_job_id,
        first_seen_at=vacancy.first_seen_at,
        last_seen_at=T1,
    )

    result = await _persist(
        persistence,
        company,
        _result((vacancy, duplicate)),
        T1,
    )

    observations = await vacancies.list_observations(vacancy.id)
    assert result.applied is True
    assert result.vacancy_upsert.created == 0
    assert result.vacancy_upsert.updated == 2
    assert len(observations) == 1
    assert observations[0].status is VacancyStatus.ACTIVE
    assert observations[0].missing_complete_snapshots == 0


@pytest.mark.asyncio
async def test_legacy_null_ownership_observation_is_not_reconciled(database) -> None:
    (
        session_factory,
        companies,
        vacancies,
        persistence,
        company,
        vacancy,
    ) = await _setup_active_observation(database)
    async with session_factory() as session, session.begin():
        observation = await session.scalar(select(VacancyObservationRecord))
        assert observation is not None
        observation.source_company_id = None

    await _persist(persistence, company, _result(), T1)
    await _persist(persistence, company, _result(), T2)

    observation = await _only_observation(vacancies, vacancy.id)
    stored_company = await companies.get(company.id)
    assert observation.source_company_id is None
    assert observation.status is VacancyStatus.ACTIVE
    assert observation.missing_complete_snapshots == 0
    assert stored_company is not None
    assert stored_company.last_complete_snapshot_at == T2


@pytest.mark.asyncio
async def test_reconciliation_is_scoped_to_one_company(database) -> None:
    _, session_factory = database
    companies = SQLAlchemyCompanyRepository(session_factory)
    vacancies = SQLAlchemyVacancyRepository(session_factory)
    persistence = SQLAlchemyCollectionPersistence(session_factory)
    company_a = make_company(name="A", ats_identifier="a")
    company_b = make_company(name="B", ats_identifier="b")
    vacancy_a = make_vacancy(company_a.id, source_job_id="shared-id")
    vacancy_b = make_vacancy(
        company_b.id,
        source_job_id="shared-id",
        url="https://example.com/jobs/company-b",
    )
    await companies.add(company_a)
    await companies.add(company_b)
    await _persist(persistence, company_a, _result((vacancy_a,)), T0)
    await _persist(persistence, company_b, _result((vacancy_b,)), T0)

    await _persist(persistence, company_a, _result(), T1)
    await _persist(persistence, company_a, _result(), T2)

    observation_a = await _only_observation(vacancies, vacancy_a.id)
    observation_b = await _only_observation(vacancies, vacancy_b.id)
    assert observation_a.status is VacancyStatus.CLOSED
    assert observation_b.status is VacancyStatus.ACTIVE
    assert observation_b.missing_complete_snapshots == 0


@pytest.mark.asyncio
async def test_reconciliation_is_scoped_to_one_source(database) -> None:
    _, session_factory = database
    companies = SQLAlchemyCompanyRepository(session_factory)
    vacancies = SQLAlchemyVacancyRepository(session_factory)
    persistence = SQLAlchemyCollectionPersistence(session_factory)
    company = make_company()
    greenhouse = make_vacancy(company.id, source_job_id="greenhouse")
    lever = make_vacancy(
        company.id,
        source=VacancySource.LEVER,
        source_job_id="lever",
        url="https://example.com/jobs/lever",
    )
    await companies.add(company)
    await _persist(persistence, company, _result((greenhouse,)), T0)
    await vacancies.upsert_many((lever,))

    await _persist(persistence, company, _result(), T1)
    await _persist(persistence, company, _result(), T2)

    greenhouse_observation = await _only_observation(vacancies, greenhouse.id)
    lever_observation = await _only_observation(vacancies, lever.id)
    assert greenhouse_observation.status is VacancyStatus.CLOSED
    assert lever_observation.status is VacancyStatus.ACTIVE
    assert lever_observation.missing_complete_snapshots == 0


@pytest.mark.asyncio
async def test_closing_one_observation_keeps_shared_canonical_vacancy_active(
    database,
) -> None:
    _, session_factory = database
    companies = SQLAlchemyCompanyRepository(session_factory)
    vacancies = SQLAlchemyVacancyRepository(session_factory)
    persistence = SQLAlchemyCollectionPersistence(session_factory)
    greenhouse_company = make_company(name="Official", ats_identifier="official")
    lever_company = make_company(
        name="Mirror",
        ats_type=ATSType.LEVER,
        ats_identifier="mirror",
    )
    shared_url = "https://example.com/jobs/shared"
    greenhouse = make_vacancy(
        greenhouse_company.id,
        source_job_id="greenhouse-job",
        url=shared_url,
    )
    lever = make_vacancy(
        lever_company.id,
        source=VacancySource.LEVER,
        source_job_id="lever-job",
        url=shared_url,
    )
    await companies.add(greenhouse_company)
    await companies.add(lever_company)
    await _persist(persistence, greenhouse_company, _result((greenhouse,)), T0)
    await _persist(persistence, lever_company, _result((lever,)), T0)

    await _persist(persistence, greenhouse_company, _result(), T1)
    await _persist(persistence, greenhouse_company, _result(), T2)

    canonical = await vacancies.get(greenhouse.id)
    observations = await vacancies.list_observations(greenhouse.id)
    states = {
        observation.discovered_via: observation.status for observation in observations
    }
    assert canonical is not None
    assert canonical.status is VacancyStatus.ACTIVE
    assert states == {
        VacancySource.GREENHOUSE: VacancyStatus.CLOSED,
        VacancySource.LEVER: VacancyStatus.ACTIVE,
    }


@pytest.mark.asyncio
async def test_all_owned_trustworthy_observations_close_shared_canonical(
    database,
) -> None:
    _, session_factory = database
    companies = SQLAlchemyCompanyRepository(session_factory)
    vacancies = SQLAlchemyVacancyRepository(session_factory)
    persistence = SQLAlchemyCollectionPersistence(session_factory)
    greenhouse_company = make_company(name="Official", ats_identifier="official")
    lever_company = make_company(
        name="Mirror",
        ats_type=ATSType.LEVER,
        ats_identifier="mirror",
    )
    shared_url = "https://example.com/jobs/shared-closed"
    greenhouse = make_vacancy(
        greenhouse_company.id,
        source_job_id="greenhouse-closed",
        url=shared_url,
    )
    lever = make_vacancy(
        lever_company.id,
        source=VacancySource.LEVER,
        source_job_id="lever-closed",
        url=shared_url,
    )
    await companies.add(greenhouse_company)
    await companies.add(lever_company)
    await _persist(persistence, greenhouse_company, _result((greenhouse,)), T0)
    await _persist(persistence, lever_company, _result((lever,)), T0)

    await _persist(persistence, greenhouse_company, _result(), T1)
    await _persist(persistence, greenhouse_company, _result(), T2)
    after_one_source = await vacancies.get(greenhouse.id)
    assert after_one_source is not None
    assert after_one_source.status is VacancyStatus.ACTIVE

    await _persist(persistence, lever_company, _result(), T1)
    await _persist(persistence, lever_company, _result(), T2)

    canonical = await vacancies.get(greenhouse.id)
    observations = await vacancies.list_observations(greenhouse.id)
    assert canonical is not None
    assert canonical.status is VacancyStatus.CLOSED
    assert canonical.closed_at == T2
    assert {item.status for item in observations} == {VacancyStatus.CLOSED}
    assert {item.closed_at for item in observations} == {T2}
    assert {item.closure_evidence for item in observations} == {
        VacancyClosureEvidence.AUTHORITATIVE_ABSENCE
    }


@pytest.mark.asyncio
async def test_null_owned_observation_blocks_canonical_closure(database) -> None:
    _, session_factory = database
    companies = SQLAlchemyCompanyRepository(session_factory)
    vacancies = SQLAlchemyVacancyRepository(session_factory)
    persistence = SQLAlchemyCollectionPersistence(session_factory)
    official_company = make_company(name="Official", ats_identifier="official-null")
    mirror_company = make_company(
        name="Mirror",
        ats_type=ATSType.LEVER,
        ats_identifier="mirror-null",
    )
    shared_url = "https://example.com/jobs/shared-null"
    official = make_vacancy(
        official_company.id,
        source_job_id="official-null",
        url=shared_url,
    )
    mirror = make_vacancy(
        mirror_company.id,
        source=VacancySource.LEVER,
        source_job_id="mirror-null",
        url=shared_url,
    )
    await companies.add(official_company)
    await companies.add(mirror_company)
    await _persist(persistence, official_company, _result((official,)), T0)
    await _persist(persistence, mirror_company, _result((mirror,)), T0)
    async with session_factory() as session, session.begin():
        unowned = await session.scalar(
            select(VacancyObservationRecord).where(
                VacancyObservationRecord.discovered_via
                == VacancySource.GREENHOUSE.value
            )
        )
        canonical = await session.get(JobVacancyRecord, str(official.id))
        assert unowned is not None
        assert canonical is not None
        unowned.source_company_id = None
        unowned.status = VacancyStatus.CLOSED.value
        unowned.closed_at = T1
        canonical.status = VacancyStatus.CLOSED.value
        canonical.closed_at = T1

    await _persist(persistence, mirror_company, _result(), T1)
    await _persist(persistence, mirror_company, _result(), T2)

    canonical = await vacancies.get(official.id)
    assert canonical is not None
    assert canonical.status is VacancyStatus.ACTIVE
    assert canonical.closed_at is None


@pytest.mark.asyncio
async def test_incomplete_positive_row_reopens_closed_observation_and_canonical(
    database,
) -> None:
    _, _, vacancies, persistence, company, vacancy = await _setup_active_observation(
        database
    )
    await _persist(persistence, company, _result(), T1)
    await _persist(persistence, company, _result(), T2)
    reappeared = make_vacancy(
        company.id,
        source_job_id=vacancy.source_job_id,
        first_seen_at=vacancy.first_seen_at,
        last_seen_at=T3,
    )

    await _persist(
        persistence,
        company,
        _result((reappeared,), complete=False),
        T3,
    )

    observation = await _only_observation(vacancies, vacancy.id)
    canonical = await vacancies.get(vacancy.id)
    assert observation.status is VacancyStatus.ACTIVE
    assert observation.missing_complete_snapshots == 0
    assert observation.closed_at is None
    assert observation.last_seen_at == T3
    assert canonical is not None
    assert canonical.status is VacancyStatus.ACTIVE
    assert canonical.closed_at is None


@pytest.mark.asyncio
async def test_positive_closed_row_closes_observation_and_canonical(database) -> None:
    _, session_factory = database
    companies = SQLAlchemyCompanyRepository(session_factory)
    vacancies = SQLAlchemyVacancyRepository(session_factory)
    persistence = SQLAlchemyCollectionPersistence(session_factory)
    company = make_company(ats_type=ATSType.HH, ats_identifier="hh")
    active = make_vacancy(
        company.id,
        source=VacancySource.HH,
        source_job_id="hh-job",
    )
    closed = make_vacancy(
        company.id,
        vacancy_id=active.id,
        source=VacancySource.HH,
        source_job_id=active.source_job_id,
        first_seen_at=T0,
        last_seen_at=T1,
        status=VacancyStatus.CLOSED,
        closure_evidence=VacancyClosureEvidence.PROVIDER_EXPLICIT,
    )
    await companies.add(company)
    await _persist(
        persistence,
        company,
        _result((active,), coverage=CollectionCoverage.ROLLING_WINDOW),
        T0,
    )

    await _persist(
        persistence,
        company,
        _result((closed,), coverage=CollectionCoverage.ROLLING_WINDOW),
        T1,
    )

    observation = await _only_observation(vacancies, active.id)
    canonical = await vacancies.get(active.id)
    assert observation.status is VacancyStatus.CLOSED
    assert observation.closed_at == T1
    assert observation.missing_complete_snapshots == 0
    assert observation.closure_evidence is VacancyClosureEvidence.PROVIDER_EXPLICIT
    assert canonical is not None
    assert canonical.status is VacancyStatus.CLOSED
    assert canonical.closed_at == T1

    repeated = make_vacancy(
        company.id,
        vacancy_id=active.id,
        source=VacancySource.HH,
        source_job_id=active.source_job_id,
        first_seen_at=T0,
        last_seen_at=T2,
        status=VacancyStatus.CLOSED,
        closure_evidence=VacancyClosureEvidence.PROVIDER_EXPLICIT,
    )
    await _persist(
        persistence,
        company,
        _result((repeated,), coverage=CollectionCoverage.ROLLING_WINDOW),
        T2,
    )
    repeated_observation = await _only_observation(vacancies, active.id)
    repeated_canonical = await vacancies.get(active.id)
    assert repeated_observation.closed_at == T1
    assert (
        repeated_observation.closure_evidence
        is VacancyClosureEvidence.PROVIDER_EXPLICIT
    )
    assert repeated_canonical is not None
    assert repeated_canonical.closed_at == T1

    stale_active = make_vacancy(
        company.id,
        vacancy_id=active.id,
        source=VacancySource.HH,
        source_job_id=active.source_job_id,
        first_seen_at=T0,
        last_seen_at=T1,
    )
    stale = await _persist(
        persistence,
        company,
        _result((stale_active,), coverage=CollectionCoverage.ROLLING_WINDOW),
        T1,
    )
    after_stale = await _only_observation(vacancies, active.id)
    assert stale.applied is False
    assert after_stale.closed_at == T1
    assert after_stale.closure_evidence is VacancyClosureEvidence.PROVIDER_EXPLICIT


@pytest.mark.asyncio
async def test_unknown_positive_closed_row_cannot_close_canonical(database) -> None:
    _, session_factory = database
    companies = SQLAlchemyCompanyRepository(session_factory)
    vacancies = SQLAlchemyVacancyRepository(session_factory)
    persistence = SQLAlchemyCollectionPersistence(session_factory)
    company = make_company(ats_type=ATSType.HH, ats_identifier="hh-unknown")
    ambiguous = make_vacancy(
        company.id,
        source=VacancySource.HH,
        source_job_id="ambiguous-closed",
        status=VacancyStatus.CLOSED,
        closed_at=T1,
    )
    await companies.add(company)

    await _persist(
        persistence,
        company,
        _result((ambiguous,), coverage=CollectionCoverage.ROLLING_WINDOW),
        T1,
    )

    observation = await _only_observation(vacancies, ambiguous.id)
    canonical = await vacancies.get(ambiguous.id)
    assert observation.status is VacancyStatus.CLOSED
    assert observation.closed_at == T1
    assert observation.closure_evidence is VacancyClosureEvidence.UNKNOWN
    assert canonical is not None
    assert canonical.status is VacancyStatus.ACTIVE
    assert canonical.closed_at is None


@pytest.mark.asyncio
async def test_generic_active_upsert_cannot_reopen_authoritative_closure(
    database,
) -> None:
    _, session_factory = database
    companies = SQLAlchemyCompanyRepository(session_factory)
    vacancies = SQLAlchemyVacancyRepository(session_factory)
    persistence = SQLAlchemyCollectionPersistence(session_factory)
    company = make_company(ats_type=ATSType.HH, ats_identifier="generic-reopen")
    explicit = make_vacancy(
        company.id,
        source=VacancySource.HH,
        status=VacancyStatus.CLOSED,
        closure_evidence=VacancyClosureEvidence.PROVIDER_EXPLICIT,
    )
    await companies.add(company)
    await _persist(
        persistence,
        company,
        _result((explicit,), coverage=CollectionCoverage.ROLLING_WINDOW),
        T1,
    )
    direct_active = make_vacancy(
        company.id,
        vacancy_id=explicit.id,
        source=VacancySource.HH,
        source_job_id=explicit.source_job_id,
        title="Metadata refreshed outside collection",
        first_seen_at=T0,
        last_seen_at=T2,
    )

    await vacancies.upsert_many((direct_active,))

    canonical = await vacancies.get(explicit.id)
    observation = await _only_observation(vacancies, explicit.id)
    assert canonical is not None
    assert canonical.title == "Metadata refreshed outside collection"
    assert canonical.status is VacancyStatus.CLOSED
    assert canonical.closed_at == T1
    assert observation.status is VacancyStatus.CLOSED
    assert observation.closed_at == T1
    assert observation.closure_evidence is VacancyClosureEvidence.PROVIDER_EXPLICIT


@pytest.mark.asyncio
async def test_unknown_legacy_closure_requires_two_new_authoritative_misses(
    database,
) -> None:
    (
        session_factory,
        _,
        vacancies,
        persistence,
        company,
        vacancy,
    ) = await _setup_active_observation(database)
    async with session_factory() as session, session.begin():
        observation = await session.scalar(select(VacancyObservationRecord))
        assert observation is not None
        observation.status = VacancyStatus.CLOSED.value
        observation.closed_at = T0
        observation.closure_evidence = VacancyClosureEvidence.UNKNOWN.value
        observation.missing_complete_snapshots = 2

    await _persist(persistence, company, _result(), T1)
    after_first = await _only_observation(vacancies, vacancy.id)
    assert after_first.status is VacancyStatus.ACTIVE
    assert after_first.closed_at is None
    assert after_first.missing_complete_snapshots == 1
    assert after_first.closure_evidence is VacancyClosureEvidence.UNKNOWN

    await _persist(persistence, company, _result(), T2)
    after_second = await _only_observation(vacancies, vacancy.id)
    canonical = await vacancies.get(vacancy.id)
    assert after_second.status is VacancyStatus.CLOSED
    assert after_second.closed_at == T2
    assert after_second.closure_evidence is VacancyClosureEvidence.AUTHORITATIVE_ABSENCE
    assert canonical is not None
    assert canonical.status is VacancyStatus.CLOSED


@pytest.mark.asyncio
async def test_ambiguous_active_counter_requires_two_new_authoritative_misses(
    database,
) -> None:
    (
        session_factory,
        _,
        vacancies,
        persistence,
        company,
        vacancy,
    ) = await _setup_active_observation(database)
    async with session_factory() as session, session.begin():
        observation = await session.scalar(select(VacancyObservationRecord))
        assert observation is not None
        observation.missing_complete_snapshots = 2

    await _persist(persistence, company, _result(), T1)
    after_first = await _only_observation(vacancies, vacancy.id)
    assert after_first.status is VacancyStatus.ACTIVE
    assert after_first.missing_complete_snapshots == 1
    assert after_first.closure_evidence is VacancyClosureEvidence.UNKNOWN

    await _persist(persistence, company, _result(), T2)
    after_second = await _only_observation(vacancies, vacancy.id)
    assert after_second.status is VacancyStatus.CLOSED
    assert after_second.missing_complete_snapshots == 2
    assert after_second.closure_evidence is VacancyClosureEvidence.AUTHORITATIVE_ABSENCE


@pytest.mark.asyncio
async def test_authoritative_and_unknown_closed_observations_keep_canonical_active(
    database,
) -> None:
    _, session_factory = database
    companies = SQLAlchemyCompanyRepository(session_factory)
    vacancies = SQLAlchemyVacancyRepository(session_factory)
    persistence = SQLAlchemyCollectionPersistence(session_factory)
    hh_company = make_company(
        name="Authoritative",
        ats_type=ATSType.HH,
        ats_identifier="mixed-hh",
    )
    lever_company = make_company(
        name="Ambiguous",
        ats_type=ATSType.LEVER,
        ats_identifier="mixed-lever",
    )
    shared_url = "https://example.com/jobs/mixed-evidence"
    explicit = make_vacancy(
        hh_company.id,
        source=VacancySource.HH,
        source_job_id="mixed-hh",
        url=shared_url,
        status=VacancyStatus.CLOSED,
        closure_evidence=VacancyClosureEvidence.PROVIDER_EXPLICIT,
    )
    ambiguous = make_vacancy(
        lever_company.id,
        source=VacancySource.LEVER,
        source_job_id="mixed-lever",
        url=shared_url,
        status=VacancyStatus.CLOSED,
        closed_at=T1,
    )
    await companies.add(hh_company)
    await companies.add(lever_company)
    await _persist(
        persistence,
        hh_company,
        _result((explicit,), coverage=CollectionCoverage.ROLLING_WINDOW),
        T1,
    )
    await _persist(
        persistence,
        lever_company,
        _result((ambiguous,), coverage=CollectionCoverage.ROLLING_WINDOW),
        T1,
    )

    canonical = await vacancies.get(explicit.id)
    observations = await vacancies.list_observations(explicit.id)
    assert canonical is not None
    assert canonical.status is VacancyStatus.ACTIVE
    assert {item.closure_evidence for item in observations} == {
        VacancyClosureEvidence.PROVIDER_EXPLICIT,
        VacancyClosureEvidence.UNKNOWN,
    }


@pytest.mark.asyncio
async def test_rolling_window_positive_active_row_reopens_closed_vacancy(
    database,
) -> None:
    _, _, vacancies, persistence, company, vacancy = await _setup_active_observation(
        database
    )
    await _persist(persistence, company, _result(), T1)
    await _persist(persistence, company, _result(), T2)
    reappeared = make_vacancy(
        company.id,
        source_job_id=vacancy.source_job_id,
        first_seen_at=T0,
        last_seen_at=T3,
    )

    await _persist(
        persistence,
        company,
        _result((reappeared,), coverage=CollectionCoverage.ROLLING_WINDOW),
        T3,
    )

    observation = await _only_observation(vacancies, vacancy.id)
    canonical = await vacancies.get(vacancy.id)
    assert observation.status is VacancyStatus.ACTIVE
    assert observation.closed_at is None
    assert observation.missing_complete_snapshots == 0
    assert canonical is not None
    assert canonical.status is VacancyStatus.ACTIVE
    assert canonical.closed_at is None


@pytest.mark.asyncio
async def test_reclosure_after_positive_reopening_uses_new_timestamp(database) -> None:
    _, _, vacancies, persistence, company, vacancy = await _setup_active_observation(
        database
    )
    await _persist(persistence, company, _result(), T1)
    await _persist(persistence, company, _result(), T2)
    reappeared = make_vacancy(
        company.id,
        source_job_id=vacancy.source_job_id,
        first_seen_at=vacancy.first_seen_at,
        last_seen_at=T3,
    )
    await _persist(
        persistence,
        company,
        _result((reappeared,), complete=False),
        T3,
    )
    t4 = T3 + timedelta(hours=1)
    t5 = T3 + timedelta(hours=2)

    await _persist(persistence, company, _result(), t4)
    await _persist(persistence, company, _result(), t5)

    observation = await _only_observation(vacancies, vacancy.id)
    canonical = await vacancies.get(vacancy.id)
    assert observation.status is VacancyStatus.CLOSED
    assert observation.closed_at == t5
    assert observation.closure_evidence is VacancyClosureEvidence.AUTHORITATIVE_ABSENCE
    assert canonical is not None
    assert canonical.status is VacancyStatus.CLOSED
    assert canonical.closed_at == t5


@pytest.mark.asyncio
async def test_failure_after_missing_counter_update_rolls_back_collection(
    database,
    monkeypatch,
) -> None:
    (
        _,
        companies,
        vacancies,
        persistence,
        company,
        vacancy,
    ) = await _setup_active_observation(database)
    original = repositories._mark_observation_missing

    def fail_after_counter(*args, **kwargs) -> None:
        original(*args, **kwargs)
        raise RuntimeError("injected after missing counter update")

    monkeypatch.setattr(repositories, "_mark_observation_missing", fail_after_counter)

    with pytest.raises(RuntimeError, match="missing counter"):
        await _persist(persistence, company, _result(), T1)

    observation = await _only_observation(vacancies, vacancy.id)
    stored_company = await companies.get(company.id)
    assert observation.status is VacancyStatus.ACTIVE
    assert observation.missing_complete_snapshots == 0
    assert stored_company is not None
    assert stored_company.last_success_at == T0
    assert stored_company.last_complete_snapshot_at == T0


@pytest.mark.asyncio
async def test_failure_after_observation_closure_rolls_back_collection(
    database,
    monkeypatch,
) -> None:
    (
        _,
        companies,
        vacancies,
        persistence,
        company,
        vacancy,
    ) = await _setup_active_observation(database)
    await _persist(persistence, company, _result(), T1)
    original = repositories._mark_observation_missing

    def fail_after_closure(*args, **kwargs) -> None:
        original(*args, **kwargs)
        raise RuntimeError("injected after observation closure")

    monkeypatch.setattr(repositories, "_mark_observation_missing", fail_after_closure)

    with pytest.raises(RuntimeError, match="observation closure"):
        await _persist(persistence, company, _result(), T2)

    observation = await _only_observation(vacancies, vacancy.id)
    stored_company = await companies.get(company.id)
    assert observation.status is VacancyStatus.ACTIVE
    assert observation.missing_complete_snapshots == 1
    assert observation.closed_at is None
    assert stored_company is not None
    assert stored_company.last_success_at == T1
    assert stored_company.last_complete_snapshot_at == T1


@pytest.mark.asyncio
async def test_failure_after_complete_snapshot_metadata_rolls_back_collection(
    database,
    monkeypatch,
) -> None:
    (
        _,
        companies,
        vacancies,
        persistence,
        company,
        vacancy,
    ) = await _setup_active_observation(database)
    original = repositories._advance_complete_snapshot

    def fail_after_metadata(*args, **kwargs) -> None:
        original(*args, **kwargs)
        raise RuntimeError("injected after complete snapshot metadata")

    monkeypatch.setattr(repositories, "_advance_complete_snapshot", fail_after_metadata)

    with pytest.raises(RuntimeError, match="snapshot metadata"):
        await _persist(persistence, company, _result(), T1)

    observation = await _only_observation(vacancies, vacancy.id)
    stored_company = await companies.get(company.id)
    assert observation.status is VacancyStatus.ACTIVE
    assert observation.missing_complete_snapshots == 0
    assert stored_company is not None
    assert stored_company.last_success_at == T0
    assert stored_company.last_complete_snapshot_at == T0


@pytest.mark.asyncio
async def test_failure_during_success_health_rolls_back_lifecycle_changes(
    database,
    monkeypatch,
) -> None:
    (
        _,
        companies,
        vacancies,
        persistence,
        company,
        vacancy,
    ) = await _setup_active_observation(database)
    original = repositories._apply_success_health

    async def fail_after_health(*args, **kwargs) -> None:
        await original(*args, **kwargs)
        raise RuntimeError("injected after successful health update")

    monkeypatch.setattr(repositories, "_apply_success_health", fail_after_health)

    with pytest.raises(RuntimeError, match="successful health"):
        await _persist(persistence, company, _result(), T1)

    observation = await _only_observation(vacancies, vacancy.id)
    stored_company = await companies.get(company.id)
    assert observation.status is VacancyStatus.ACTIVE
    assert observation.missing_complete_snapshots == 0
    assert stored_company is not None
    assert stored_company.last_checked_at == T0
    assert stored_company.last_success_at == T0
    assert stored_company.last_complete_snapshot_at == T0


@pytest.mark.asyncio
@pytest.mark.parametrize("apply_before_failure", [False, True])
async def test_aggregation_failure_rolls_back_observation_canonical_and_health(
    database,
    monkeypatch,
    apply_before_failure: bool,
) -> None:
    (
        _,
        companies,
        vacancies,
        persistence,
        company,
        vacancy,
    ) = await _setup_active_observation(database)
    await _persist(persistence, company, _result(), T1)
    original = repositories._aggregate_canonical_lifecycle

    async def fail_aggregation(*args, **kwargs) -> None:
        if apply_before_failure:
            await original(*args, **kwargs)
        raise RuntimeError("injected aggregation failure")

    monkeypatch.setattr(
        repositories,
        "_aggregate_canonical_lifecycle",
        fail_aggregation,
    )

    with pytest.raises(RuntimeError, match="aggregation failure"):
        await _persist(persistence, company, _result(), T2)

    observation = await _only_observation(vacancies, vacancy.id)
    canonical = await vacancies.get(vacancy.id)
    stored_company = await companies.get(company.id)
    assert observation.status is VacancyStatus.ACTIVE
    assert observation.missing_complete_snapshots == 1
    assert observation.closed_at is None
    assert canonical is not None
    assert canonical.status is VacancyStatus.ACTIVE
    assert canonical.closed_at is None
    assert stored_company is not None
    assert stored_company.last_success_at == T1
    assert stored_company.last_complete_snapshot_at == T1


@pytest.mark.asyncio
async def test_lifecycle_aggregation_batches_all_affected_vacancies(database) -> None:
    _, session_factory = database
    companies = SQLAlchemyCompanyRepository(session_factory)
    vacancies = SQLAlchemyVacancyRepository(session_factory)
    persistence = SQLAlchemyCollectionPersistence(session_factory)
    company = make_company(ats_identifier="batching")
    vacancy_count = repositories._LIFECYCLE_AGGREGATION_BATCH_SIZE + 5
    source_vacancies = tuple(
        make_vacancy(company.id, source_job_id=f"batch-{index}")
        for index in range(vacancy_count)
    )
    await companies.add(company)
    await _persist(persistence, company, _result(source_vacancies), T0)

    await _persist(persistence, company, _result(), T1)
    await _persist(persistence, company, _result(), T2)

    async with session_factory() as session:
        canonical_states = (
            await session.scalars(select(JobVacancyRecord.status))
        ).all()
        observation_states = (
            await session.execute(
                select(
                    VacancyObservationRecord.status,
                    VacancyObservationRecord.closure_evidence,
                )
            )
        ).all()
    assert len(canonical_states) == vacancy_count
    assert set(canonical_states) == {VacancyStatus.CLOSED.value}
    assert len(observation_states) == vacancy_count
    assert set(observation_states) == {
        (
            VacancyStatus.CLOSED.value,
            VacancyClosureEvidence.AUTHORITATIVE_ABSENCE.value,
        )
    }
    assert await vacancies.list_active() == ()


@pytest.mark.asyncio
async def test_separate_persistence_instances_serialize_shared_canonical_updates(
    database,
    monkeypatch,
) -> None:
    _, session_factory = database
    companies = SQLAlchemyCompanyRepository(session_factory)
    vacancies = SQLAlchemyVacancyRepository(session_factory)
    persistence_a = SQLAlchemyCollectionPersistence(session_factory)
    persistence_b = SQLAlchemyCollectionPersistence(session_factory)
    company_a = make_company(name="A", ats_identifier="concurrent-a")
    company_b = make_company(
        name="B",
        ats_type=ATSType.LEVER,
        ats_identifier="concurrent-b",
    )
    shared_url = "https://example.com/jobs/concurrent-shared"
    vacancy_a = make_vacancy(
        company_a.id,
        source_job_id="concurrent-a",
        url=shared_url,
    )
    vacancy_b = make_vacancy(
        company_b.id,
        source=VacancySource.LEVER,
        source_job_id="concurrent-b",
        url=shared_url,
    )
    await companies.add(company_a)
    await companies.add(company_b)
    await _persist(persistence_a, company_a, _result((vacancy_a,)), T0)
    await _persist(persistence_b, company_b, _result((vacancy_b,)), T0)
    await _persist(persistence_a, company_a, _result(), T1)
    await _persist(persistence_b, company_b, _result(), T1)
    first_aggregation_entered = asyncio.Event()
    release_first_transaction = asyncio.Event()
    second_claim_started = asyncio.Event()
    original_aggregate = repositories._aggregate_canonical_lifecycle
    original_claim = repositories._claim_collection_success

    async def controlled_aggregate(session, vacancy_ids, transition_at) -> None:
        if transition_at == T2 and not first_aggregation_entered.is_set():
            first_aggregation_entered.set()
            await asyncio.wait_for(release_first_transaction.wait(), timeout=2)
        await original_aggregate(session, vacancy_ids, transition_at)

    async def observed_claim(
        session,
        company_id,
        checked_at,
        *,
        complete_snapshot,
    ):
        if company_id == company_b.id and checked_at == T2:
            second_claim_started.set()
        return await original_claim(
            session,
            company_id,
            checked_at,
            complete_snapshot=complete_snapshot,
        )

    monkeypatch.setattr(
        repositories,
        "_aggregate_canonical_lifecycle",
        controlled_aggregate,
    )
    monkeypatch.setattr(repositories, "_claim_collection_success", observed_claim)
    task_a = asyncio.create_task(_persist(persistence_a, company_a, _result(), T2))
    await asyncio.wait_for(first_aggregation_entered.wait(), timeout=1)
    task_b = asyncio.create_task(_persist(persistence_b, company_b, _result(), T2))
    try:
        await asyncio.wait_for(second_claim_started.wait(), timeout=1)
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(asyncio.shield(task_b), timeout=0.05)
    finally:
        release_first_transaction.set()
    result_a, result_b = await asyncio.gather(task_a, task_b)

    canonical = await vacancies.get(vacancy_a.id)
    observations = await vacancies.list_observations(vacancy_a.id)
    assert result_a.applied is True
    assert result_b.applied is True
    assert canonical is not None
    assert canonical.status is VacancyStatus.CLOSED
    assert canonical.closed_at == T2
    assert {item.status for item in observations} == {VacancyStatus.CLOSED}
    assert {item.closure_evidence for item in observations} == {
        VacancyClosureEvidence.AUTHORITATIVE_ABSENCE
    }
    stored_a = await companies.get(company_a.id)
    stored_b = await companies.get(company_b.id)
    assert stored_a is not None
    assert stored_b is not None
    assert stored_a.last_complete_snapshot_at == T2
    assert stored_b.last_complete_snapshot_at == T2


@pytest.mark.asyncio
async def test_source_mismatch_rejects_whole_collection(database) -> None:
    _, session_factory = database
    companies = SQLAlchemyCompanyRepository(session_factory)
    persistence = SQLAlchemyCollectionPersistence(session_factory)
    company = make_company()
    mismatched = make_vacancy(company.id, source=VacancySource.LEVER)
    await companies.add(company)

    with pytest.raises(ValueError, match="company source"):
        await _persist(persistence, company, _result((mismatched,)), T0)

    stored_company = await companies.get(company.id)
    async with session_factory() as session:
        vacancy_count = len((await session.scalars(select(JobVacancyRecord))).all())
        observation_count = len(
            (await session.scalars(select(VacancyObservationRecord))).all()
        )
    assert stored_company == company
    assert vacancy_count == 0
    assert observation_count == 0
