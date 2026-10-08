from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator

from job_search.domain.enums import ATSType, RegistryProvenance


class CompanyImportDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255)
    country: str | None = Field(default=None, max_length=120)
    careers_url: HttpUrl
    ats_type: ATSType
    ats_identifier: str = Field(min_length=1, max_length=255)
    priority: int = Field(default=50, ge=0)
    enabled: bool = True
    provenance: RegistryProvenance
    source_verified_at: datetime

    @field_validator("ats_type")
    @classmethod
    def supported_ats_only(cls, value: ATSType) -> ATSType:
        if value not in {
            ATSType.GREENHOUSE,
            ATSType.LEVER,
            ATSType.ASHBY,
            ATSType.RECRUITEE,
            ATSType.AGILEFLUENT,
            ATSType.HH,
        }:
            raise ValueError(
                "registry import supports Greenhouse, Lever, Ashby, Recruitee, "
                "AgileFluent, and hh.ru"
            )
        return value

    @field_validator("source_verified_at")
    @classmethod
    def verified_at_is_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("source_verified_at must be timezone-aware")
        return value


class CompanyRegistryDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = Field(ge=1)
    companies: list[CompanyImportDTO]
