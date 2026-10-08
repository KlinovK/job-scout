from collections.abc import Sequence
from datetime import datetime
from enum import StrEnum
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from job_search.application.models import (
    ClassificationUpsertResult,
    DeduplicationStats,
    SourceHealthUpdate,
    VacancyUpsertResult,
)
from job_search.domain.canonicalization import (
    extract_original_reference,
    normalize_canonical_url,
    normalize_comparison_text,
    source_trust_rank,
)
from job_search.domain.classification import (
    ClassificationDecision,
    GeographyScope,
    IOSRelevance,
    PositiveSignal,
    RejectionReason,
    RelocationStatus,
    RoleCategory,
    Seniority,
    VacancyClassification,
    WarningCode,
    WorkMode,
)
from job_search.domain.enums import (
    ATSType,
    RegistryProvenance,
    RemotePolicy,
    SourceHealthStatus,
    VacancySource,
    VacancyStatus,
)
from job_search.domain.models import Company, JobVacancy, VacancyObservation
from job_search.infrastructure.persistence.sqlalchemy.models import (
    CompanyRecord,
    JobVacancyRecord,
    VacancyClassificationRecord,
    VacancyObservationRecord,
)


def _encode_enum_values(values: Sequence[StrEnum]) -> str:
    return ",".join(value.value for value in values)


def _decode_enum_values[T: StrEnum](value: str, enum_type: type[T]) -> tuple[T, ...]:
    return tuple(enum_type(item) for item in value.split(",") if item)


def _company_to_record(company: Company) -> CompanyRecord:
    return CompanyRecord(
        id=str(company.id),
        name=company.name,
        country=company.country,
        careers_url=company.careers_url,
        ats_type=company.ats_type.value,
        ats_identifier=company.ats_identifier,
        priority=company.priority,
        enabled=company.enabled,
        last_checked_at=company.last_checked_at,
        created_at=company.created_at,
        updated_at=company.updated_at,
        provenance=company.provenance.value,
        source_verified_at=company.source_verified_at,
        last_success_at=company.last_success_at,
        last_job_count=company.last_job_count,
        last_status=company.last_status.value if company.last_status else None,
        last_error_category=company.last_error_category,
    )


def _company_to_domain(record: CompanyRecord) -> Company:
    return Company(
        id=UUID(record.id),
        name=record.name,
        country=record.country,
        careers_url=record.careers_url,
        ats_type=ATSType(record.ats_type),
        ats_identifier=record.ats_identifier,
        priority=record.priority,
        enabled=record.enabled,
        last_checked_at=record.last_checked_at,
        created_at=record.created_at,
        updated_at=record.updated_at,
        provenance=RegistryProvenance(record.provenance),
        source_verified_at=record.source_verified_at,
        last_success_at=record.last_success_at,
        last_job_count=record.last_job_count,
        last_status=(
            SourceHealthStatus(record.last_status) if record.last_status else None
        ),
        last_error_category=record.last_error_category,
    )


def _vacancy_to_record(vacancy: JobVacancy) -> JobVacancyRecord:
    canonical_url = vacancy.canonical_url or normalize_canonical_url(vacancy.url)
    return JobVacancyRecord(
        id=str(vacancy.id),
        company_id=str(vacancy.company_id),
        title=vacancy.title,
        description=vacancy.description,
        employer_name=vacancy.employer_name,
        canonical_url=canonical_url,
        original_source=(vacancy.original_source or vacancy.source).value,
        salary=vacancy.salary,
        employment=vacancy.employment,
        experience=vacancy.experience,
        company_location=vacancy.company_location,
        work_location=vacancy.work_location,
        remote_policy=vacancy.remote_policy.value,
        url=vacancy.url,
        source=vacancy.source.value,
        source_job_id=vacancy.source_job_id,
        published_at=vacancy.published_at,
        first_seen_at=vacancy.first_seen_at,
        last_seen_at=vacancy.last_seen_at,
        status=vacancy.status.value,
    )


