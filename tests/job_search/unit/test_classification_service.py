from collections.abc import Sequence
from uuid import UUID

import pytest

from job_search.application.models import (
    ClassificationUpsertResult,
    VacancyUpsertResult,
)
from job_search.application.services import ClassifyVacanciesService
from job_search.domain.classification import VacancyClassification
from job_search.domain.classifier import IOSVacancyClassifier
from job_search.domain.enums import VacancySource
from job_search.domain.models import JobVacancy
from tests.job_search.factories import NOW, make_company, make_vacancy


class FakeVacancies:
    def __init__(self, vacancies: Sequence[JobVacancy]) -> None:
        self.vacancies = tuple(vacancies)

    async def get(self, vacancy_id: UUID) -> JobVacancy | None:
        return next((item for item in self.vacancies if item.id == vacancy_id), None)

    async def get_by_source_identity(
        self,
        source: VacancySource,
        source_job_id: str,
    ) -> JobVacancy | None:
        del source, source_job_id
        return None

    async def upsert_many(
        self,
        vacancies: Sequence[JobVacancy],
    ) -> VacancyUpsertResult:
        del vacancies
        raise NotImplementedError

    async def list_active(self) -> Sequence[JobVacancy]:
        return self.vacancies


class FakeClassifications:
    def __init__(self) -> None:
        self.items: dict[tuple[UUID, str], VacancyClassification] = {}

    async def upsert_many(
        self,
        classifications: Sequence[VacancyClassification],
    ) -> ClassificationUpsertResult:
        created = 0
        updated = 0
        for classification in classifications:
            key = (classification.vacancy_id, classification.classifier_version)
            if key in self.items:
                updated += 1
            else:
                created += 1
            self.items[key] = classification
        return ClassificationUpsertResult(created=created, updated=updated)

    async def get(
        self,
        vacancy_id: UUID,
        classifier_version: str,
    ) -> VacancyClassification | None:
        return self.items.get((vacancy_id, classifier_version))

    async def list_for_version(
        self,
        classifier_version: str,
    ) -> Sequence[VacancyClassification]:
        return tuple(
            item
            for (_, version), item in self.items.items()
            if version == classifier_version
        )


@pytest.mark.asyncio
async def test_classification_service_counts_mixed_decisions() -> None:
    company = make_company()
    vacancies = (
        make_vacancy(
            company.id,
            source_job_id="match",
            title="Senior iOS Engineer",
            description="Swift role. Remote worldwide.",
        ),
        make_vacancy(
            company.id,
            source_job_id="possible",
            title="iOS Engineer",
            description="Build apps in Swift. Remote.",
        ),
        make_vacancy(
            company.id,
            source_job_id="reject",
            title="Senior Android Engineer",
            description="Collaborate with iOS engineers. Remote worldwide.",
        ),
    )
    stored = FakeClassifications()
    service = ClassifyVacanciesService(
        FakeVacancies(vacancies),
        stored,
        IOSVacancyClassifier(),
        clock=lambda: NOW,
    )

    summary = await service.classify()

    assert summary.processed == 3
    assert (summary.matches, summary.possible_matches, summary.rejected) == (1, 1, 1)
    assert (summary.created, summary.updated) == (3, 0)


@pytest.mark.asyncio
async def test_repeated_classification_is_idempotent() -> None:
    company = make_company()
    vacancy = make_vacancy(
        company.id,
        title="Senior iOS Engineer",
        description="Swift role. Remote worldwide.",
    )
    stored = FakeClassifications()
    service = ClassifyVacanciesService(
        FakeVacancies([vacancy]),
        stored,
        IOSVacancyClassifier(),
        clock=lambda: NOW,
    )

    first = await service.classify()
    second = await service.classify()

    assert (first.created, first.updated) == (1, 0)
    assert (second.created, second.updated) == (0, 1)
    assert len(stored.items) == 1
