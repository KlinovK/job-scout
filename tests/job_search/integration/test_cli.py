import pytest

from job_search.domain.enums import (
    VacancyClosureEvidence,
    VacancyLifecycleProvenance,
    VacancyStatus,
)
from job_search.infrastructure.configuration import Settings
from job_search.infrastructure.persistence.sqlalchemy.repositories import (
    SQLAlchemyCompanyRepository,
    SQLAlchemyVacancyRepository,
)
from job_search.presentation.cli import _run
from tests.job_search.factories import LATER, make_company, make_vacancy


@pytest.mark.asyncio
async def test_vacancy_sources_displays_canonical_and_observation_lifecycle(
    database,
    capsys,
) -> None:
    engine, session_factory = database
    companies = SQLAlchemyCompanyRepository(session_factory)
    vacancies = SQLAlchemyVacancyRepository(session_factory)
    company = make_company()
    vacancy = make_vacancy(
        company.id,
        status=VacancyStatus.CLOSED,
        closed_at=LATER,
    )
    await companies.add(company)
    await vacancies.upsert_many((vacancy,))

    exit_code = await _run(
        "vacancy",
        Settings(database_url=str(engine.url)),
        vacancy_command="sources",
        vacancy_id=vacancy.id,
    )

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "Canonical status: active" in output
    assert "Canonical closed at: -" in output
    assert f"Source company ID: {company.id}" in output
    assert "Observation status: closed" in output
    assert f"Closure evidence: {VacancyClosureEvidence.UNKNOWN.value}" in output
    assert (
        f"Lifecycle provenance: {VacancyLifecycleProvenance.GENERIC_UPSERT.value}"
        in output
    )
    assert f"Observation closed at: {LATER.isoformat()}" in output
    assert "Missing complete snapshots: 0" in output
