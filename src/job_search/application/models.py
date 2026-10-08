from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from job_search.domain.classification import VacancyClassification
from job_search.domain.enums import ATSType, SourceHealthStatus
from job_search.domain.models import Company, JobVacancy, VacancyObservation


@dataclass(frozen=True, slots=True)
class VacancyUpsertResult:
    created: int
    updated: int
    observations_created: int = 0
    cross_source_duplicates_collapsed: int = 0


@dataclass(frozen=True, slots=True)
class CompanySeedResult:
    created: int
    updated: int


@dataclass(frozen=True, slots=True)
class CollectionFailure:
    company_id: UUID
    company_name: str
    ats_type: ATSType
    error_category: str
    message: str


@dataclass(frozen=True, slots=True)
class CollectionSummary:
    companies_checked: int
    jobs_fetched: int
    new_jobs: int
    updated_jobs: int
    failures: tuple[CollectionFailure, ...]
    provider_summaries: tuple["ProviderCollectionSummary", ...]
    healthy: int
    empty: int
    invalid_configuration: int

    @property
    def failure_count(self) -> int:
        return len(self.failures)


@dataclass(frozen=True, slots=True)
class ProviderCollectionSummary:
    ats_type: ATSType
    checked: int
    healthy: int
    empty: int
    failed: int
    invalid_configuration: int
    jobs_fetched: int
    new_jobs: int
    updated_jobs: int
    observations_created: int = 0
    cross_source_duplicates_collapsed: int = 0


@dataclass(frozen=True, slots=True)
class SourceHealthUpdate:
    checked_at: datetime
    status: SourceHealthStatus
    job_count: int | None
    error_category: str | None


@dataclass(frozen=True, slots=True)
class ClassificationUpsertResult:
    created: int
    updated: int


@dataclass(frozen=True, slots=True)
class ClassificationSummary:
    processed: int
    matches: int
    possible_matches: int
    rejected: int
    created: int
    updated: int
    classifier_version: str


@dataclass(frozen=True, slots=True)
class CandidateVacancy:
    company: Company
    vacancy: JobVacancy
    classification: VacancyClassification
    observations: tuple[VacancyObservation, ...] = ()


@dataclass(frozen=True, slots=True)
class DeduplicationStats:
    observations: int
    canonical_vacancies: int
    cross_source_duplicates_collapsed: int
    possible_duplicate_groups: int
