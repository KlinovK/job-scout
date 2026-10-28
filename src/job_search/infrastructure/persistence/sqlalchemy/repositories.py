from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import func, or_, select
from sqlalchemy import update as sqlalchemy_update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from job_search.application.models import (
    ClassificationUpsertResult,
    CollectionPersistenceResult,
    DeduplicationStats,
    SourceCollectionResult,
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
    VacancyClosureEvidence,
    VacancyLifecycleProvenance,
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

_MISSING_SNAPSHOTS_TO_CLOSE = 2
_LIFECYCLE_AGGREGATION_BATCH_SIZE = 400
_AUTHORITATIVE_CLOSURE_EVIDENCE = frozenset(
    {
        VacancyClosureEvidence.PROVIDER_EXPLICIT.value,
        VacancyClosureEvidence.AUTHORITATIVE_ABSENCE.value,
    }
)


@dataclass(frozen=True, slots=True)
class _VacancyUpsertOutcome:
    result: VacancyUpsertResult
    affected_vacancy_ids: frozenset[str]


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
        last_complete_snapshot_at=company.last_complete_snapshot_at,
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
        last_complete_snapshot_at=record.last_complete_snapshot_at,
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
        status=VacancyStatus.ACTIVE.value,
        closed_at=None,
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
        closed_at=record.closed_at,
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
        source_company_id=(
            UUID(record.source_company_id) if record.source_company_id else None
        ),
        status=VacancyStatus(record.status),
        closed_at=record.closed_at,
        missing_complete_snapshots=record.missing_complete_snapshots,
        closure_evidence=VacancyClosureEvidence(record.closure_evidence),
        lifecycle_provenance=VacancyLifecycleProvenance(record.lifecycle_provenance),
    )


def _new_observation(
    vacancy: JobVacancy,
    canonical_vacancy_id: str,
    canonical_url: str,
    observed_at: datetime | None = None,
    *,
    accepted_collection: bool,
) -> VacancyObservationRecord:
    identity = f"{vacancy.company_id}:{vacancy.source.value}:{vacancy.source_job_id}"
    provider_explicit = (
        accepted_collection
        and vacancy.status is VacancyStatus.CLOSED
        and vacancy.closure_evidence is VacancyClosureEvidence.PROVIDER_EXPLICIT
    )
    closure_evidence = (
        VacancyClosureEvidence.PROVIDER_EXPLICIT
        if provider_explicit
        else VacancyClosureEvidence.UNKNOWN
    )
    closed_at = None
    if vacancy.status is VacancyStatus.CLOSED:
        closed_at = vacancy.closed_at or (observed_at if provider_explicit else None)
    return VacancyObservationRecord(
        id=str(uuid5(NAMESPACE_URL, f"vacancy-observation:{identity}")),
        vacancy_id=canonical_vacancy_id,
        source_company_id=str(vacancy.company_id),
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
        status=vacancy.status.value,
        closed_at=closed_at,
        closure_evidence=closure_evidence.value,
        lifecycle_provenance=(
            VacancyLifecycleProvenance.ACCEPTED_COLLECTION.value
            if accepted_collection
            else VacancyLifecycleProvenance.GENERIC_UPSERT.value
        ),
        missing_complete_snapshots=0,
    )


def _refresh_observation_from_positive_row(
    observation: VacancyObservationRecord,
    vacancy: JobVacancy,
    observed_at: datetime | None,
    *,
    accepted_collection: bool,
) -> None:
    if accepted_collection:
        observation.lifecycle_provenance = (
            VacancyLifecycleProvenance.ACCEPTED_COLLECTION.value
        )
    if vacancy.status is VacancyStatus.ACTIVE:
        _mark_observation_present(observation)
        return

    provider_explicit = (
        accepted_collection
        and vacancy.closure_evidence is VacancyClosureEvidence.PROVIDER_EXPLICIT
    )
    if (
        observation.status == VacancyStatus.CLOSED.value
        and observation.closed_at is not None
        and observation.closure_evidence in _AUTHORITATIVE_CLOSURE_EVIDENCE
    ):
        observation.missing_complete_snapshots = 0
        return
    observation.status = VacancyStatus.CLOSED.value
    observation.closed_at = vacancy.closed_at or (
        observed_at if provider_explicit else None
    )
    observation.closure_evidence = (
        VacancyClosureEvidence.PROVIDER_EXPLICIT.value
        if provider_explicit
        else VacancyClosureEvidence.UNKNOWN.value
    )
    observation.missing_complete_snapshots = 0


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


