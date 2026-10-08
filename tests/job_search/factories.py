from datetime import UTC, datetime
from uuid import UUID, uuid4

from job_search.domain.enums import (
    ATSType,
    RemotePolicy,
    VacancySource,
    VacancyStatus,
)
from job_search.domain.models import Company, JobVacancy

NOW = datetime(2026, 9, 17, 8, 0, tzinfo=UTC)
LATER = datetime(2026, 9, 17, 9, 0, tzinfo=UTC)


def make_company(
    *,
    company_id: UUID | None = None,
    name: str = "Example",
    ats_type: ATSType = ATSType.GREENHOUSE,
    ats_identifier: str = "example",
    enabled: bool = True,
    priority: int = 10,
) -> Company:
    return Company(
        id=company_id or uuid4(),
        name=name,
        country="Netherlands",
        careers_url=f"https://example.com/{ats_identifier}/careers",
        ats_type=ats_type,
        ats_identifier=ats_identifier,
        priority=priority,
        enabled=enabled,
        last_checked_at=None,
        created_at=NOW,
        updated_at=NOW,
    )


def make_vacancy(
    company_id: UUID,
    *,
    vacancy_id: UUID | None = None,
    source_job_id: str = "job-1",
    title: str = "iOS Engineer",
    description: str = "Build iOS applications with Swift.",
    work_location: str | None = "Remote",
    remote_policy: RemotePolicy = RemotePolicy.REMOTE,
    first_seen_at: datetime = NOW,
    last_seen_at: datetime = NOW,
    employer_name: str | None = None,
    source: VacancySource = VacancySource.GREENHOUSE,
    url: str | None = None,
    original_source: VacancySource | None = None,
) -> JobVacancy:
    return JobVacancy(
        id=vacancy_id or uuid4(),
        company_id=company_id,
        title=title,
        description=description,
        company_location="Netherlands",
        work_location=work_location,
        remote_policy=remote_policy,
        url=url or f"https://example.com/jobs/{source_job_id}",
        source=source,
        source_job_id=source_job_id,
        published_at=None,
        first_seen_at=first_seen_at,
        last_seen_at=last_seen_at,
        status=VacancyStatus.ACTIVE,
        employer_name=employer_name,
        original_source=original_source,
    )
