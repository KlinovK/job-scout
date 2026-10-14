import asyncio
from collections.abc import Sequence
from datetime import datetime
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from job_search.application.models import (
    CollectionCoverage,
    SourceCollectionResult,
    SourceHealthUpdate,
)
from job_search.application.services import CollectJobsService
from job_search.domain.enums import ATSType, SourceHealthStatus, VacancyStatus
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
from tests.job_search.factories import LATER, NOW, make_company, make_vacancy


def _result(
    vacancies: Sequence[JobVacancy],
    *,
    complete: bool = True,
    raw_count: int | None = None,
    malformed_count: int = 0,
) -> SourceCollectionResult:
    return SourceCollectionResult(
        vacancies=tuple(vacancies),
        coverage=CollectionCoverage.FULL_BOARD,
        complete=complete,
        raw_count=(
            len(vacancies) + malformed_count if raw_count is None else raw_count
        ),
        malformed_count=malformed_count,
        pagination_exhausted=True,
    )


def _health(
    checked_at: datetime,
    job_count: int,
) -> SourceHealthUpdate:
    return SourceHealthUpdate(
        checked_at=checked_at,
        status=(SourceHealthStatus.HEALTHY if job_count else SourceHealthStatus.EMPTY),
        job_count=job_count,
        error_category=None,
    )


async def _row_counts(session_factory) -> tuple[int, int]:
    async with session_factory() as session:
        vacancies = int(
            await session.scalar(select(func.count()).select_from(JobVacancyRecord))
            or 0
        )
        observations = int(
            await session.scalar(
                select(func.count()).select_from(VacancyObservationRecord)
            )
            or 0
        )
    return vacancies, observations


@pytest.mark.asyncio
async def test_successful_collection_commits_vacancies_observations_and_health(
    database,
) -> None:
    _, session_factory = database
    companies = SQLAlchemyCompanyRepository(session_factory)
    persistence = SQLAlchemyCollectionPersistence(session_factory)
    company = make_company()
    vacancy_a = make_vacancy(company.id, source_job_id="a")
    vacancy_b = make_vacancy(company.id, source_job_id="b")
    await companies.add(company)

    persisted = await persistence.persist_collection(
        company.id,
        _result((vacancy_a, vacancy_b)),
        _health(NOW, 2),
    )

    stored = await companies.get(company.id)
    assert persisted.applied is True
    assert persisted.vacancy_upsert.created == 2
    assert persisted.vacancy_upsert.observations_created == 2
    assert await _row_counts(session_factory) == (2, 2)
    assert stored is not None
    assert stored.last_checked_at == NOW
    assert stored.last_success_at == NOW
    assert stored.last_job_count == 2
    assert stored.last_status is SourceHealthStatus.HEALTHY
    assert stored.last_complete_snapshot_at is None
    async with session_factory() as session:
        owners = set(
            await session.scalars(select(VacancyObservationRecord.source_company_id))
        )
    assert owners == {str(company.id)}


@pytest.mark.asyncio
async def test_failure_after_canonical_insert_rolls_back_everything(
    database,
    monkeypatch,
) -> None:
    _, session_factory = database
    companies = SQLAlchemyCompanyRepository(session_factory)
    persistence = SQLAlchemyCollectionPersistence(session_factory)
    company = make_company()
    await companies.add(company)

    def fail_observation(*args, **kwargs):
        del args, kwargs
        raise RuntimeError("injected after canonical insert")

    monkeypatch.setattr(repositories, "_new_observation", fail_observation)

    with pytest.raises(RuntimeError, match="after canonical"):
        await persistence.persist_collection(
            company.id,
            _result((make_vacancy(company.id),)),
            _health(NOW, 1),
        )

    assert await _row_counts(session_factory) == (0, 0)
    assert await companies.get(company.id) == company


@pytest.mark.asyncio
async def test_failure_during_observation_flush_rolls_back_everything(
    database,
    monkeypatch,
) -> None:
    _, session_factory = database
    companies = SQLAlchemyCompanyRepository(session_factory)
    persistence = SQLAlchemyCollectionPersistence(session_factory)
    company = make_company()
    await companies.add(company)
    original = repositories._new_observation

    def invalid_observation(*args, **kwargs):
        observation = original(*args, **kwargs)
        observation.source_company_id = str(uuid4())
        return observation

    monkeypatch.setattr(repositories, "_new_observation", invalid_observation)

    with pytest.raises(IntegrityError):
        await persistence.persist_collection(
            company.id,
            _result((make_vacancy(company.id),)),
            _health(NOW, 1),
        )

    assert await _row_counts(session_factory) == (0, 0)
    assert await companies.get(company.id) == company


