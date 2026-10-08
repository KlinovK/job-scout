from uuid import uuid4

import pytest

from job_search.domain.classification import (
    ClassificationDecision,
    GeographyScope,
    PositiveSignal,
    RejectionReason,
    Seniority,
    WarningCode,
    WorkMode,
)
from job_search.domain.classifier import IOS_CLASSIFIER_VERSION, IOSVacancyClassifier
from job_search.domain.enums import RemotePolicy
from tests.job_search.factories import NOW, make_vacancy
from tests.job_search.ios_regression_corpus import REGRESSION_CASES


def classify(
    title: str,
    description: str,
    *,
    remote_policy: RemotePolicy = RemotePolicy.REMOTE,
    work_location: str | None = "Remote",
):
    vacancy = make_vacancy(
        uuid4(),
        title=title,
        description=description,
        remote_policy=remote_policy,
        work_location=work_location,
    )
    return IOSVacancyClassifier().classify(vacancy, NOW)


@pytest.mark.parametrize("case", REGRESSION_CASES, ids=lambda case: case.name)
def test_regression_corpus(case) -> None:
    result = classify(
        case.title,
        case.description,
        remote_policy=case.remote_policy,
    )

    assert result.decision is case.decision
    if case.rejection is not None:
        assert case.rejection in result.rejection_reasons
    if case.warning is not None:
        assert case.warning in result.warnings


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Senior iOS Engineer", Seniority.SENIOR),
        ("Middle iOS Developer", Seniority.MIDDLE),
        ("Intermediate iOS Developer", Seniority.MIDDLE),
        ("Mid-level Software Engineer, iOS", Seniority.MIDDLE),
        ("Swift Engineer", Seniority.UNKNOWN),
    ],
)
def test_accepted_role_and_seniority_variants(title: str, expected: Seniority) -> None:
    result = classify(title, "Build native apps with Swift. Remote worldwide.")

    assert result.decision is not ClassificationDecision.REJECT
    assert result.seniority is expected


@pytest.mark.parametrize(
    ("title", "reason"),
    [
        ("Junior iOS Engineer", RejectionReason.JUNIOR_ROLE),
        ("iOS Engineering Intern", RejectionReason.INTERNSHIP),
        ("iOS Trainee", RejectionReason.JUNIOR_ROLE),
        ("Staff iOS Engineer", RejectionReason.STAFF_ROLE),
        ("Principal iOS Engineer", RejectionReason.PRINCIPAL_ROLE),
        ("Senior Flutter Engineer", RejectionReason.FLUTTER_ONLY),
    ],
)
def test_hard_rejections(title: str, reason: RejectionReason) -> None:
    result = classify(title, "Remote worldwide role.")

    assert result.decision is ClassificationDecision.REJECT
    assert reason in result.rejection_reasons


def test_mobile_without_native_ios_evidence_is_rejected() -> None:
    result = classify(
        "Senior Mobile Engineer",
        "Build mobile products and APIs. Remote worldwide.",
    )

    assert result.rejection_reasons == (RejectionReason.NO_IOS_RELEVANCE,)


def test_mixed_android_ios_role_is_possible() -> None:
    result = classify(
        "Senior Android and iOS Engineer",
        "Develop Android and iOS applications. Strong Swift and UIKit required. \
Remote worldwide.",
    )

    assert result.decision is ClassificationDecision.POSSIBLE_MATCH
    assert WarningCode.MIXED_MOBILE_ROLE in result.warnings


def test_swift_migration_from_objective_c_is_not_rejected() -> None:
    result = classify(
        "Senior iOS Engineer",
        "Swift is the primary language. Migrate legacy Objective-C modules. \
Remote worldwide.",
    )

    assert result.decision is ClassificationDecision.POSSIBLE_MATCH
    assert WarningCode.OBJECTIVE_C_PRESENT in result.warnings


def test_optional_objective_c_is_warning_not_rejection() -> None:
    result = classify(
        "Senior iOS Engineer",
        "Swift and UIKit required. Objective-C is optional. Remote worldwide.",
    )

    assert result.decision is ClassificationDecision.POSSIBLE_MATCH
    assert WarningCode.OBJECTIVE_C_PRESENT in result.warnings


@pytest.mark.parametrize(
    ("policy", "description", "decision", "reason"),
    [
        (
            RemotePolicy.HYBRID,
            "Swift role. Relocation assistance provided.",
            ClassificationDecision.MATCH,
            None,
        ),
        (
            RemotePolicy.HYBRID,
            "Swift role. No relocation.",
            ClassificationDecision.REJECT,
            RejectionReason.HYBRID_NO_RELOCATION,
        ),
        (
            RemotePolicy.ONSITE,
            "Swift role with no relocation.",
            ClassificationDecision.REJECT,
            RejectionReason.ONSITE_NO_RELOCATION,
        ),
    ],
)
def test_work_mode_and_relocation(
    policy: RemotePolicy,
    description: str,
    decision: ClassificationDecision,
    reason: RejectionReason | None,
) -> None:
    result = classify("Senior iOS Engineer", description, remote_policy=policy)

    assert result.decision is decision
    if reason is not None:
        assert reason in result.rejection_reasons


def test_unknown_work_mode_is_possible_not_rejected() -> None:
    result = classify(
        "Senior iOS Engineer",
        "Build applications using Swift and UIKit.",
        remote_policy=RemotePolicy.UNKNOWN,
        work_location=None,
    )

    assert result.decision is ClassificationDecision.POSSIBLE_MATCH
    assert result.work_mode is WorkMode.UNKNOWN
    assert WarningCode.WORK_MODE_UNCLEAR in result.warnings


