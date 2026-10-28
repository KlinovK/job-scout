from dataclasses import replace
from uuid import UUID

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from job_search.application.models import (
    CollectionCoverage,
    SourceCollectionResult,
    SourceHealthUpdate,
)
from job_search.application.services import ListCandidateVacanciesService
from job_search.domain.classification import ClassificationDecision
from job_search.domain.classifier import IOSVacancyClassifier
from job_search.domain.enums import (
    ATSType,
    SourceHealthStatus,
    VacancyClosureEvidence,
    VacancyLifecycleProvenance,
    VacancySource,
    VacancyStatus,
)
from job_search.infrastructure.persistence.sqlalchemy.models import (
    JobVacancyRecord,
    VacancyClassificationRecord,
)
from job_search.infrastructure.persistence.sqlalchemy.repositories import (
    SQLAlchemyClassificationRepository,
    SQLAlchemyCollectionPersistence,
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
async def test_active_candidate_filter_runs_before_limit_and_keeps_history(
    database,
) -> None:
    _, session_factory = database
    companies = SQLAlchemyCompanyRepository(session_factory)
    vacancies = SQLAlchemyVacancyRepository(session_factory)
    classifications = SQLAlchemyClassificationRepository(session_factory)
    company = make_company(ats_type=ATSType.HH, ats_identifier="candidate-hh")
    await companies.add(company)
    closed = make_vacancy(
        company.id,
        source_job_id="closed-match",
        title="Senior iOS Engineer",
        description="Swift. Remote worldwide.",
        status=VacancyStatus.CLOSED,
        source=VacancySource.HH,
        closure_evidence=VacancyClosureEvidence.PROVIDER_EXPLICIT,
    )
    active = make_vacancy(
        company.id,
        source_job_id="active-match",
        title="Senior iOS Engineer",
        description="Swift. Remote worldwide.",
        source=VacancySource.HH,
    )
    result = SourceCollectionResult(
        vacancies=(closed, active),
        coverage=CollectionCoverage.ROLLING_WINDOW,
        complete=True,
        raw_count=2,
        malformed_count=0,
        pagination_exhausted=True,
    )
    await SQLAlchemyCollectionPersistence(session_factory).persist_collection(
        company.id,
        result,
        SourceHealthUpdate(
            checked_at=LATER,
            status=SourceHealthStatus.HEALTHY,
            job_count=2,
            error_category=None,
        ),
    )
    classifier = IOSVacancyClassifier()
    closed_classification = classifier.classify(closed, LATER)
    active_classification = classifier.classify(active, NOW)
    await classifications.upsert_many([closed_classification, active_classification])

    historical = await classifications.list_by_decisions(
        classifier.version,
        (ClassificationDecision.MATCH,),
        limit=1,
    )
    current = await classifications.list_active_by_decisions(
        classifier.version,
        (ClassificationDecision.MATCH,),
        limit=1,
    )
    candidates = await ListCandidateVacanciesService(
        companies,
        vacancies,
        classifications,
    ).list_candidates(classifier.version, limit=1)

    assert historical == (closed_classification,)
    assert current == (active_classification,)
    assert [candidate.vacancy.id for candidate in candidates] == [active.id]
    assert len(await classifications.list_for_version(classifier.version)) == 2
    async with session_factory() as session:
        stored_closed = await session.get(JobVacancyRecord, str(closed.id))
    assert stored_closed is not None
    assert stored_closed.status == VacancyStatus.CLOSED.value


@pytest.mark.asyncio
async def test_generic_cross_source_presence_requires_accepted_collection_to_reopen(
    database,
) -> None:
    _, session_factory = database
    companies = SQLAlchemyCompanyRepository(session_factory)
    vacancies = SQLAlchemyVacancyRepository(session_factory)
    classifications = SQLAlchemyClassificationRepository(session_factory)
    persistence = SQLAlchemyCollectionPersistence(session_factory)
    hh_company = make_company(
        name="HH",
        ats_type=ATSType.HH,
        ats_identifier="candidate-hh-authority",
    )
    greenhouse_company = make_company(
        name="Greenhouse",
        ats_identifier="candidate-greenhouse-authority",
    )
    shared_url = "https://example.com/jobs/candidate-authority"
    closed = make_vacancy(
        hh_company.id,
        source=VacancySource.HH,
        source_job_id="hh-closed",
        url=shared_url,
        title="Senior iOS Engineer",
        description="Swift. Remote worldwide.",
        status=VacancyStatus.CLOSED,
        closure_evidence=VacancyClosureEvidence.PROVIDER_EXPLICIT,
    )
    greenhouse_active = make_vacancy(
        greenhouse_company.id,
        source=VacancySource.GREENHOUSE,
        source_job_id="greenhouse-active",
        url=shared_url,
        title="Senior iOS Engineer",
        description="Swift. Remote worldwide.",
    )
    await companies.add(hh_company)
    await companies.add(greenhouse_company)
    await persistence.persist_collection(
        hh_company.id,
        SourceCollectionResult(
            vacancies=(closed,),
            coverage=CollectionCoverage.ROLLING_WINDOW,
            complete=True,
            raw_count=1,
            malformed_count=0,
            pagination_exhausted=True,
        ),
        SourceHealthUpdate(
            checked_at=NOW,
            status=SourceHealthStatus.HEALTHY,
            job_count=1,
            error_category=None,
        ),
    )
    classification = IOSVacancyClassifier().classify(closed, NOW)
    await classifications.upsert_many((classification,))

    first_generic = await vacancies.upsert_many((greenhouse_active,))
    repeated_generic = await vacancies.upsert_many((greenhouse_active,))

    restarted_vacancies = SQLAlchemyVacancyRepository(session_factory)
    canonical = await restarted_vacancies.get(closed.id)
    observations = await restarted_vacancies.list_observations(closed.id)
    candidates = await ListCandidateVacanciesService(
        companies,
        restarted_vacancies,
        classifications,
    ).list_candidates(classification.classifier_version, limit=1)
    assert first_generic.observations_created == 1
    assert repeated_generic.updated == 1
    assert len(observations) == 2
    assert canonical is not None
    assert canonical.status is VacancyStatus.CLOSED
    assert candidates == ()
    assert {
        observation.discovered_via: observation.lifecycle_provenance
        for observation in observations
    } == {
        VacancySource.HH: VacancyLifecycleProvenance.ACCEPTED_COLLECTION,
        VacancySource.GREENHOUSE: VacancyLifecycleProvenance.GENERIC_UPSERT,
    }
    assert {
        observation.discovered_via: (
            observation.status,
            observation.closure_evidence,
        )
        for observation in observations
    } == {
        VacancySource.HH: (
            VacancyStatus.CLOSED,
            VacancyClosureEvidence.PROVIDER_EXPLICIT,
        ),
        VacancySource.GREENHOUSE: (
            VacancyStatus.ACTIVE,
            VacancyClosureEvidence.UNKNOWN,
        ),
    }

    await persistence.persist_collection(
        hh_company.id,
        SourceCollectionResult(
            vacancies=(closed,),
            coverage=CollectionCoverage.ROLLING_WINDOW,
            complete=True,
            raw_count=1,
            malformed_count=0,
            pagination_exhausted=True,
        ),
        SourceHealthUpdate(
            checked_at=LATER,
            status=SourceHealthStatus.HEALTHY,
            job_count=1,
            error_category=None,
        ),
    )
    still_closed = await vacancies.get(closed.id)
    assert still_closed is not None
    assert still_closed.status is VacancyStatus.CLOSED

    await persistence.persist_collection(
        greenhouse_company.id,
        SourceCollectionResult(
            vacancies=(greenhouse_active,),
            coverage=CollectionCoverage.FULL_BOARD,
            complete=True,
            raw_count=1,
            malformed_count=0,
            pagination_exhausted=True,
        ),
        SourceHealthUpdate(
            checked_at=LATER,
            status=SourceHealthStatus.HEALTHY,
            job_count=1,
            error_category=None,
        ),
    )

    reopened = await vacancies.get(closed.id)
    reopened_observations = await vacancies.list_observations(closed.id)
    reopened_candidates = await ListCandidateVacanciesService(
        companies,
        vacancies,
        classifications,
    ).list_candidates(classification.classifier_version, limit=1)
    assert reopened is not None
    assert reopened.status is VacancyStatus.ACTIVE
    assert reopened.closed_at is None
    assert len(reopened_observations) == 2
    assert {
        observation.discovered_via: observation.lifecycle_provenance
        for observation in reopened_observations
    } == {
        VacancySource.HH: VacancyLifecycleProvenance.ACCEPTED_COLLECTION,
        VacancySource.GREENHOUSE: VacancyLifecycleProvenance.ACCEPTED_COLLECTION,
    }
    assert {
        observation.discovered_via: observation.status
        for observation in reopened_observations
    } == {
        VacancySource.HH: VacancyStatus.CLOSED,
        VacancySource.GREENHOUSE: VacancyStatus.ACTIVE,
    }
    assert [candidate.vacancy.id for candidate in reopened_candidates] == [closed.id]


@pytest.mark.asyncio
async def test_unknown_closed_evidence_keeps_candidate_visible(database) -> None:
    _, session_factory = database
    companies = SQLAlchemyCompanyRepository(session_factory)
    vacancies = SQLAlchemyVacancyRepository(session_factory)
    classifications = SQLAlchemyClassificationRepository(session_factory)
    company = make_company()
    vacancy = make_vacancy(
        company.id,
        title="Senior iOS Engineer",
        description="Swift. Remote worldwide.",
        status=VacancyStatus.CLOSED,
        closed_at=LATER,
        closure_evidence=VacancyClosureEvidence.PROVIDER_EXPLICIT,
    )
    await companies.add(company)
    await vacancies.upsert_many((vacancy,))
    classification = IOSVacancyClassifier().classify(vacancy, LATER)
    await classifications.upsert_many((classification,))

    candidates = await ListCandidateVacanciesService(
        companies,
        vacancies,
        classifications,
    ).list_candidates(classification.classifier_version, limit=1)

    assert [candidate.vacancy.id for candidate in candidates] == [vacancy.id]
    assert candidates[0].vacancy.status is VacancyStatus.ACTIVE
    assert (
        candidates[0].observations[0].closure_evidence is VacancyClosureEvidence.UNKNOWN
    )


@pytest.mark.asyncio
async def test_active_candidate_order_uses_vacancy_id_as_stable_tie_breaker(
    database,
) -> None:
    _, session_factory = database
    companies = SQLAlchemyCompanyRepository(session_factory)
    vacancies = SQLAlchemyVacancyRepository(session_factory)
    classifications = SQLAlchemyClassificationRepository(session_factory)
    company = make_company()
    await companies.add(company)
    later_id = make_vacancy(
        company.id,
        vacancy_id=UUID("00000000-0000-0000-0000-000000000002"),
        source_job_id="later-id",
        title="Senior iOS Engineer",
        description="Swift. Remote worldwide.",
    )
    earlier_id = make_vacancy(
        company.id,
        vacancy_id=UUID("00000000-0000-0000-0000-000000000001"),
        source_job_id="earlier-id",
        title="Senior iOS Engineer",
        description="Swift. Remote worldwide.",
    )
    await vacancies.upsert_many([later_id, earlier_id])
    classifier = IOSVacancyClassifier()
    await classifications.upsert_many(
        [classifier.classify(item, NOW) for item in (later_id, earlier_id)]
    )

    results = await classifications.list_active_by_decisions(
        classifier.version,
        (ClassificationDecision.MATCH,),
    )

    assert [item.vacancy_id for item in results] == [earlier_id.id, later_id.id]


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
