from dataclasses import replace

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from job_search.application.services import ListCandidateVacanciesService
from job_search.domain.classification import ClassificationDecision
from job_search.domain.classifier import IOSVacancyClassifier
from job_search.infrastructure.persistence.sqlalchemy.models import (
    VacancyClassificationRecord,
)
from job_search.infrastructure.persistence.sqlalchemy.repositories import (
    SQLAlchemyClassificationRepository,
    SQLAlchemyCompanyRepository,
    SQLAlchemyVacancyRepository,
)
from tests.job_search.factories import LATER, NOW, make_company, make_vacancy


@pytest.mark.asyncio
async def test_classification_persistence_and_reclassification(database) -> None:
    _, session_factory = database
    companies = SQLAlchemyCompanyRepository(session_factory)
    vacancies = SQLAlchemyVacancyRepository(session_factory)
    classifications = SQLAlchemyClassificationRepository(session_factory)
    company = make_company()
    vacancy = make_vacancy(company.id)
    await companies.add(company)
    await vacancies.upsert_many([vacancy])
    original = IOSVacancyClassifier().classify(vacancy, NOW)

    first = await classifications.upsert_many([original])
    updated_value = replace(original, classified_at=LATER)
    second = await classifications.upsert_many([updated_value])
    stored = await classifications.get(vacancy.id, original.classifier_version)

    assert (first.created, first.updated) == (1, 0)
    assert (second.created, second.updated) == (0, 1)
    assert stored == updated_value


@pytest.mark.asyncio
async def test_classifier_versions_are_independent(database) -> None:
    _, session_factory = database
    companies = SQLAlchemyCompanyRepository(session_factory)
    vacancies = SQLAlchemyVacancyRepository(session_factory)
    classifications = SQLAlchemyClassificationRepository(session_factory)
    company = make_company()
    vacancy = make_vacancy(company.id)
    await companies.add(company)
    await vacancies.upsert_many([vacancy])
    first_version = IOSVacancyClassifier().classify(vacancy, NOW)
    second_version = replace(first_version, classifier_version="ios-v2")

    result = await classifications.upsert_many([first_version, second_version])

    assert (result.created, result.updated) == (2, 0)
    assert len(await classifications.list_for_version("ios-v1")) == 1
    assert len(await classifications.list_for_version("ios-v2")) == 1


@pytest.mark.asyncio
async def test_candidate_decision_query_and_limit(database) -> None:
    _, session_factory = database
    companies = SQLAlchemyCompanyRepository(session_factory)
    vacancies = SQLAlchemyVacancyRepository(session_factory)
    classifications = SQLAlchemyClassificationRepository(session_factory)
    company = make_company()
    await companies.add(company)
    match = make_vacancy(
        company.id,
        source_job_id="match",
        title="Senior iOS Engineer",
        description="Swift. Remote worldwide.",
    )
    possible = make_vacancy(
        company.id,
        source_job_id="possible",
        title="iOS Engineer",
        description="Swift. Remote.",
    )
    reject = make_vacancy(
        company.id,
        source_job_id="reject",
        title="Senior Android Engineer",
        description="Kotlin. Remote worldwide.",
    )
    await vacancies.upsert_many([match, possible, reject])
    classifier = IOSVacancyClassifier()
    await classifications.upsert_many(
        [classifier.classify(item, NOW) for item in (match, possible, reject)]
    )

    results = await classifications.list_by_decisions(
        classifier.version,
        (ClassificationDecision.MATCH, ClassificationDecision.POSSIBLE_MATCH),
        limit=1,
    )

    assert len(results) == 1
    assert results[0].decision is ClassificationDecision.MATCH

    candidates = await ListCandidateVacanciesService(
        companies,
        vacancies,
        classifications,
    ).list_candidates(classifier.version, limit=2)
    assert [item.classification.decision for item in candidates] == [
        ClassificationDecision.MATCH,
        ClassificationDecision.POSSIBLE_MATCH,
    ]
    assert all(item.company.id == company.id for item in candidates)


@pytest.mark.asyncio
async def test_database_enforces_classification_identity(database) -> None:
    _, session_factory = database
    companies = SQLAlchemyCompanyRepository(session_factory)
    vacancies = SQLAlchemyVacancyRepository(session_factory)
    company = make_company()
    vacancy = make_vacancy(company.id)
    await companies.add(company)
    await vacancies.upsert_many([vacancy])
    classification = IOSVacancyClassifier().classify(vacancy, NOW)

    def record() -> VacancyClassificationRecord:
        return VacancyClassificationRecord(
            vacancy_id=str(vacancy.id),
            classifier_version=classification.classifier_version,
            decision=classification.decision.value,
            role_category=classification.role_category.value,
            seniority=classification.seniority.value,
            ios_relevance=classification.ios_relevance.value,
            work_mode=classification.work_mode.value,
            relocation=classification.relocation.value,
            geography=classification.geography.value,
            positive_signals="",
            warnings="",
            rejection_reasons="",
            classified_at=NOW,
        )

    async with session_factory() as session:
        session.add_all([record(), record()])
        with pytest.raises(IntegrityError):
            await session.commit()

    async with session_factory() as session:
        count = await session.scalar(
            select(func.count()).select_from(VacancyClassificationRecord)
        )
    assert count == 0
