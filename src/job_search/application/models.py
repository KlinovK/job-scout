from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from job_search.domain.classification import VacancyClassification
from job_search.domain.enums import ATSType, SourceHealthStatus
from job_search.domain.models import Company, JobVacancy, VacancyObservation


class CollectionCoverage(StrEnum):
    FULL_BOARD = "full_board"
    FILTERED_SUBSET = "filtered_subset"
    ROLLING_WINDOW = "rolling_window"


@dataclass(frozen=True, slots=True)
class SourceCollectionResult:
    vacancies: tuple[JobVacancy, ...]
    coverage: CollectionCoverage
    complete: bool
    raw_count: int
    malformed_count: int
    pagination_exhausted: bool

    def __post_init__(self) -> None:
        if self.raw_count < 0:
            raise ValueError("raw_count must not be negative")
        if self.malformed_count < 0:
            raise ValueError("malformed_count must not be negative")
        if self.malformed_count > self.raw_count:
            raise ValueError("malformed_count must not exceed raw_count")
        if len(self.vacancies) + self.malformed_count > self.raw_count:
            raise ValueError(
                "vacancies and malformed records must not exceed raw_count"
            )
        if self.complete and self.malformed_count:
            raise ValueError("a complete result cannot contain malformed records")
        if self.complete and not self.pagination_exhausted:
            raise ValueError("a complete result must exhaust pagination")

    @property
    def reconciliation_eligible(self) -> bool:
        return (
            self.coverage is CollectionCoverage.FULL_BOARD
            and self.complete
            and self.malformed_count == 0
            and self.pagination_exhausted
        )


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