@pytest.mark.parametrize(
    ("phrase", "scope", "warning"),
    [
        ("Remote EU only", GeographyScope.EU_ONLY, WarningCode.EU_ONLY),
        ("Remote US only", GeographyScope.US_ONLY, WarningCode.US_ONLY),
        ("Remote UK only", GeographyScope.UK_ONLY, WarningCode.UK_ONLY),
        (
            "Must be authorized to work in Canada",
            GeographyScope.UNKNOWN,
            WarningCode.WORK_AUTHORIZATION_REQUIRED,
        ),
        (
            "Visa sponsorship is not available",
            GeographyScope.UNKNOWN,
            WarningCode.VISA_SPONSORSHIP_UNAVAILABLE,
        ),
        (
            "Remote in Canada",
            GeographyScope.COUNTRY_ONLY,
            WarningCode.COUNTRY_RESTRICTED,
        ),
    ],
)
def test_geography_and_authorization_warnings(
    phrase: str,
    scope: GeographyScope,
    warning: WarningCode,
) -> None:
    result = classify("Senior iOS Engineer", f"Swift. {phrase}")

    assert result.decision is ClassificationDecision.POSSIBLE_MATCH
    assert result.geography is scope
    assert warning in result.warnings


def test_sponsorship_available_is_positive_signal() -> None:
    result = classify(
        "Senior iOS Engineer",
        "Swift role. Visa sponsorship is available. On-site.",
        remote_policy=RemotePolicy.ONSITE,
    )

    assert result.decision is ClassificationDecision.MATCH
    assert PositiveSignal.VISA_SPONSORSHIP_AVAILABLE in result.positive_signals


@pytest.mark.parametrize("language", ["German", "Dutch", "Polish"])
def test_unsupported_mandatory_language_is_rejected(language: str) -> None:
    result = classify(
        "Senior iOS Engineer",
        f"Swift. Fluent {language} required. Remote worldwide.",
    )

    assert RejectionReason.UNSUPPORTED_REQUIRED_LANGUAGE in result.rejection_reasons


def test_preferred_language_does_not_reject() -> None:
    result = classify(
        "Senior iOS Engineer",
        "Swift. German preferred, not required. Remote worldwide.",
    )

    assert result.decision is ClassificationDecision.POSSIBLE_MATCH
    assert WarningCode.OTHER_LANGUAGE_PREFERRED in result.warnings


@pytest.mark.parametrize("language", ["English", "Russian"])
def test_supported_required_language_is_accepted(language: str) -> None:
    result = classify(
        "Senior iOS Engineer",
        f"Swift and UIKit. {language} required. Remote worldwide.",
    )

    assert result.decision is ClassificationDecision.MATCH


def test_polish_nice_to_have_is_not_rejected() -> None:
    result = classify(
        "Senior iOS Engineer",
        "Swift. Polish is nice to have. Remote worldwide.",
    )

    assert result.decision is ClassificationDecision.POSSIBLE_MATCH
    assert WarningCode.OTHER_LANGUAGE_PREFERRED in result.warnings


@pytest.mark.parametrize(
    ("domain", "signal"),
    [
        ("FinTech", PositiveSignal.FINTECH_DOMAIN),
        ("Web3", PositiveSignal.WEB3_DOMAIN),
        ("crypto", PositiveSignal.CRYPTO_DOMAIN),
        ("blockchain", PositiveSignal.BLOCKCHAIN_DOMAIN),
        ("machine learning", PositiveSignal.AI_ML_DOMAIN),
    ],
)
def test_preferred_domain_is_positive_only(
    domain: str,
    signal: PositiveSignal,
) -> None:
    result = classify(
        "Senior iOS Engineer",
        f"Build a {domain} product in Swift. Remote worldwide.",
    )

    assert result.decision is ClassificationDecision.MATCH
    assert signal in result.positive_signals


@pytest.mark.parametrize("domain", ["healthcare", "ecommerce", "education"])
def test_other_domains_are_not_downgraded(domain: str) -> None:
    result = classify(
        "Senior iOS Engineer",
        f"Build a {domain} product using Swift. Remote worldwide.",
    )

    assert result.decision is ClassificationDecision.MATCH


def test_html_unicode_case_and_word_boundaries() -> None:
    result = classify(
        "SENIOR MOBILE ENGINEER",
        "<p>Build native <strong>iOS</strong> apps with SwiftUI.</p> \
Remote WORLDWIDE.",
    )
    unrelated = classify(
        "Senior Mobile Engineer",
        "Work with scenarios and APIs. Remote worldwide.",
    )
    double_encoded = classify(
        "Senior Mobile Engineer",
        "&lt;p&gt;Build iOS using SwiftUI and UIKit.&lt;/p&gt; Remote worldwide.",
    )

    assert result.decision is ClassificationDecision.MATCH
    assert unrelated.decision is ClassificationDecision.REJECT
    assert double_encoded.decision is ClassificationDecision.MATCH


def test_swift_as_adjective_is_not_a_programming_signal() -> None:
    result = classify(
        "Senior Mobile Engineer",
        "Ensure customers receive a swift response. Remote worldwide.",
    )

    assert result.decision is ClassificationDecision.REJECT
    assert PositiveSignal.SWIFT_REQUIRED not in result.positive_signals


def test_empty_description_and_missing_location_are_safe() -> None:
    vacancy = make_vacancy(
        uuid4(),
        title="Senior iOS Engineer",
        description="",
        work_location=None,
        remote_policy=RemotePolicy.UNKNOWN,
    )

    result = IOSVacancyClassifier().classify(vacancy, NOW)

    assert result.decision is ClassificationDecision.POSSIBLE_MATCH
    assert result.classifier_version == IOS_CLASSIFIER_VERSION
