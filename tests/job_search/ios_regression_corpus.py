from dataclasses import dataclass

from job_search.domain.classification import (
    ClassificationDecision,
    RejectionReason,
    WarningCode,
)
from job_search.domain.enums import RemotePolicy


@dataclass(frozen=True, slots=True)
class RegressionCase:
    name: str
    title: str
    description: str
    remote_policy: RemotePolicy
    decision: ClassificationDecision
    rejection: RejectionReason | None = None
    warning: WarningCode | None = None


REGRESSION_CASES = (
    RegressionCase(
        "obvious good iOS role",
        "Senior iOS Engineer",
        "Build iOS products with Swift. Remote worldwide.",
        RemotePolicy.REMOTE,
        ClassificationDecision.MATCH,
    ),
    RegressionCase(
        "generic mobile with strong iOS",
        "Senior Mobile Engineer",
        "Build our native iOS app using Swift and SwiftUI. Remote worldwide.",
        RemotePolicy.REMOTE,
        ClassificationDecision.MATCH,
    ),
    RegressionCase(
        "Android false positive",
        "Senior Android Engineer",
        "Work in Kotlin and collaborate with the iOS team using Swift.",
        RemotePolicy.REMOTE,
        ClassificationDecision.REJECT,
        RejectionReason.ANDROID_ONLY,
    ),
    RegressionCase(
        "React Native false positive",
        "Senior React Native Engineer",
        "Build cross-platform applications and collaborate with iOS engineers.",
        RemotePolicy.REMOTE,
        ClassificationDecision.REJECT,
        RejectionReason.REACT_NATIVE_ONLY,
    ),
    RegressionCase(
        "Objective-C-heavy role",
        "Senior iOS Engineer",
        "The main language is Objective-C in a predominantly Objective-C app.",
        RemotePolicy.REMOTE,
        ClassificationDecision.REJECT,
        RejectionReason.OBJECTIVE_C_PRIMARY,
    ),
    RegressionCase(
        "Objective-C legacy role",
        "Senior iOS Engineer",
        "Swift is primary; maintain some legacy Objective-C. Remote worldwide.",
        RemotePolicy.REMOTE,
        ClassificationDecision.POSSIBLE_MATCH,
        warning=WarningCode.OBJECTIVE_C_PRESENT,
    ),
    RegressionCase(
        "remote Europe role",
        "Senior iOS Developer",
        "Swift role, remote across Europe.",
        RemotePolicy.REMOTE,
        ClassificationDecision.MATCH,
    ),
    RegressionCase(
        "onsite relocation role",
        "Senior Swift Engineer",
        "On-site role with relocation assistance provided.",
        RemotePolicy.ONSITE,
        ClassificationDecision.MATCH,
    ),
    RegressionCase(
        "onsite no relocation",
        "Senior iOS Engineer",
        "On-site only. Relocation is not available.",
        RemotePolicy.ONSITE,
        ClassificationDecision.REJECT,
        RejectionReason.ONSITE_NO_RELOCATION,
    ),
    RegressionCase(
        "mandatory German",
        "Senior iOS Engineer",
        "Swift and UIKit. Fluent German required. Remote worldwide.",
        RemotePolicy.REMOTE,
        ClassificationDecision.REJECT,
        RejectionReason.UNSUPPORTED_REQUIRED_LANGUAGE,
    ),
    RegressionCase(
        "ambiguous geography",
        "Senior iOS Engineer",
        "Develop native apps in Swift. Remote role.",
        RemotePolicy.REMOTE,
        ClassificationDecision.POSSIBLE_MATCH,
        warning=WarningCode.REMOTE_GEOGRAPHY_UNCLEAR,
    ),
    RegressionCase(
        "hands-on Lead",
        "Lead iOS Engineer",
        "Hands-on role building iOS features in Swift. Remote worldwide.",
        RemotePolicy.REMOTE,
        ClassificationDecision.POSSIBLE_MATCH,
        warning=WarningCode.LEAD_ROLE,
    ),
)
