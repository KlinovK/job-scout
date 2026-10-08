from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from job_search.domain.models import _require_utc


class ClassificationDecision(StrEnum):
    MATCH = "match"
    POSSIBLE_MATCH = "possible_match"
    REJECT = "reject"


class RoleCategory(StrEnum):
    IOS = "ios"
    SWIFT = "swift"
    APPLE_PLATFORMS = "apple_platforms"
    IOS_SDK = "ios_sdk"
    MOBILE_IOS = "mobile_ios"
    MIXED_MOBILE = "mixed_mobile"
    OTHER = "other"


class Seniority(StrEnum):
    MIDDLE = "middle"
    SENIOR = "senior"
    LEAD = "lead"
    JUNIOR = "junior"
    INTERN = "intern"
    STAFF = "staff"
    PRINCIPAL = "principal"
    UNKNOWN = "unknown"


class IOSRelevance(StrEnum):
    STRONG = "strong"
    SUBSTANTIAL = "substantial"
    WEAK = "weak"
    NONE = "none"


class WorkMode(StrEnum):
    REMOTE = "remote"
    HYBRID = "hybrid"
    ONSITE = "onsite"
    UNKNOWN = "unknown"


class RelocationStatus(StrEnum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"


class GeographyScope(StrEnum):
    WORLDWIDE = "worldwide"
    EMEA = "emea"
    EUROPE = "europe"
    EU_ONLY = "eu_only"
    US_ONLY = "us_only"
    UK_ONLY = "uk_only"
    COUNTRY_ONLY = "country_only"
    UNKNOWN = "unknown"


class PositiveSignal(StrEnum):
    IOS_TITLE = "ios_title"
    SWIFT_TITLE = "swift_title"
    IOS_DESCRIPTION = "ios_description"
    SWIFT_REQUIRED = "swift_required"
    SWIFTUI = "swiftui"
    UIKIT = "uikit"
    SWIFT_CONCURRENCY = "swift_concurrency"
    APPLE_PLATFORMS = "apple_platforms"
    XCODE = "xcode"
    IOS_SDK = "ios_sdk"
    XCTEST = "xctest"
    COMBINE = "combine"
    APP_STORE = "app_store"
    REMOTE_WORLDWIDE = "remote_worldwide"
    REMOTE_EMEA = "remote_emea"
    REMOTE_EUROPE = "remote_europe"
    RELOCATION_AVAILABLE = "relocation_available"
    VISA_SPONSORSHIP_AVAILABLE = "visa_sponsorship_available"
    FINTECH_DOMAIN = "fintech_domain"
    WEB3_DOMAIN = "web3_domain"
    CRYPTO_DOMAIN = "crypto_domain"
    BLOCKCHAIN_DOMAIN = "blockchain_domain"
    AI_ML_DOMAIN = "ai_ml_domain"


class WarningCode(StrEnum):
    OBJECTIVE_C_PRESENT = "objective_c_present"
    RELOCATION_UNCLEAR = "relocation_unclear"
    EU_ONLY = "eu_only"
    US_ONLY = "us_only"
    UK_ONLY = "uk_only"
    COUNTRY_RESTRICTED = "country_restricted"
    WORK_AUTHORIZATION_REQUIRED = "work_authorization_required"
    VISA_SPONSORSHIP_UNAVAILABLE = "visa_sponsorship_unavailable"
    REMOTE_GEOGRAPHY_UNCLEAR = "remote_geography_unclear"
    OTHER_LANGUAGE_PREFERRED = "other_language_preferred"
    LEAD_ROLE = "lead_role"
    MIXED_MOBILE_ROLE = "mixed_mobile_role"
    SENIORITY_UNCLEAR = "seniority_unclear"
    WORK_MODE_UNCLEAR = "work_mode_unclear"


class RejectionReason(StrEnum):
    JUNIOR_ROLE = "junior_role"
    INTERNSHIP = "internship"
    STAFF_ROLE = "staff_role"
    PRINCIPAL_ROLE = "principal_role"
    LEAD_NOT_HANDS_ON = "lead_not_hands_on"
    NO_IOS_RELEVANCE = "no_ios_relevance"
    ANDROID_ONLY = "android_only"
    FLUTTER_ONLY = "flutter_only"
    REACT_NATIVE_ONLY = "react_native_only"
    OBJECTIVE_C_PRIMARY = "objective_c_primary"
    ONSITE_NO_RELOCATION = "onsite_no_relocation"
    HYBRID_NO_RELOCATION = "hybrid_no_relocation"
    UNSUPPORTED_REQUIRED_LANGUAGE = "unsupported_required_language"


@dataclass(frozen=True, slots=True)
class VacancyClassification:
    vacancy_id: UUID
    decision: ClassificationDecision
    role_category: RoleCategory
    seniority: Seniority
    ios_relevance: IOSRelevance
    work_mode: WorkMode
    relocation: RelocationStatus
    geography: GeographyScope
    positive_signals: tuple[PositiveSignal, ...]
    warnings: tuple[WarningCode, ...]
    rejection_reasons: tuple[RejectionReason, ...]
    classified_at: datetime
    classifier_version: str

    def __post_init__(self) -> None:
        _require_utc(self.classified_at, "classified_at")
        if not self.classifier_version.strip():
            raise ValueError("classifier_version must not be empty")
        if self.decision is ClassificationDecision.REJECT:
            if not self.rejection_reasons:
                raise ValueError("rejected classification requires a reason")
        elif self.rejection_reasons:
            raise ValueError(
                "non-rejected classification cannot have rejection reasons"
            )
