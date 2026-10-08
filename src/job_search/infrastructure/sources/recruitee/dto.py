from datetime import datetime

from pydantic import (
    AnyHttpUrl,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    field_validator,
)


class RecruiteeLocationDTO(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str | None = None
    state: str | None = None
    country: str | None = None
    city: str | None = None


class RecruiteeSalaryDTO(BaseModel):
    model_config = ConfigDict(extra="ignore")

    min: str | None = None
    max: str | None = None
    period: str | None = None
    currency: str | None = None


class RecruiteeOfferDTO(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: int | str
    title: str
    careers_url: AnyHttpUrl
    company_name: str | None = None
    description: str = ""
    requirements: str | None = None
    location: str | None = None
    locations: list[RecruiteeLocationDTO] = Field(default_factory=list)
    remote: bool = False
    hybrid: bool = False
    on_site: bool = False
    published_at: datetime | None = None
    salary: RecruiteeSalaryDTO | None = None
    employment_type_code: str | None = None
    experience_code: str | None = None

    @field_validator("published_at", mode="before")
    @classmethod
    def normalize_utc_suffix(cls, value: object) -> object:
        if isinstance(value, str) and value.endswith(" UTC"):
            return f"{value[:-4]}+00:00"
        return value


class RecruiteeOffersEnvelopeDTO(BaseModel):
    model_config = ConfigDict(extra="ignore")

    offers: list[JsonValue]