def _vacancy_to_domain(record: JobVacancyRecord) -> JobVacancy:
    return JobVacancy(
        id=UUID(record.id),
        company_id=UUID(record.company_id),
        title=record.title,
        description=record.description,
        company_location=record.company_location,
        work_location=record.work_location,
        remote_policy=RemotePolicy(record.remote_policy),
        url=record.url,
        source=VacancySource(record.source),
        source_job_id=record.source_job_id,
        published_at=record.published_at,
        first_seen_at=record.first_seen_at,
        last_seen_at=record.last_seen_at,
        status=VacancyStatus(record.status),
        employer_name=record.employer_name,
        canonical_url=record.canonical_url,
        original_source=(
            VacancySource(record.original_source) if record.original_source else None
        ),
        salary=record.salary,
        employment=record.employment,
        experience=record.experience,
    )


def _observation_to_domain(record: VacancyObservationRecord) -> VacancyObservation:
    return VacancyObservation(
        id=UUID(record.id),
        vacancy_id=UUID(record.vacancy_id),
        discovered_via=VacancySource(record.discovered_via),
        source_job_id=record.source_job_id,
        original_source=(
            VacancySource(record.original_source) if record.original_source else None
        ),
        observation_url=record.observation_url,
        canonical_url=record.canonical_url,
        original_reference=record.original_reference,
        employer_name=record.employer_name,
        title=record.title,
        work_location=record.work_location,
        first_seen_at=record.first_seen_at,
        last_seen_at=record.last_seen_at,
    )


def _new_observation(
    vacancy: JobVacancy,
    canonical_vacancy_id: str,
    canonical_url: str,
) -> VacancyObservationRecord:
    identity = f"{vacancy.source.value}:{vacancy.source_job_id}"
    return VacancyObservationRecord(
        id=str(uuid5(NAMESPACE_URL, f"vacancy-observation:{identity}")),
        vacancy_id=canonical_vacancy_id,
        discovered_via=vacancy.source.value,
        source_job_id=vacancy.source_job_id,
        original_source=(vacancy.original_source or vacancy.source).value,
        observation_url=vacancy.url,
        canonical_url=canonical_url,
        original_reference=extract_original_reference(vacancy.url),
        employer_name=vacancy.employer_name,
        title=vacancy.title,
        work_location=vacancy.work_location,
        first_seen_at=vacancy.first_seen_at,
        last_seen_at=vacancy.last_seen_at,
    )


def _update_canonical_record(
    record: JobVacancyRecord,
    vacancy: JobVacancy,
    canonical_url: str,
) -> None:
    record.company_id = str(vacancy.company_id)
    record.title = vacancy.title
    record.description = vacancy.description
    record.employer_name = vacancy.employer_name
    record.canonical_url = canonical_url
    record.original_source = (vacancy.original_source or vacancy.source).value
    record.salary = vacancy.salary
    record.employment = vacancy.employment
    record.experience = vacancy.experience
    record.company_location = vacancy.company_location
    record.work_location = vacancy.work_location
    record.remote_policy = vacancy.remote_policy.value
    record.url = vacancy.url
    record.source = vacancy.source.value
    record.source_job_id = vacancy.source_job_id
    record.published_at = vacancy.published_at
    record.last_seen_at = vacancy.last_seen_at
    record.status = vacancy.status.value


def _classification_to_record(
    classification: VacancyClassification,
) -> VacancyClassificationRecord:
    return VacancyClassificationRecord(
        vacancy_id=str(classification.vacancy_id),
        classifier_version=classification.classifier_version,
        decision=classification.decision.value,
        role_category=classification.role_category.value,
        seniority=classification.seniority.value,
        ios_relevance=classification.ios_relevance.value,
        work_mode=classification.work_mode.value,
        relocation=classification.relocation.value,
        geography=classification.geography.value,
        positive_signals=_encode_enum_values(classification.positive_signals),
        warnings=_encode_enum_values(classification.warnings),
        rejection_reasons=_encode_enum_values(classification.rejection_reasons),
        classified_at=classification.classified_at,
    )


def _classification_to_domain(
    record: VacancyClassificationRecord,
) -> VacancyClassification:
    return VacancyClassification(
        vacancy_id=UUID(record.vacancy_id),
        decision=ClassificationDecision(record.decision),
        role_category=RoleCategory(record.role_category),
        seniority=Seniority(record.seniority),
        ios_relevance=IOSRelevance(record.ios_relevance),
        work_mode=WorkMode(record.work_mode),
        relocation=RelocationStatus(record.relocation),
        geography=GeographyScope(record.geography),
        positive_signals=_decode_enum_values(record.positive_signals, PositiveSignal),
        warnings=_decode_enum_values(record.warnings, WarningCode),
        rejection_reasons=_decode_enum_values(
            record.rejection_reasons,
            RejectionReason,
        ),
        classified_at=record.classified_at,
        classifier_version=record.classifier_version,
    )


