from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from job_search.infrastructure.persistence.sqlalchemy.base import Base, UTCDateTime


class CompanyRecord(Base):
    __tablename__ = "companies"
    __table_args__ = (
        UniqueConstraint(
            "ats_type",
            "ats_identifier",
            name="uq_companies_ats_identity",
        ),
        CheckConstraint("priority >= 0", name="ck_companies_priority_nonnegative"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    country: Mapped[str | None] = mapped_column(String(120))
    careers_url: Mapped[str] = mapped_column(Text, nullable=False)
    ats_type: Mapped[str] = mapped_column(String(32), nullable=False)
    ats_identifier: Mapped[str] = mapped_column(String(255), nullable=False)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    provenance: Mapped[str] = mapped_column(
        String(32), nullable=False, default="manual"
    )
    source_verified_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_success_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_job_count: Mapped[int | None] = mapped_column(Integer)
    last_status: Mapped[str | None] = mapped_column(String(32))
    last_error_category: Mapped[str | None] = mapped_column(String(120))
    last_complete_snapshot_at: Mapped[datetime | None] = mapped_column(UTCDateTime())

    vacancies: Mapped[list["JobVacancyRecord"]] = relationship(
        back_populates="company",
        cascade="all, delete-orphan",
    )


class JobVacancyRecord(Base):
    __tablename__ = "job_vacancies"
    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "source",
            "source_job_id",
            name="uq_job_vacancies_company_source_identity",
        ),
        CheckConstraint(
            "remote_policy IN ('unknown', 'remote', 'hybrid', 'onsite')",
            name="ck_job_vacancies_remote_policy",
        ),
        CheckConstraint(
            "status IN ('active', 'closed')",
            name="ck_job_vacancies_status",
        ),
        Index("ix_job_vacancies_company_id", "company_id"),
        Index("ix_job_vacancies_canonical_url", "canonical_url"),
        Index("ix_job_vacancies_status", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    company_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    employer_name: Mapped[str | None] = mapped_column(String(255))
    canonical_url: Mapped[str | None] = mapped_column(Text)
    original_source: Mapped[str | None] = mapped_column(String(32))
    salary: Mapped[str | None] = mapped_column(String(255))
    employment: Mapped[str | None] = mapped_column(String(255))
    experience: Mapped[str | None] = mapped_column(String(255))
    company_location: Mapped[str | None] = mapped_column(String(255))
    work_location: Mapped[str | None] = mapped_column(String(500))
    remote_policy: Mapped[str] = mapped_column(String(32), nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    source_job_id: Mapped[str] = mapped_column(String(255), nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    first_seen_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())

    company: Mapped[CompanyRecord] = relationship(back_populates="vacancies")


class VacancyObservationRecord(Base):
    __tablename__ = "vacancy_observations"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'closed')",
            name="ck_vacancy_observations_status",
        ),
        CheckConstraint(
            "missing_complete_snapshots >= 0",
            name="ck_vacancy_observations_missing_nonnegative",
        ),
        Index(
            "uq_vacancy_observations_owned_source_identity",
            "source_company_id",
            "discovered_via",
            "source_job_id",
            unique=True,
            sqlite_where=text("source_company_id IS NOT NULL"),
        ),
        Index(
            "uq_vacancy_observations_unowned_source_identity",
            "discovered_via",
            "source_job_id",
            unique=True,
            sqlite_where=text("source_company_id IS NULL"),
        ),
        Index("ix_vacancy_observations_vacancy_id", "vacancy_id"),
        Index("ix_vacancy_observations_canonical_url", "canonical_url"),
        Index("ix_vacancy_observations_original_reference", "original_reference"),
        Index(
            "ix_vacancy_observations_company_reconciliation",
            "source_company_id",
            "discovered_via",
            "status",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    vacancy_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("job_vacancies.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_company_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("companies.id", ondelete="SET NULL"),
    )
    discovered_via: Mapped[str] = mapped_column(String(32), nullable=False)
    source_job_id: Mapped[str] = mapped_column(String(255), nullable=False)
    original_source: Mapped[str | None] = mapped_column(String(32))
    observation_url: Mapped[str] = mapped_column(Text, nullable=False)
    canonical_url: Mapped[str | None] = mapped_column(Text)
    original_reference: Mapped[str | None] = mapped_column(String(512))
    employer_name: Mapped[str | None] = mapped_column(String(255))
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    work_location: Mapped[str | None] = mapped_column(String(500))
    first_seen_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="active",
    )
    closed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    missing_complete_snapshots: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )


class VacancyClassificationRecord(Base):
    __tablename__ = "vacancy_classifications"
    __table_args__ = (
        CheckConstraint(
            "decision IN ('match', 'possible_match', 'reject')",
            name="ck_vacancy_classifications_decision",
        ),
        CheckConstraint(
            "role_category IN "
            "('ios', 'swift', 'apple_platforms', 'ios_sdk', "
            "'mobile_ios', 'mixed_mobile', 'other')",
            name="ck_vacancy_classifications_role_category",
        ),
        CheckConstraint(
            "seniority IN "
            "('middle', 'senior', 'lead', 'junior', 'intern', "
            "'staff', 'principal', 'unknown')",
            name="ck_vacancy_classifications_seniority",
        ),
        CheckConstraint(
            "ios_relevance IN ('strong', 'substantial', 'weak', 'none')",
            name="ck_vacancy_classifications_ios_relevance",
        ),
        CheckConstraint(
            "work_mode IN ('remote', 'hybrid', 'onsite', 'unknown')",
            name="ck_vacancy_classifications_work_mode",
        ),
        CheckConstraint(
            "relocation IN ('available', 'unavailable', 'unknown')",
            name="ck_vacancy_classifications_relocation",
        ),
        CheckConstraint(
            "geography IN "
            "('worldwide', 'emea', 'europe', 'eu_only', 'us_only', "
            "'uk_only', 'country_only', 'unknown')",
            name="ck_vacancy_classifications_geography",
        ),
        Index(
            "ix_vacancy_classifications_version_decision",
            "classifier_version",
            "decision",
        ),
    )

    vacancy_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("job_vacancies.id", ondelete="CASCADE"),
        primary_key=True,
    )
    classifier_version: Mapped[str] = mapped_column(
        String(64),
        primary_key=True,
    )
    decision: Mapped[str] = mapped_column(String(32), nullable=False)
    role_category: Mapped[str] = mapped_column(String(32), nullable=False)
    seniority: Mapped[str] = mapped_column(String(32), nullable=False)
    ios_relevance: Mapped[str] = mapped_column(String(32), nullable=False)
    work_mode: Mapped[str] = mapped_column(String(32), nullable=False)
    relocation: Mapped[str] = mapped_column(String(32), nullable=False)
    geography: Mapped[str] = mapped_column(String(32), nullable=False)
    positive_signals: Mapped[str] = mapped_column(Text, nullable=False)
    warnings: Mapped[str] = mapped_column(Text, nullable=False)
    rejection_reasons: Mapped[str] = mapped_column(Text, nullable=False)
    classified_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
