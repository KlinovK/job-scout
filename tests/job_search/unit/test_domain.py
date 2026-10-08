from dataclasses import replace
from datetime import datetime

import pytest

from job_search.domain.classification import ClassificationDecision, RejectionReason
from job_search.domain.classifier import IOSVacancyClassifier
from tests.job_search.factories import NOW, make_company, make_vacancy


def test_company_rejects_naive_timestamps() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        company = make_company()
        company.__class__(
            id=company.id,
            name=company.name,
            country=company.country,
            careers_url=company.careers_url,
            ats_type=company.ats_type,
            ats_identifier=company.ats_identifier,
            priority=company.priority,
            enabled=company.enabled,
            last_checked_at=None,
            created_at=datetime(2026, 9, 17),
            updated_at=company.updated_at,
        )


def test_vacancy_requires_external_identity() -> None:
    company = make_company()
    vacancy = make_vacancy(company.id)
    with pytest.raises(ValueError, match="source_job_id"):
        vacancy.__class__(
            id=vacancy.id,
            company_id=vacancy.company_id,
            title=vacancy.title,
            description=vacancy.description,
            company_location=vacancy.company_location,
            work_location=vacancy.work_location,
            remote_policy=vacancy.remote_policy,
            url=vacancy.url,
            source=vacancy.source,
            source_job_id="",
            published_at=vacancy.published_at,
            first_seen_at=vacancy.first_seen_at,
            last_seen_at=vacancy.last_seen_at,
            status=vacancy.status,
        )


def test_classification_requires_utc_and_consistent_reasons() -> None:
    company = make_company()
    vacancy = make_vacancy(company.id)
    classification = IOSVacancyClassifier().classify(vacancy, NOW)

    with pytest.raises(ValueError, match="timezone-aware"):
        replace(classification, classified_at=datetime(2026, 9, 17))
    with pytest.raises(ValueError, match="non-rejected"):
        replace(
            classification,
            decision=ClassificationDecision.MATCH,
            rejection_reasons=(RejectionReason.NO_IOS_RELEVANCE,),
        )
    with pytest.raises(ValueError, match="requires a reason"):
        replace(
            classification,
            decision=ClassificationDecision.REJECT,
            rejection_reasons=(),
        )