class SQLAlchemyCompanyRepository:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self._session_factory = session_factory

    async def add(self, company: Company) -> None:
        async with self._session_factory() as session, session.begin():
            session.add(_company_to_record(company))

    async def get(self, company_id: UUID) -> Company | None:
        async with self._session_factory() as session:
            record = await session.get(CompanyRecord, str(company_id))
            return _company_to_domain(record) if record else None

    async def list_enabled(self) -> Sequence[Company]:
        async with self._session_factory() as session:
            statement = (
                select(CompanyRecord)
                .where(CompanyRecord.enabled.is_(True))
                .order_by(CompanyRecord.priority.desc(), CompanyRecord.name)
            )
            records = (await session.scalars(statement)).all()
            return tuple(_company_to_domain(record) for record in records)

    async def list_all(self) -> Sequence[Company]:
        async with self._session_factory() as session:
            statement = select(CompanyRecord).order_by(
                CompanyRecord.priority.desc(),
                CompanyRecord.name,
            )
            records = (await session.scalars(statement)).all()
            return tuple(_company_to_domain(record) for record in records)

    async def list_for_sources(
        self,
        sources: Sequence[ATSType],
        *,
        include_disabled: bool = False,
    ) -> Sequence[Company]:
        if not sources:
            return ()
        async with self._session_factory() as session:
            statement = select(CompanyRecord).where(
                CompanyRecord.ats_type.in_(source.value for source in sources)
            )
            if not include_disabled:
                statement = statement.where(CompanyRecord.enabled.is_(True))
            statement = statement.order_by(
                CompanyRecord.priority.desc(), CompanyRecord.name
            )
            records = (await session.scalars(statement)).all()
            return tuple(_company_to_domain(record) for record in records)

    async def upsert_by_ats_identity(self, company: Company) -> bool:
        async with self._session_factory() as session, session.begin():
            statement = select(CompanyRecord).where(
                CompanyRecord.ats_type == company.ats_type.value,
                CompanyRecord.ats_identifier == company.ats_identifier,
            )
            record = await session.scalar(statement)
            if record is None:
                session.add(_company_to_record(company))
                return True

            record.name = company.name
            record.country = company.country
            record.careers_url = company.careers_url
            record.priority = company.priority
            record.enabled = company.enabled
            record.ats_identifier = company.ats_identifier
            record.provenance = company.provenance.value
            record.source_verified_at = company.source_verified_at
            record.updated_at = company.updated_at
            return False

    async def mark_checked(self, company_id: UUID, checked_at: datetime) -> None:
        async with self._session_factory() as session, session.begin():
            record = await session.get(CompanyRecord, str(company_id))
            if record is None:
                raise LookupError(f"Unknown company: {company_id}")
            record.last_checked_at = checked_at
            record.updated_at = checked_at

    async def update_health(
        self,
        company_id: UUID,
        update: SourceHealthUpdate,
    ) -> None:
        async with self._session_factory() as session, session.begin():
            record = await session.get(CompanyRecord, str(company_id))
            if record is None:
                raise LookupError(f"Unknown company: {company_id}")
            record.last_checked_at = update.checked_at
            record.last_status = update.status.value
            record.last_error_category = update.error_category
            record.updated_at = update.checked_at
            if update.status in (
                SourceHealthStatus.HEALTHY,
                SourceHealthStatus.EMPTY,
            ):
                record.last_success_at = update.checked_at
                record.last_job_count = update.job_count