async def _upsert_vacancies(
    session: AsyncSession,
    vacancies: Sequence[JobVacancy],
    *,
    observed_at: datetime | None,
    accepted_collection: bool,
) -> _VacancyUpsertOutcome:
    created = 0
    updated = 0
    observations_created = 0
    cross_source_duplicates_collapsed = 0
    affected_vacancy_ids: set[str] = set()
    for vacancy in vacancies:
        canonical_url = vacancy.canonical_url or normalize_canonical_url(vacancy.url)
        source_statement = select(VacancyObservationRecord).where(
            VacancyObservationRecord.source_company_id == str(vacancy.company_id),
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
            observation.original_reference = extract_original_reference(vacancy.url)
            observation.original_source = (
                vacancy.original_source or vacancy.source
            ).value
            observation.employer_name = vacancy.employer_name
            observation.title = vacancy.title
            observation.work_location = vacancy.work_location
            observation.last_seen_at = vacancy.last_seen_at
            if accepted_collection:
                _refresh_observation_from_positive_row(
                    observation,
                    vacancy,
                    observed_at,
                    accepted_collection=True,
                )
            affected_vacancy_ids.add(record.id)
            if (
                record.source == vacancy.source.value
                and record.source_job_id == vacancy.source_job_id
            ):
                _update_canonical_record(record, vacancy, canonical_url)
            updated += 1
            continue

        original_reference = extract_original_reference(vacancy.url)
        identity_conditions = [VacancyObservationRecord.canonical_url == canonical_url]
        if original_reference is not None:
            identity_conditions.append(
                VacancyObservationRecord.original_reference == original_reference
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
            session.add(
                _new_observation(
                    vacancy,
                    record.id,
                    canonical_url,
                    observed_at,
                    accepted_collection=accepted_collection,
                )
            )
            affected_vacancy_ids.add(record.id)
            created += 1
            observations_created += 1
            continue

        record = await session.get(
            JobVacancyRecord,
            matching_observation.vacancy_id,
        )
        if record is None:
            raise LookupError("Canonical observation points to a missing vacancy")
        session.add(
            _new_observation(
                vacancy,
                record.id,
                canonical_url,
                observed_at,
                accepted_collection=accepted_collection,
            )
        )
        affected_vacancy_ids.add(record.id)
        observations_created += 1
        cross_source_duplicates_collapsed += int(
            matching_observation.discovered_via != vacancy.source.value
        )
        if source_trust_rank(vacancy.source) > source_trust_rank(
            VacancySource(record.source)
        ):
            _update_canonical_record(record, vacancy, canonical_url)
        updated += 1

    return _VacancyUpsertOutcome(
        result=VacancyUpsertResult(
            created=created,
            updated=updated,
            observations_created=observations_created,
            cross_source_duplicates_collapsed=cross_source_duplicates_collapsed,
        ),
        affected_vacancy_ids=frozenset(affected_vacancy_ids),
    )


async def _company_exists(session: AsyncSession, company_id: UUID) -> bool:
    statement = select(CompanyRecord.id).where(CompanyRecord.id == str(company_id))
    return await session.scalar(statement) is not None


async def _claim_collection_success(
    session: AsyncSession,
    company_id: UUID,
    checked_at: datetime,
    *,
    complete_snapshot: bool,
) -> bool:
    ordering_conditions = [
        or_(
            CompanyRecord.last_success_at.is_(None),
            CompanyRecord.last_success_at < checked_at,
        )
    ]
    if complete_snapshot:
        ordering_conditions.append(
            or_(
                CompanyRecord.last_complete_snapshot_at.is_(None),
                CompanyRecord.last_complete_snapshot_at < checked_at,
            )
        )
    statement = (
        sqlalchemy_update(CompanyRecord)
        .where(CompanyRecord.id == str(company_id), *ordering_conditions)
        .values(last_success_at=checked_at)
        .returning(CompanyRecord.id)
    )
    if await session.scalar(statement) is not None:
        return True
    if not await _company_exists(session, company_id):
        raise LookupError(f"Unknown company: {company_id}")
    return False


def _mark_observation_missing(
    observation: VacancyObservationRecord,
    snapshot_at: datetime,
) -> None:
    if (
        observation.status == VacancyStatus.CLOSED.value
        and observation.closed_at is not None
        and observation.closure_evidence in _AUTHORITATIVE_CLOSURE_EVIDENCE
    ):
        observation.lifecycle_provenance = (
            VacancyLifecycleProvenance.ACCEPTED_COLLECTION.value
        )
        observation.missing_complete_snapshots = _MISSING_SNAPSHOTS_TO_CLOSE
        return
    if (
        observation.status == VacancyStatus.CLOSED.value
        or observation.closed_at is not None
        or observation.closure_evidence != VacancyClosureEvidence.UNKNOWN.value
    ):
        observation.status = VacancyStatus.ACTIVE.value
        observation.closed_at = None
        observation.closure_evidence = VacancyClosureEvidence.UNKNOWN.value
        observation.missing_complete_snapshots = 0
    elif observation.missing_complete_snapshots >= _MISSING_SNAPSHOTS_TO_CLOSE:
        # Historical rows could contain a schema-valid but impossible ACTIVE/2
        # state. Do not treat that ambiguous counter as a confirmed first miss.
        observation.missing_complete_snapshots = 0
    observation.missing_complete_snapshots = min(
        observation.missing_complete_snapshots + 1,
        _MISSING_SNAPSHOTS_TO_CLOSE,
    )
    if observation.missing_complete_snapshots == _MISSING_SNAPSHOTS_TO_CLOSE:
        observation.status = VacancyStatus.CLOSED.value
        observation.closed_at = snapshot_at
        observation.lifecycle_provenance = (
            VacancyLifecycleProvenance.ACCEPTED_COLLECTION.value
        )
        observation.closure_evidence = (
            VacancyClosureEvidence.AUTHORITATIVE_ABSENCE.value
        )


def _mark_observation_present(observation: VacancyObservationRecord) -> None:
    observation.status = VacancyStatus.ACTIVE.value
    observation.closed_at = None
    observation.closure_evidence = VacancyClosureEvidence.UNKNOWN.value
    observation.lifecycle_provenance = (
        VacancyLifecycleProvenance.ACCEPTED_COLLECTION.value
    )
    observation.missing_complete_snapshots = 0


async def _reconcile_observations(
    session: AsyncSession,
    company_id: UUID,
    source: str,
    present_source_job_ids: set[str],
    snapshot_at: datetime,
) -> set[str]:
    statement = select(VacancyObservationRecord).where(
        VacancyObservationRecord.source_company_id == str(company_id),
        VacancyObservationRecord.discovered_via == source,
    )
    observations = (await session.scalars(statement)).all()
    affected_vacancy_ids: set[str] = set()
    for observation in observations:
        affected_vacancy_ids.add(observation.vacancy_id)
        if observation.source_job_id in present_source_job_ids:
            continue
        _mark_observation_missing(observation, snapshot_at)
    return affected_vacancy_ids


async def _aggregate_canonical_lifecycle(
    session: AsyncSession,
    vacancy_ids: set[str],
    transition_at: datetime | None,
) -> None:
    if not vacancy_ids:
        return

    await session.flush()
    ordered_ids = sorted(vacancy_ids)
    for start in range(0, len(ordered_ids), _LIFECYCLE_AGGREGATION_BATCH_SIZE):
        batch_ids = ordered_ids[start : start + _LIFECYCLE_AGGREGATION_BATCH_SIZE]
        canonical_records = (
            await session.scalars(
                select(JobVacancyRecord).where(JobVacancyRecord.id.in_(batch_ids))
            )
        ).all()
        observations = (
            await session.scalars(
                select(VacancyObservationRecord).where(
                    VacancyObservationRecord.vacancy_id.in_(batch_ids)
                )
            )
        ).all()
        observations_by_vacancy: dict[str, list[VacancyObservationRecord]] = {}
        for observation in observations:
            observations_by_vacancy.setdefault(observation.vacancy_id, []).append(
                observation
            )

        for canonical in canonical_records:
            linked = observations_by_vacancy.get(canonical.id, [])
            lifecycle_observations = [
                observation
                for observation in linked
                if observation.lifecycle_provenance
                != VacancyLifecycleProvenance.GENERIC_UPSERT.value
            ]
            trustworthy_closure = (
                bool(lifecycle_observations)
                and all(
                    observation.source_company_id is not None for observation in linked
                )
                and all(
                    observation.source_company_id is not None
                    and observation.status == VacancyStatus.CLOSED.value
                    and observation.closed_at is not None
                    and observation.closure_evidence in _AUTHORITATIVE_CLOSURE_EVIDENCE
                    for observation in lifecycle_observations
                )
            )
            if not trustworthy_closure:
                canonical.status = VacancyStatus.ACTIVE.value
                canonical.closed_at = None
                continue

            if (
                canonical.status == VacancyStatus.CLOSED.value
                and canonical.closed_at is not None
            ):
                continue
            closure_times = [
                observation.closed_at
                for observation in lifecycle_observations
                if observation.closed_at is not None
            ]
            if transition_at is not None:
                closure_times.append(transition_at)
            canonical.status = VacancyStatus.CLOSED.value
            canonical.closed_at = max(closure_times)


def _advance_complete_snapshot(
    company: CompanyRecord,
    snapshot_at: datetime,
) -> None:
    if (
        company.last_complete_snapshot_at is not None
        and company.last_complete_snapshot_at >= snapshot_at
    ):
        raise ValueError("complete snapshot timestamp must advance monotonically")
    company.last_complete_snapshot_at = snapshot_at


async def _apply_success_health(
    session: AsyncSession,
    company_id: UUID,
    health: SourceHealthUpdate,
) -> None:
    record = await session.get(CompanyRecord, str(company_id))
    if record is None:
        raise LookupError(f"Unknown company: {company_id}")
    record.last_job_count = health.job_count
    if record.last_checked_at is None or record.last_checked_at <= health.checked_at:
        record.last_checked_at = health.checked_at
        record.last_status = health.status.value
        record.last_error_category = health.error_category
        record.updated_at = health.checked_at


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
    ) -> bool:
        values: dict[str, object] = {
            "last_checked_at": update.checked_at,
            "last_status": update.status.value,
            "last_error_category": update.error_category,
            "updated_at": update.checked_at,
        }
        if update.status in (
            SourceHealthStatus.HEALTHY,
            SourceHealthStatus.EMPTY,
        ):
            values["last_success_at"] = update.checked_at
            values["last_job_count"] = update.job_count
        async with self._session_factory() as session, session.begin():
            statement = (
                sqlalchemy_update(CompanyRecord)
                .where(
                    CompanyRecord.id == str(company_id),
                    or_(
                        CompanyRecord.last_checked_at.is_(None),
                        CompanyRecord.last_checked_at < update.checked_at,
                    ),
                )
                .values(**values)
                .returning(CompanyRecord.id)
            )
            if await session.scalar(statement) is not None:
                return True
            if not await _company_exists(session, company_id):
                raise LookupError(f"Unknown company: {company_id}")
            return False


class SQLAlchemyCollectionPersistence:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self._session_factory = session_factory

    async def persist_collection(
        self,
        company_id: UUID,
        result: SourceCollectionResult,
        health: SourceHealthUpdate,
    ) -> CollectionPersistenceResult:
        if health.status not in (
            SourceHealthStatus.HEALTHY,
            SourceHealthStatus.EMPTY,
        ):
            raise ValueError("successful collection health must be healthy or empty")
        if health.error_category is not None:
            raise ValueError("successful collection health cannot contain an error")
        if health.job_count != len(result.vacancies):
            raise ValueError("collection health job count must match vacancies")
        if any(vacancy.company_id != company_id for vacancy in result.vacancies):
            raise ValueError("all vacancies must belong to the collected company")

        async with self._session_factory() as session, session.begin():
            applied = await _claim_collection_success(
                session,
                company_id,
                health.checked_at,
                complete_snapshot=result.reconciliation_eligible,
            )
            if not applied:
                return CollectionPersistenceResult(
                    applied=False,
                    vacancy_upsert=VacancyUpsertResult(created=0, updated=0),
                )
            company = await session.get(CompanyRecord, str(company_id))
            if company is None:
                raise LookupError(f"Unknown company: {company_id}")
            source = company.ats_type
            if any(vacancy.source.value != source for vacancy in result.vacancies):
                raise ValueError(
                    "all vacancies must match the collected company source"
                )
            upserted = await _upsert_vacancies(
                session,
                result.vacancies,
                observed_at=health.checked_at,
                accepted_collection=True,
            )
            affected_vacancy_ids = set(upserted.affected_vacancy_ids)
            if result.reconciliation_eligible:
                affected_vacancy_ids.update(
                    await _reconcile_observations(
                        session,
                        company_id,
                        source,
                        {vacancy.source_job_id for vacancy in result.vacancies},
                        health.checked_at,
                    )
                )
            await _aggregate_canonical_lifecycle(
                session,
                affected_vacancy_ids,
                health.checked_at,
            )
            if result.reconciliation_eligible:
                _advance_complete_snapshot(company, health.checked_at)
            await _apply_success_health(session, company_id, health)
            return CollectionPersistenceResult(
                applied=True,
                vacancy_upsert=upserted.result,
            )


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
        source_company_id: UUID,
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
                    VacancyObservationRecord.source_company_id
                    == str(source_company_id),
                )
            )
            record = await session.scalar(statement)
            return _vacancy_to_domain(record) if record else None

    async def upsert_many(
        self,
        vacancies: Sequence[JobVacancy],
    ) -> VacancyUpsertResult:
        async with self._session_factory() as session, session.begin():
            upserted = await _upsert_vacancies(
                session,
                vacancies,
                observed_at=None,
                accepted_collection=False,
            )
            await _aggregate_canonical_lifecycle(
                session,
                set(upserted.affected_vacancy_ids),
                None,
            )
            return upserted.result

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
                    VacancyClassificationRecord.vacancy_id,
                )
            )
            if limit is not None:
                statement = statement.limit(limit)
            records = (await session.scalars(statement)).all()
            return tuple(_classification_to_domain(record) for record in records)

    async def list_active_by_decisions(
        self,
        classifier_version: str,
        decisions: Sequence[ClassificationDecision],
        *,
        limit: int | None = None,
    ) -> Sequence[VacancyClassification]:
        async with self._session_factory() as session:
            statement = (
                select(VacancyClassificationRecord)
                .join(
                    JobVacancyRecord,
                    JobVacancyRecord.id == VacancyClassificationRecord.vacancy_id,
                )
                .where(
                    VacancyClassificationRecord.classifier_version
                    == classifier_version,
                    VacancyClassificationRecord.decision.in_(
                        decision.value for decision in decisions
                    ),
                    JobVacancyRecord.status == VacancyStatus.ACTIVE.value,
                )
                .order_by(
                    VacancyClassificationRecord.decision,
                    VacancyClassificationRecord.classified_at.desc(),
                    VacancyClassificationRecord.vacancy_id,
                )
            )
            if limit is not None:
                statement = statement.limit(limit)
            records = (await session.scalars(statement)).all()
            return tuple(_classification_to_domain(record) for record in records)
