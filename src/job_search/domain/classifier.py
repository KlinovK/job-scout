import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from html.parser import HTMLParser

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
from job_search.domain.enums import RemotePolicy
from job_search.domain.models import JobVacancy

IOS_CLASSIFIER_VERSION = "ios-v1"


class _HTMLTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


@dataclass(frozen=True, slots=True)
class _VacancyText:
    title: str
    description: str
    location: str

    @property
    def combined(self) -> str:
        return f"{self.title} {self.description} {self.location}"


def normalize_text(value: str | None) -> str:
    if not value:
        return ""
    text = value
    for _ in range(2):
        parser = _HTMLTextExtractor()
        parser.feed(text)
        parser.close()
        text = " ".join(parser.parts)
    text = unicodedata.normalize("NFKC", text).casefold()
    text = re.sub(r"[‐‑‒–—−]", "-", text)
    text = re.sub(r"[^\w+#.-]+", " ", text, flags=re.UNICODE)
    return re.sub(r"\s+", " ", text).strip()


def _has(text: str, *patterns: str) -> bool:
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns)


def _ordered_unique[T](values: Iterable[T]) -> tuple[T, ...]:
    return tuple(dict.fromkeys(values))


def _has_technical_swift(text: str) -> bool:
    return _has(
        text,
        r"\b(?:using|with|in) swift\b",
        r"\bswift (?:is )?(?:primary|development|language|code|concurrency)\b",
        r"\bswift\b.{0,20}\b(?:swiftui|uikit|ios|xcode)\b",
        r"\b(?:swiftui|uikit|ios|xcode)\b.{0,40}\bswift\b",
        r"\bexperience\b.{0,35}\bswift\b",
    )