class SQLAlchemyVacancyRepository:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self._session_factory = session_factory

    async def get(self, vacancy_id: UUID) -> JobVacancy | None:
        async with self._session_factory() as session:
            record = await session.get(JobVacancyRecord, str(vacancy_id))
            return _vacancy_to_domain(record) if record else None

    async def get_by_source_identity(
        self,
        source: VacancySource,
        source_job_id: str,
    ) -> JobVacancy | None:
        async with self._session_factory() as session:
            statement = (
                select(JobVacancyRecord)
                .join(
                    VacancyObservationRecord,
                    VacancyObservationRecord.vacancy_id == JobVacancyRecord.id,
                )
                .where(
                    VacancyObservationRecord.discovered_via == source.value,
                    VacancyObservationRecord.source_job_id == source_job_id,
                )
            )
            record = await session.scalar(statement)
            return _vacancy_to_domain(record) if record else None

    async def upsert_many(
        self,
        vacancies: Sequence[JobVacancy],
    ) -> VacancyUpsertResult:
        created = 0
        updated = 0
        observations_created = 0
        cross_source_duplicates_collapsed = 0
        async with self._session_factory() as session, session.begin():
            for vacancy in vacancies:
                canonical_url = vacancy.canonical_url or normalize_canonical_url(
                    vacancy.url
                )
                source_statement = select(VacancyObservationRecord).where(
                    VacancyObservationRecord.discovered_via == vacancy.source.value,
                    VacancyObservationRecord.source_job_id == vacancy.source_job_id,
                )
                observation = await session.scalar(source_statement)
                if observation is not None:
                    record = await session.get(
                        JobVacancyRecord,
                        observation.vacancy_id,
                    )
                    if record is None:
                        raise LookupError(
                            f"Observation points to missing vacancy: {observation.id}"
                        )
                    observation.observation_url = vacancy.url
                    observation.canonical_url = canonical_url
                    observation.original_reference = extract_original_reference(
                        vacancy.url
                    )
                    observation.original_source = (
                        vacancy.original_source or vacancy.source
                    ).value
                    observation.employer_name = vacancy.employer_name
                    observation.title = vacancy.title
                    observation.work_location = vacancy.work_location
                    observation.last_seen_at = vacancy.last_seen_at
                    if (
                        record.source == vacancy.source.value
                        and record.source_job_id == vacancy.source_job_id
                    ):
                        _update_canonical_record(record, vacancy, canonical_url)
                    updated += 1
                    continue

                original_reference = extract_original_reference(vacancy.url)
                identity_conditions = [
                    VacancyObservationRecord.canonical_url == canonical_url
                ]
                if original_reference is not None:
                    identity_conditions.append(
                        VacancyObservationRecord.original_reference
                        == original_reference
                    )
                matching_observation = await session.scalar(
                    select(VacancyObservationRecord)
                    .where(or_(*identity_conditions))
                    .order_by(VacancyObservationRecord.first_seen_at)
                )
                if matching_observation is None:
                    record = _vacancy_to_record(vacancy)
                    session.add(record)
                    await session.flush()
                    session.add(_new_observation(vacancy, record.id, canonical_url))
                    created += 1
                    observations_created += 1
                    continue

                record = await session.get(
                    JobVacancyRecord,
                    matching_observation.vacancy_id,
                )
                if record is None:
                    raise LookupError(
                        "Canonical observation points to a missing vacancy"
                    )
                session.add(_new_observation(vacancy, record.id, canonical_url))
                observations_created += 1
                cross_source_duplicates_collapsed += int(
                    matching_observation.discovered_via != vacancy.source.value
                )
                if source_trust_rank(vacancy.source) > source_trust_rank(
                    VacancySource(record.source)
                ):
                    _update_canonical_record(record, vacancy, canonical_url)
                updated += 1

        return VacancyUpsertResult(
            created=created,
            updated=updated,
            observations_created=observations_created,
            cross_source_duplicates_collapsed=cross_source_duplicates_collapsed,
        )

    async def list_active(self) -> Sequence[JobVacancy]:
        async with self._session_factory() as session:
            statement = (
                select(JobVacancyRecord)
                .where(JobVacancyRecord.status == VacancyStatus.ACTIVE.value)
                .order_by(JobVacancyRecord.id)
            )
            records = (await session.scalars(statement)).all()
            return tuple(_vacancy_to_domain(record) for record in records)

    async def list_observations(
        self,
        vacancy_id: UUID,
    ) -> Sequence[VacancyObservation]:
        async with self._session_factory() as session:
            statement = (
                select(VacancyObservationRecord)
                .where(VacancyObservationRecord.vacancy_id == str(vacancy_id))
                .order_by(
                    VacancyObservationRecord.first_seen_at,
                    VacancyObservationRecord.discovered_via,
                )
            )
            records = (await session.scalars(statement)).all()
            return tuple(_observation_to_domain(record) for record in records)

    async def deduplication_stats(self) -> DeduplicationStats:
        async with self._session_factory() as session:
            observations = int(
                await session.scalar(
                    select(func.count()).select_from(VacancyObservationRecord)
                )
                or 0
            )
            canonical_vacancies = int(
                await session.scalar(select(func.count()).select_from(JobVacancyRecord))
                or 0
            )
            rows = (
                await session.execute(
                    select(
                        VacancyObservationRecord.vacancy_id,
                        func.count(
                            func.distinct(VacancyObservationRecord.discovered_via)
                        ),
                    ).group_by(VacancyObservationRecord.vacancy_id)
                )
            ).all()
            cross_source = sum(max(0, int(count) - 1) for _, count in rows)
            active = (
                await session.scalars(
                    select(JobVacancyRecord).where(
                        JobVacancyRecord.status == VacancyStatus.ACTIVE.value
                    )
                )
            ).all()
            ambiguous: dict[tuple[str | None, str | None, str | None], set[str]] = {}
            for record in active:
                key = (
                    normalize_comparison_text(record.employer_name),
                    normalize_comparison_text(record.title),
                    normalize_comparison_text(record.work_location),
                )
                if key[0] is None or key[1] is None:
                    continue
                ambiguous.setdefault(key, set()).add(record.canonical_url or record.url)
            possible_groups = sum(len(urls) > 1 for urls in ambiguous.values())
            return DeduplicationStats(
                observations=observations,
                canonical_vacancies=canonical_vacancies,
                cross_source_duplicates_collapsed=cross_source,
                possible_duplicate_groups=possible_groups,
            )


