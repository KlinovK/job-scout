from enum import StrEnum


class ATSType(StrEnum):
    GREENHOUSE = "greenhouse"
    LEVER = "lever"
    ASHBY = "ashby"
    RECRUITEE = "recruitee"
    AGILEFLUENT = "agilefluent"
    HH = "hh"
    WORKABLE = "workable"
    TEAMTAILOR = "teamtailor"
    SMARTRECRUITERS = "smartrecruiters"
    CUSTOM = "custom"


class VacancySource(StrEnum):
    GREENHOUSE = "greenhouse"
    LEVER = "lever"
    ASHBY = "ashby"
    RECRUITEE = "recruitee"
    AGILEFLUENT = "agilefluent"
    HH = "hh"
    DREAMOFFER = "dreamoffer"
    LINKEDIN = "linkedin"
    WORKABLE = "workable"
    TEAMTAILOR = "teamtailor"
    SMARTRECRUITERS = "smartrecruiters"
    CUSTOM = "custom"


class RemotePolicy(StrEnum):
    UNKNOWN = "unknown"
    REMOTE = "remote"
    HYBRID = "hybrid"
    ONSITE = "onsite"


class VacancyStatus(StrEnum):
    ACTIVE = "active"
    CLOSED = "closed"


class SourceHealthStatus(StrEnum):
    HEALTHY = "healthy"
    EMPTY = "empty"
    FAILED = "failed"
    INVALID_CONFIGURATION = "invalid_configuration"


class RegistryProvenance(StrEnum):
    MANUAL = "manual"
    GREENHOUSE = "greenhouse"
    LEVER = "lever"
    ASHBY = "ashby"
    RECRUITEE = "recruitee"
    AGILEFLUENT = "agilefluent"
    HH = "hh"
    FUTURE_WEB_DISCOVERY = "future_web_discovery"