@pytest.mark.asyncio
async def test_failure_during_success_health_update_rolls_back_everything(
    database,
    monkeypatch,
) -> None:
    _, session_factory = database
    companies = SQLAlchemyCompanyRepository(session_factory)
    persistence = SQLAlchemyCollectionPersistence(session_factory)
    company = make_company()
    await companies.add(company)
    original = repositories._apply_success_health

    async def fail_after_health(*args, **kwargs) -> None:
        await original(*args, **kwargs)
        raise RuntimeError("injected during success metadata update")

    monkeypatch.setattr(repositories, "_apply_success_health", fail_after_health)

    with pytest.raises(RuntimeError, match="success metadata"):
        await persistence.persist_collection(
            company.id,
            _result((make_vacancy(company.id),)),
            _health(NOW, 1),
        )

    assert await _row_counts(session_factory) == (0, 0)
    assert await companies.get(company.id) == company


@pytest.mark.asyncio
async def test_replay_is_idempotent_and_preserves_lifecycle_state(database) -> None:
    _, session_factory = database
    companies = SQLAlchemyCompanyRepository(session_factory)
    vacancies = SQLAlchemyVacancyRepository(session_factory)
    persistence = SQLAlchemyCollectionPersistence(session_factory)
    company = make_company()
    vacancy = make_vacancy(company.id)
    result = _result((vacancy,))
    await companies.add(company)

    first = await persistence.persist_collection(
        company.id,
        result,
        _health(NOW, 1),
    )
    async with session_factory() as session, session.begin():
        canonical = await session.get(JobVacancyRecord, str(vacancy.id))
        observation = await session.scalar(select(VacancyObservationRecord))
        assert canonical is not None
        assert observation is not None
        canonical.status = VacancyStatus.CLOSED.value
        canonical.closed_at = LATER
        observation.status = VacancyStatus.CLOSED.value
        observation.closed_at = LATER
        observation.missing_complete_snapshots = 2

    exact_replay = await persistence.persist_collection(
        company.id,
        result,
        _health(NOW, 1),
    )
    later_replay = await persistence.persist_collection(
        company.id,
        result,
        _health(LATER, 1),
    )

    stored = await vacancies.get(vacancy.id)
    observations = await vacancies.list_observations(vacancy.id)
    assert first.applied is True
    assert exact_replay.applied is False
    assert later_replay.applied is True
    assert later_replay.vacancy_upsert.updated == 1
    assert await _row_counts(session_factory) == (1, 1)
    assert stored is not None
    assert stored.id == vacancy.id
    assert stored.status is VacancyStatus.CLOSED
    assert stored.closed_at == LATER
    assert observations[0].status is VacancyStatus.CLOSED
    assert observations[0].closed_at == LATER
    assert observations[0].missing_complete_snapshots == 2


@pytest.mark.asyncio
async def test_older_collection_and_health_cannot_overwrite_newer_state(
    database,
) -> None:
    _, session_factory = database
    companies = SQLAlchemyCompanyRepository(session_factory)
    vacancies = SQLAlchemyVacancyRepository(session_factory)
    persistence = SQLAlchemyCollectionPersistence(session_factory)
    company = make_company()
    newer = make_vacancy(
        company.id,
        title="Newer title",
        first_seen_at=LATER,
        last_seen_at=LATER,
    )
    older = make_vacancy(company.id, title="Older title")
    await companies.add(company)

    newest = await persistence.persist_collection(
        company.id,
        _result((newer,)),
        _health(LATER, 1),
    )
    stale = await persistence.persist_collection(
        company.id,
        _result((older,)),
        _health(NOW, 1),
    )
    stale_failure_applied = await companies.update_health(
        company.id,
        SourceHealthUpdate(
            checked_at=NOW,
            status=SourceHealthStatus.FAILED,
            job_count=None,
            error_category="TimeoutError",
        ),
    )

    stored_company = await companies.get(company.id)
    stored_vacancy = await vacancies.get(newer.id)
    assert newest.applied is True
    assert stale.applied is False
    assert stale_failure_applied is False
    assert stored_vacancy is not None
    assert stored_vacancy.title == "Newer title"
    assert stored_vacancy.last_seen_at == LATER
    assert stored_company is not None
    assert stored_company.last_checked_at == LATER
    assert stored_company.last_success_at == LATER
    assert stored_company.last_status is SourceHealthStatus.HEALTHY


