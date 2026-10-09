from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from job_search.domain.enums import (
    ATSType,
    RegistryProvenance,
    RemotePolicy,
    SourceHealthStatus,
    VacancySource,
    VacancyStatus,
)


def utc_now() -> datetime:
    return datetime.now(UTC)


def _require_utc(value: datetime | None, field_name: str) -> None:
    if value is None:
        return
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    if value.utcoffset() != UTC.utcoffset(value):
        raise ValueError(f"{field_name} must be in UTC")


@dataclass(frozen=True, slots=True)
class Company:
    id: UUID
    name: str
    country: str | None
    careers_url: str
    ats_type: ATSType
    ats_identifier: str
    priority: int
    enabled: bool
    last_checked_at: datetime | None
    created_at: datetime
    updated_at: datetime
    provenance: RegistryProvenance = RegistryProvenance.MANUAL
    source_verified_at: datetime | None = None
    last_success_at: datetime | None = None
    last_job_count: int | None = None
    last_status: SourceHealthStatus | None = None
    last_error_category: str | None = None
    last_complete_snapshot_at: datetime | None = None

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("company name must not be empty")
        if not self.careers_url.strip():
            raise ValueError("careers_url must not be empty")
        if not self.ats_identifier.strip():
            raise ValueError("ats_identifier must not be empty")
        _require_utc(self.last_checked_at, "last_checked_at")
        _require_utc(self.created_at, "created_at")
        _require_utc(self.updated_at, "updated_at")
        _require_utc(self.source_verified_at, "source_verified_at")
        _require_utc(self.last_success_at, "last_success_at")
        _require_utc(self.last_complete_snapshot_at, "last_complete_snapshot_at")
        if self.last_job_count is not None and self.last_job_count < 0:
            raise ValueError("last_job_count must not be negative")


@dataclass(frozen=True, slots=True)
class JobVacancy:
    id: UUID
    company_id: UUID
    title: str
    description: str
    company_location: str | None
    work_location: str | None
    remote_policy: RemotePolicy
    url: str
    source: VacancySource
    source_job_id: str
    published_at: datetime | None
    first_seen_at: datetime
    last_seen_at: datetime
    status: VacancyStatus
    employer_name: str | None = None
    canonical_url: str | None = None
    original_source: VacancySource | None = None
    salary: str | None = None
    employment: str | None = None
    experience: str | None = None
    closed_at: datetime | None = None

    def __post_init__(self) -> None:
        if not self.title.strip():
            raise ValueError("vacancy title must not be empty")
        if not self.url.strip():
            raise ValueError("vacancy URL must not be empty")
        if not self.source_job_id.strip():
            raise ValueError("source_job_id must not be empty")
        if self.employer_name is not None and not self.employer_name.strip():
            raise ValueError("employer_name must not be empty when provided")
        for field_name, value in (
            ("canonical_url", self.canonical_url),
            ("salary", self.salary),
            ("employment", self.employment),
            ("experience", self.experience),
        ):
            if value is not None and not value.strip():
                raise ValueError(f"{field_name} must not be empty when provided")
        _require_utc(self.published_at, "published_at")
        _require_utc(self.first_seen_at, "first_seen_at")
        _require_utc(self.last_seen_at, "last_seen_at")
        _require_utc(self.closed_at, "closed_at")


@dataclass(frozen=True, slots=True)
class VacancyObservation:
    id: UUID
    vacancy_id: UUID
    discovered_via: VacancySource
    source_job_id: str
    original_source: VacancySource | None
    observation_url: str
    canonical_url: str | None
    original_reference: str | None
    employer_name: str | None
    title: str
    work_location: str | None
    first_seen_at: datetime
    last_seen_at: datetime
    source_company_id: UUID | None = None
    status: VacancyStatus = VacancyStatus.ACTIVE
    closed_at: datetime | None = None
    missing_complete_snapshots: int = 0

    def __post_init__(self) -> None:
        if not self.source_job_id.strip():
            raise ValueError("source_job_id must not be empty")
        if not self.observation_url.strip():
            raise ValueError("observation_url must not be empty")
        if not self.title.strip():
            raise ValueError("title must not be empty")
        _require_utc(self.first_seen_at, "first_seen_at")
        _require_utc(self.last_seen_at, "last_seen_at")
        _require_utc(self.closed_at, "closed_at")
        if self.missing_complete_snapshots < 0:
            raise ValueError("missing_complete_snapshots must not be negative")