class SQLAlchemyClassificationRepository:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self._session_factory = session_factory

    async def upsert_many(
        self,
        classifications: Sequence[VacancyClassification],
    ) -> ClassificationUpsertResult:
        created = 0
        updated = 0
        async with self._session_factory() as session, session.begin():
            for classification in classifications:
                record = await session.get(
                    VacancyClassificationRecord,
                    (str(classification.vacancy_id), classification.classifier_version),
                )
                if record is None:
                    session.add(_classification_to_record(classification))
                    created += 1
                    continue
                record.decision = classification.decision.value
                record.role_category = classification.role_category.value
                record.seniority = classification.seniority.value
                record.ios_relevance = classification.ios_relevance.value
                record.work_mode = classification.work_mode.value
                record.relocation = classification.relocation.value
                record.geography = classification.geography.value
                record.positive_signals = _encode_enum_values(
                    classification.positive_signals
                )
                record.warnings = _encode_enum_values(classification.warnings)
                record.rejection_reasons = _encode_enum_values(
                    classification.rejection_reasons
                )
                record.classified_at = classification.classified_at
                updated += 1
        return ClassificationUpsertResult(created=created, updated=updated)

    async def get(
        self,
        vacancy_id: UUID,
        classifier_version: str,
    ) -> VacancyClassification | None:
        async with self._session_factory() as session:
            record = await session.get(
                VacancyClassificationRecord,
                (str(vacancy_id), classifier_version),
            )
            return _classification_to_domain(record) if record else None

    async def list_for_version(
        self,
        classifier_version: str,
    ) -> Sequence[VacancyClassification]:
        async with self._session_factory() as session:
            statement = (
                select(VacancyClassificationRecord)
                .where(
                    VacancyClassificationRecord.classifier_version == classifier_version
                )
                .order_by(VacancyClassificationRecord.vacancy_id)
            )
            records = (await session.scalars(statement)).all()
            return tuple(_classification_to_domain(record) for record in records)

    async def list_by_decisions(
        self,
        classifier_version: str,
        decisions: Sequence[ClassificationDecision],
        *,
        limit: int | None = None,
    ) -> Sequence[VacancyClassification]:
        async with self._session_factory() as session:
            statement = (
                select(VacancyClassificationRecord)
                .where(
                    VacancyClassificationRecord.classifier_version
                    == classifier_version,
                    VacancyClassificationRecord.decision.in_(
                        decision.value for decision in decisions
                    ),
                )
                .order_by(
                    VacancyClassificationRecord.decision,
                    VacancyClassificationRecord.classified_at.desc(),
                )
            )
            if limit is not None:
                statement = statement.limit(limit)
            records = (await session.scalars(statement)).all()
            return tuple(_classification_to_domain(record) for record in records)