@pytest.mark.asyncio
async def test_success_wins_an_equal_timestamp_tie_with_failure_health(
    database,
) -> None:
    _, session_factory = database
    companies = SQLAlchemyCompanyRepository(session_factory)
    persistence = SQLAlchemyCollectionPersistence(session_factory)
    company = make_company()
    await companies.add(company)
    await companies.update_health(
        company.id,
        SourceHealthUpdate(
            checked_at=NOW,
            status=SourceHealthStatus.FAILED,
            job_count=None,
            error_category="TimeoutError",
        ),
    )

    persisted = await persistence.persist_collection(
        company.id,
        _result((make_vacancy(company.id),)),
        _health(NOW, 1),
    )

    stored = await companies.get(company.id)
    assert persisted.applied is True
    assert stored is not None
    assert stored.last_checked_at == NOW
    assert stored.last_success_at == NOW
    assert stored.last_status is SourceHealthStatus.HEALTHY
    assert stored.last_error_category is None


@pytest.mark.asyncio
async def test_incomplete_collection_persists_valid_rows_without_snapshot_advance(
    database,
) -> None:
    _, session_factory = database
    companies = SQLAlchemyCompanyRepository(session_factory)
    persistence = SQLAlchemyCollectionPersistence(session_factory)
    company = make_company()
    vacancy = make_vacancy(company.id)
    incomplete = _result(
        (vacancy,),
        complete=False,
        raw_count=2,
        malformed_count=1,
    )
    await companies.add(company)

    persisted = await persistence.persist_collection(
        company.id,
        incomplete,
        _health(NOW, 1),
    )

    stored = await companies.get(company.id)
    assert incomplete.reconciliation_eligible is False
    assert persisted.applied is True
    assert await _row_counts(session_factory) == (1, 1)
    assert stored is not None
    assert stored.last_success_at == NOW
    assert stored.last_complete_snapshot_at is None


class _Provider:
    def __init__(self, source) -> None:
        self._source = source

    def get_source(self, ats_type: ATSType):
        assert ats_type is ATSType.GREENHOUSE
        return self._source


class _ImmediateSource:
    async def collect(
        self,
        company: Company,
        observed_at: datetime,
    ) -> SourceCollectionResult:
        return _result(
            (
                make_vacancy(
                    company.id,
                    title="Newer concurrent title",
                    first_seen_at=observed_at,
                    last_seen_at=observed_at,
                ),
            )
        )


class _BlockedOlderSource:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def collect(
        self,
        company: Company,
        observed_at: datetime,
    ) -> SourceCollectionResult:
        self.started.set()
        await self.release.wait()
        return _result(
            (
                make_vacancy(
                    company.id,
                    title="Older concurrent title",
                    first_seen_at=observed_at,
                    last_seen_at=observed_at,
                ),
            )
        )


@pytest.mark.asyncio
async def test_overlapping_runs_cannot_apply_an_older_result_after_a_newer_one(
    database,
) -> None:
    _, session_factory = database
    companies = SQLAlchemyCompanyRepository(session_factory)
    vacancies = SQLAlchemyVacancyRepository(session_factory)
    company = make_company()
    await companies.add(company)
    older_source = _BlockedOlderSource()
    older_service = CollectJobsService(
        companies,
        SQLAlchemyCollectionPersistence(session_factory),
        _Provider(older_source),
        clock=lambda: NOW,
    )
    newer_service = CollectJobsService(
        companies,
        SQLAlchemyCollectionPersistence(session_factory),
        _Provider(_ImmediateSource()),
        clock=lambda: LATER,
    )

    older_task = asyncio.create_task(older_service.collect())
    await older_source.started.wait()
    newer_summary = await newer_service.collect()
    older_source.release.set()
    older_summary = await older_task

    stored_company = await companies.get(company.id)
    active = await vacancies.list_active()
    assert newer_summary.new_jobs == 1
    assert older_summary.new_jobs == 0
    assert older_summary.updated_jobs == 0
    assert len(active) == 1
    assert active[0].title == "Newer concurrent title"
    assert active[0].last_seen_at == LATER
    assert stored_company is not None
    assert stored_company.last_success_at == LATER