class IOSVacancyClassifier:
    """Explainable deterministic policy for native iOS vacancy eligibility."""

    version = IOS_CLASSIFIER_VERSION

    def classify(
        self,
        vacancy: JobVacancy,
        classified_at: datetime,
    ) -> VacancyClassification:
        text = _VacancyText(
            title=normalize_text(vacancy.title),
            description=normalize_text(vacancy.description),
            location=normalize_text(vacancy.work_location),
        )
        signals = self._positive_signals(text)
        role, relevance, role_warnings, role_rejections = self._classify_role(text)
        seniority, seniority_warnings, seniority_rejections = self._seniority(text)
        objective_warnings, objective_rejections = self._objective_c(text)
        work_mode = self._work_mode(vacancy.remote_policy, text)
        relocation, relocation_signals = self._relocation(text)
        geography, geography_signals, geography_warnings = self._geography(text)
        work_warnings, work_rejections = self._work_eligibility(
            work_mode,
            relocation,
            geography,
        )
        language_warnings, language_rejections = self._languages(text)

        all_signals = _ordered_unique(
            (*signals, *relocation_signals, *geography_signals)
        )
        warnings = _ordered_unique(
            (
                *role_warnings,
                *seniority_warnings,
                *objective_warnings,
                *geography_warnings,
                *work_warnings,
                *language_warnings,
            )
        )
        rejections = _ordered_unique(
            (
                *role_rejections,
                *seniority_rejections,
                *objective_rejections,
                *work_rejections,
                *language_rejections,
            )
        )

        if rejections:
            decision = ClassificationDecision.REJECT
            warnings = ()
        elif warnings:
            decision = ClassificationDecision.POSSIBLE_MATCH
        else:
            decision = ClassificationDecision.MATCH

        return VacancyClassification(
            vacancy_id=vacancy.id,
            decision=decision,
            role_category=role,
            seniority=seniority,
            ios_relevance=relevance,
            work_mode=work_mode,
            relocation=relocation,
            geography=geography,
            positive_signals=all_signals,
            warnings=warnings,
            rejection_reasons=rejections,
            classified_at=classified_at,
            classifier_version=self.version,
        )

    def _classify_role(
        self,
        text: _VacancyText,
    ) -> tuple[
        RoleCategory,
        IOSRelevance,
        tuple[WarningCode, ...],
        tuple[RejectionReason, ...],
    ]:
        title = text.title
        description = text.description
        title_ios = _has(title, r"\bios\b")
        title_swift = _has(title, r"\bswift\b")
        title_apple = _has(title, r"\bapple platforms?\b")
        title_sdk = _has(title, r"\bios sdk\b")
        title_mobile = _has(
            title,
            r"\bmobile (?:software |platform )?(?:engineer|developer)\b",
            r"\bproduct engineer\b.*\bmobile\b",
            r"\bsoftware engineer\b.*\bmobile\b",
        )
        title_android = _has(title, r"\bandroid\b", r"\bkotlin\b.*\bandroid\b")
        title_flutter = _has(title, r"\bflutter\b")
        title_react_native = _has(title, r"\breact[ -]native\b")

        ios_evidence = {
            pattern
            for pattern in (
                r"\bios\b",
                r"\bswiftui\b",
                r"\buikit\b",
                r"\bxcode\b",
                r"\bios sdk\b",
                r"\bxctest\b",
                r"\bapple (?:platforms?|frameworks?)\b",
            )
            if _has(description, pattern)
        }
        if _has_technical_swift(description):
            ios_evidence.add("technical_swift")
        substantial_description = len(ios_evidence) >= 2

        if title_android and not (title_ios or title_swift):
            return (
                RoleCategory.OTHER,
                IOSRelevance.WEAK if ios_evidence else IOSRelevance.NONE,
                (),
                (RejectionReason.ANDROID_ONLY,),
            )
        if title_flutter and not (title_ios or title_swift or substantial_description):
            return (
                RoleCategory.OTHER,
                IOSRelevance.WEAK if ios_evidence else IOSRelevance.NONE,
                (),
                (RejectionReason.FLUTTER_ONLY,),
            )
        if title_react_native and not (
            title_ios or title_swift or substantial_description
        ):
            return (
                RoleCategory.OTHER,
                IOSRelevance.WEAK if ios_evidence else IOSRelevance.NONE,
                (),
                (RejectionReason.REACT_NATIVE_ONLY,),
            )

        mixed = title_android or title_flutter or title_react_native
        if mixed and (title_ios or title_swift or substantial_description):
            return (
                RoleCategory.MIXED_MOBILE,
                IOSRelevance.SUBSTANTIAL,
                (WarningCode.MIXED_MOBILE_ROLE,),
                (),
            )
        if title_sdk:
            return RoleCategory.IOS_SDK, IOSRelevance.STRONG, (), ()
        if title_apple:
            return RoleCategory.APPLE_PLATFORMS, IOSRelevance.STRONG, (), ()
        if title_ios:
            return RoleCategory.IOS, IOSRelevance.STRONG, (), ()
        if title_swift:
            return RoleCategory.SWIFT, IOSRelevance.STRONG, (), ()
        if title_mobile and substantial_description:
            return RoleCategory.MOBILE_IOS, IOSRelevance.SUBSTANTIAL, (), ()
        if title_mobile:
            return (
                RoleCategory.OTHER,
                IOSRelevance.WEAK if ios_evidence else IOSRelevance.NONE,
                (),
                (RejectionReason.NO_IOS_RELEVANCE,),
            )
        if substantial_description:
            return RoleCategory.MOBILE_IOS, IOSRelevance.SUBSTANTIAL, (), ()
        return (
            RoleCategory.OTHER,
            IOSRelevance.WEAK if ios_evidence else IOSRelevance.NONE,
            (),
            (RejectionReason.NO_IOS_RELEVANCE,),
        )

    def _seniority(
        self,
        text: _VacancyText,
    ) -> tuple[
        Seniority,
        tuple[WarningCode, ...],
        tuple[RejectionReason, ...],
    ]:
        title = text.title
        if _has(title, r"\b(?:intern|internship)\b"):
            return Seniority.INTERN, (), (RejectionReason.INTERNSHIP,)
        if _has(title, r"\b(?:junior|jr\.?|trainee|graduate)\b"):
            return Seniority.JUNIOR, (), (RejectionReason.JUNIOR_ROLE,)
        if _has(title, r"\bprincipal\b"):
            return Seniority.PRINCIPAL, (), (RejectionReason.PRINCIPAL_ROLE,)
        if _has(title, r"\bstaff\b"):
            return Seniority.STAFF, (), (RejectionReason.STAFF_ROLE,)
        if _has(title, r"\blead\b"):
            hands_on = _has(
                text.description,
                r"\bhands[ -]on\b",
                r"\bwrite (?:production )?code\b",
                r"\bimplement(?:ing)?\b.*\b(?:swift|ios)\b",
                r"\bbuild(?:ing)?\b.*\b(?:ios|swift)\b",
            )
            if hands_on:
                return Seniority.LEAD, (WarningCode.LEAD_ROLE,), ()
            return Seniority.LEAD, (), (RejectionReason.LEAD_NOT_HANDS_ON,)
        if _has(title, r"\b(?:senior|sr\.?)\b"):
            return Seniority.SENIOR, (), ()
        if _has(title, r"\b(?:middle|intermediate|mid[ -]?level|mid)\b"):
            return Seniority.MIDDLE, (), ()
        return Seniority.UNKNOWN, (WarningCode.SENIORITY_UNCLEAR,), ()

    def _objective_c(
        self,
        text: _VacancyText,
    ) -> tuple[tuple[WarningCode, ...], tuple[RejectionReason, ...]]:
        combined = text.combined
        if not _has(combined, r"\bobjective[ -]?c\b"):
            return (), ()
        if _has(
            combined,
            r"\bprimarily\b.{0,35}\bobjective[ -]?c\b",
            r"\bobjective[ -]?c\b.{0,35}\b(?:main|primary) language\b",
            r"\b(?:main|primary) language (?:is )?objective[ -]?c\b",
            r"\bpredominantly\b.{0,35}\bobjective[ -]?c\b",
            r"\bextensive objective[ -]?c development\b",
        ):
            return (), (RejectionReason.OBJECTIVE_C_PRIMARY,)
        return (WarningCode.OBJECTIVE_C_PRESENT,), ()

    def _work_mode(self, remote_policy: RemotePolicy, text: _VacancyText) -> WorkMode:
        if remote_policy is RemotePolicy.REMOTE:
            return WorkMode.REMOTE
        if remote_policy is RemotePolicy.HYBRID:
            return WorkMode.HYBRID
        if remote_policy is RemotePolicy.ONSITE:
            return WorkMode.ONSITE
        combined = text.combined
        if _has(combined, r"\bhybrid\b"):
            return WorkMode.HYBRID
        if _has(combined, r"\b(?:on[ -]?site|in office)\b"):
            return WorkMode.ONSITE
        if _has(combined, r"\bremote\b", r"\bwork from home\b"):
            return WorkMode.REMOTE
        return WorkMode.UNKNOWN

    def _relocation(
        self,
        text: _VacancyText,
    ) -> tuple[RelocationStatus, tuple[PositiveSignal, ...]]:
        combined = text.combined
        if _has(
            combined,
            r"\b(?:no|without) relocation\b",
            r"\brelocation (?:is )?not (?:available|provided|offered)\b",
        ):
            return RelocationStatus.UNAVAILABLE, ()
        relocation = _has(
            combined,
            r"\brelocation (?:assistance|support|package|available|provided)\b",
            r"\brelocate to\b",
            r"\bwe (?:can|will|do) (?:offer|provide) relocation\b",
        )
        sponsorship = _has(
            combined,
            r"\bvisa sponsorship (?:is )?(?:available|provided|offered)\b",
            r"\bsponsor(?:ing)? (?:a )?(?:work )?visa\b",
        )
        signals: list[PositiveSignal] = []
        if relocation:
            signals.append(PositiveSignal.RELOCATION_AVAILABLE)
        if sponsorship:
            signals.append(PositiveSignal.VISA_SPONSORSHIP_AVAILABLE)
        if relocation or sponsorship:
            return RelocationStatus.AVAILABLE, tuple(signals)
        return RelocationStatus.UNKNOWN, ()

    def _geography(
        self,
        text: _VacancyText,
    ) -> tuple[
        GeographyScope,
        tuple[PositiveSignal, ...],
        tuple[WarningCode, ...],
    ]:
        combined = text.combined
        signals: list[PositiveSignal] = []
        warnings: list[WarningCode] = []
        if _has(combined, r"\b(?:worldwide|work from anywhere|global remote)\b"):
            signals.append(PositiveSignal.REMOTE_WORLDWIDE)
            scope = GeographyScope.WORLDWIDE
        elif _has(combined, r"\bemea\b"):
            signals.append(PositiveSignal.REMOTE_EMEA)
            scope = GeographyScope.EMEA
        elif _has(combined, r"\b(?:eu only|european union only)\b"):
            warnings.append(WarningCode.EU_ONLY)
            scope = GeographyScope.EU_ONLY
        elif _has(combined, r"\b(?:europe|european time zones?)\b"):
            signals.append(PositiveSignal.REMOTE_EUROPE)
            scope = GeographyScope.EUROPE
        elif _has(combined, r"\b(?:us|u\.s\.|united states) only\b"):
            warnings.append(WarningCode.US_ONLY)
            scope = GeographyScope.US_ONLY
        elif _has(combined, r"\b(?:uk|united kingdom) only\b"):
            warnings.append(WarningCode.UK_ONLY)
            scope = GeographyScope.UK_ONLY
        elif _has(
            combined,
            r"\bmust (?:live|reside|be located|be based) in\b",
            r"\bremote (?:within|in) [a-z]",
            r"\bremote[ ,.-]+(?:canada|germany|poland|netherlands|cyprus|greece|"
            r"uae|united arab emirates|india|australia|new zealand)\b",
        ):
            warnings.append(WarningCode.COUNTRY_RESTRICTED)
            scope = GeographyScope.COUNTRY_ONLY
        else:
            scope = GeographyScope.UNKNOWN

        if _has(
            combined,
            r"\b(?:existing|current|valid) work authori[sz]ation (?:is )?required\b",
            r"\bmust be authori[sz]ed to work\b",
            r"\bwithout (?:current or future )?sponsorship\b",
        ):
            warnings.append(WarningCode.WORK_AUTHORIZATION_REQUIRED)
        if _has(
            combined,
            r"\b(?:visa )?sponsorship (?:is )?not (?:available|provided|offered)\b",
            r"\bwe (?:cannot|do not) sponsor\b",
        ):
            warnings.append(WarningCode.VISA_SPONSORSHIP_UNAVAILABLE)
        return scope, tuple(signals), tuple(warnings)

    def _work_eligibility(
        self,
        work_mode: WorkMode,
        relocation: RelocationStatus,
        geography: GeographyScope,
    ) -> tuple[tuple[WarningCode, ...], tuple[RejectionReason, ...]]:
        if work_mode is WorkMode.ONSITE:
            if relocation is RelocationStatus.UNAVAILABLE:
                return (), (RejectionReason.ONSITE_NO_RELOCATION,)
            if relocation is RelocationStatus.UNKNOWN:
                return (WarningCode.RELOCATION_UNCLEAR,), ()
        if work_mode is WorkMode.HYBRID:
            if relocation is RelocationStatus.UNAVAILABLE:
                return (), (RejectionReason.HYBRID_NO_RELOCATION,)
            if relocation is RelocationStatus.UNKNOWN:
                return (WarningCode.RELOCATION_UNCLEAR,), ()
        if work_mode is WorkMode.UNKNOWN:
            return (WarningCode.WORK_MODE_UNCLEAR,), ()
        if work_mode is WorkMode.REMOTE and geography is GeographyScope.UNKNOWN:
            return (WarningCode.REMOTE_GEOGRAPHY_UNCLEAR,), ()
        return (), ()

    def _languages(
        self,
        text: _VacancyText,
    ) -> tuple[tuple[WarningCode, ...], tuple[RejectionReason, ...]]:
        combined = text.combined
        unsupported = (
            "german",
            "polish",
            "dutch",
            "french",
            "spanish",
            "italian",
            "portuguese",
            "arabic",
            "japanese",
            "mandarin",
            "chinese",
        )
        preferred = False
        for language in unsupported:
            required = _has(
                combined,
                rf"\b(?:fluent|native|c1|c2) (?:in )?{language}\b",
                rf"\b{language}\b.{{0,25}}\b(?:required|mandatory|must)\b",
                rf"\b(?:required|mandatory|must speak)\b.{{0,25}}\b{language}\b",
            )
            optional = _has(
                combined,
                rf"\b{language}\b.{{0,25}}\b(?:preferred|optional|nice to have|plus)\b",
                rf"\b(?:preferred|optional|nice to have)\b.{{0,25}}\b{language}\b",
            )
            if required and not optional:
                return (), (RejectionReason.UNSUPPORTED_REQUIRED_LANGUAGE,)
            preferred = preferred or optional
        if preferred:
            return (WarningCode.OTHER_LANGUAGE_PREFERRED,), ()
        return (), ()

    def _positive_signals(self, text: _VacancyText) -> tuple[PositiveSignal, ...]:
        signals: list[PositiveSignal] = []
        checks = (
            (PositiveSignal.IOS_TITLE, text.title, r"\bios\b"),
            (PositiveSignal.SWIFT_TITLE, text.title, r"\bswift\b"),
            (PositiveSignal.IOS_DESCRIPTION, text.description, r"\bios\b"),
            (PositiveSignal.SWIFTUI, text.combined, r"\bswiftui\b"),
            (PositiveSignal.UIKIT, text.combined, r"\buikit\b"),
            (
                PositiveSignal.SWIFT_CONCURRENCY,
                text.combined,
                r"\bswift concurrency\b",
            ),
            (
                PositiveSignal.APPLE_PLATFORMS,
                text.combined,
                r"\bapple (?:platforms?|frameworks?)\b",
            ),
            (PositiveSignal.XCODE, text.combined, r"\bxcode\b"),
            (PositiveSignal.IOS_SDK, text.combined, r"\bios sdk\b"),
            (PositiveSignal.XCTEST, text.combined, r"\b(?:xctest|swift testing)\b"),
            (PositiveSignal.COMBINE, text.combined, r"\bcombine\b"),
            (PositiveSignal.APP_STORE, text.combined, r"\bapp store\b"),
            (PositiveSignal.FINTECH_DOMAIN, text.combined, r"\bfintech\b"),
            (PositiveSignal.WEB3_DOMAIN, text.combined, r"\bweb3\b"),
            (PositiveSignal.CRYPTO_DOMAIN, text.combined, r"\bcrypto(?:currency)?\b"),
            (PositiveSignal.BLOCKCHAIN_DOMAIN, text.combined, r"\bblockchain\b"),
            (
                PositiveSignal.AI_ML_DOMAIN,
                text.combined,
                r"\b(?:artificial intelligence|machine learning|ai/ml)\b",
            ),
        )
        for signal, value, pattern in checks:
            if _has(value, pattern):
                signals.append(signal)
        if _has_technical_swift(text.description):
            signals.append(PositiveSignal.SWIFT_REQUIRED)
        return tuple(signals)
