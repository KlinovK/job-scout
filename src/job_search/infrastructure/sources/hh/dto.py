from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class HHNamedValueDTO(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str | None = None
    name: str


class HHEmployerDTO(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str | None = None
    name: str


class HHSalaryDTO(BaseModel):
    model_config = ConfigDict(extra="ignore")

    from_: int | float | None = Field(default=None, alias="from")
    to: int | float | None = None
    currency: str | None = None
    gross: bool | None = None


class HHSearchItemDTO(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str


class HHSearchEnvelopeDTO(BaseModel):
    model_config = ConfigDict(extra="ignore")

    items: list[HHSearchItemDTO]
    found: int
    pages: int
    page: int
    per_page: int


class HHVacancyDTO(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    id: str
    name: str
    description: str
    alternate_url: str | None
    employer: HHEmployerDTO
    area: HHNamedValueDTO | None = None
    salary: HHSalaryDTO | None = None
    experience: HHNamedValueDTO | None = None
    employment: HHNamedValueDTO | None = None
    schedule: HHNamedValueDTO | None = None
    work_format: list[HHNamedValueDTO] = Field(default_factory=list)
    published_at: datetime
    archived: bool = False
