from collections.abc import Sequence
from datetime import datetime
from typing import Protocol
from uuid import UUID

from job_search.application.models import (
    ClassificationUpsertResult,
    DeduplicationStats,
    SourceCollectionResult,
    SourceHealthUpdate,
    VacancyUpsertResult,
)
from job_search.domain.classification import (
    ClassificationDecision,
    VacancyClassification,
)
from job_search.domain.enums import ATSType, VacancySource
from job_search.domain.models import Company, JobVacancy, VacancyObservation


class CompanyRepository(Protocol):
    async def add(self, company: Company) -> None: ...

    async def get(self, company_id: UUID) -> Company | None: ...

    async def list_enabled(self) -> Sequence[Company]: ...

    async def list_all(self) -> Sequence[Company]: ...

    async def list_for_sources(
        self,
        sources: Sequence[ATSType],
        *,
        include_disabled: bool = False,
    ) -> Sequence[Company]: ...

    async def upsert_by_ats_identity(self, company: Company) -> bool: ...

    async def mark_checked(self, company_id: UUID, checked_at: datetime) -> None: ...

    async def update_health(
        self,
        company_id: UUID,
        update: SourceHealthUpdate,
    ) -> None: ...


class VacancyRepository(Protocol):
    async def get(self, vacancy_id: UUID) -> JobVacancy | None: ...

    async def get_by_source_identity(
        self,
        source_company_id: UUID,
        source: VacancySource,
        source_job_id: str,
    ) -> JobVacancy | None: ...

    async def upsert_many(
        self,
        vacancies: Sequence[JobVacancy],
    ) -> VacancyUpsertResult: ...

    async def list_active(self) -> Sequence[JobVacancy]: ...

    async def list_observations(
        self,
        vacancy_id: UUID,
    ) -> Sequence[VacancyObservation]: ...

    async def deduplication_stats(self) -> DeduplicationStats: ...


class ClassificationRepository(Protocol):
    async def upsert_many(
        self,
        classifications: Sequence[VacancyClassification],
    ) -> ClassificationUpsertResult: ...

    async def get(
        self,
        vacancy_id: UUID,
        classifier_version: str,
    ) -> VacancyClassification | None: ...

    async def list_for_version(
        self,
        classifier_version: str,
    ) -> Sequence[VacancyClassification]: ...

    async def list_by_decisions(
        self,
        classifier_version: str,
        decisions: Sequence[ClassificationDecision],
        *,
        limit: int | None = None,
    ) -> Sequence[VacancyClassification]: ...


class VacancyClassifier(Protocol):
    version: str

    def classify(
        self,
        vacancy: JobVacancy,
        classified_at: datetime,
    ) -> VacancyClassification: ...


class JobSource(Protocol):
    async def collect(
        self,
        company: Company,
        observed_at: datetime,
    ) -> SourceCollectionResult: ...


class JobSourceProvider(Protocol):
    def get_source(self, ats_type: ATSType) -> JobSource | None: ...
