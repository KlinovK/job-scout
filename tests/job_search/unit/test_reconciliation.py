from job_search.domain.enums import (
    VacancyClosureEvidence,
    VacancyLifecycleProvenance,
    VacancyStatus,
)
from job_search.infrastructure.persistence.sqlalchemy.repositories import (
    _mark_observation_missing,
    _mark_observation_present,
    _new_observation,
)
from tests.job_search.factories import LATER, NOW, make_company, make_vacancy


def _observation():
    company = make_company()
    vacancy = make_vacancy(company.id)
    return _new_observation(
        vacancy,
        str(vacancy.id),
        vacancy.url,
        accepted_collection=False,
    )


def test_first_missing_snapshot_keeps_observation_active() -> None:
    observation = _observation()

    _mark_observation_missing(observation, NOW)

    assert observation.status == VacancyStatus.ACTIVE.value
    assert observation.missing_complete_snapshots == 1
    assert observation.closed_at is None
    assert (
        observation.lifecycle_provenance
        == VacancyLifecycleProvenance.GENERIC_UPSERT.value
    )


def test_second_missing_snapshot_closes_observation() -> None:
    observation = _observation()
    observation.missing_complete_snapshots = 1

    _mark_observation_missing(observation, LATER)

    assert observation.status == VacancyStatus.CLOSED.value
    assert observation.missing_complete_snapshots == 2
    assert observation.closed_at == LATER
    assert (
        observation.closure_evidence
        == VacancyClosureEvidence.AUTHORITATIVE_ABSENCE.value
    )
    assert (
        observation.lifecycle_provenance
        == VacancyLifecycleProvenance.ACCEPTED_COLLECTION.value
    )


def test_repeated_missing_snapshot_preserves_closed_at_and_bounds_counter() -> None:
    observation = _observation()
    observation.status = VacancyStatus.CLOSED.value
    observation.closed_at = NOW
    observation.missing_complete_snapshots = 2
    observation.closure_evidence = VacancyClosureEvidence.AUTHORITATIVE_ABSENCE.value

    _mark_observation_missing(observation, LATER)

    assert observation.status == VacancyStatus.CLOSED.value
    assert observation.missing_complete_snapshots == 2
    assert observation.closed_at == NOW
    assert (
        observation.closure_evidence
        == VacancyClosureEvidence.AUTHORITATIVE_ABSENCE.value
    )


def test_present_observation_reopens_and_resets_lifecycle() -> None:
    observation = _observation()
    observation.status = VacancyStatus.CLOSED.value
    observation.closed_at = NOW
    observation.missing_complete_snapshots = 2

    _mark_observation_present(observation)

    assert observation.status == VacancyStatus.ACTIVE.value
    assert observation.missing_complete_snapshots == 0
    assert observation.closed_at is None
    assert observation.closure_evidence == VacancyClosureEvidence.UNKNOWN.value
    assert (
        observation.lifecycle_provenance
        == VacancyLifecycleProvenance.ACCEPTED_COLLECTION.value
    )
