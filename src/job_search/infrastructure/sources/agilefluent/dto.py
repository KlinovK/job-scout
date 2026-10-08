from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class AgileFluentJobDTO(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str = Field(min_length=1)
    companyName: str = Field(min_length=1)
    title: str = Field(min_length=1)
    role: str = "unknown"
    grade: str = "unknown"
    format: str | None = None
    formats: list[str] = Field(default_factory=list)
    country: str | None = None
    countries: list[str] = Field(default_factory=list)
    city: str | None = None
    industry: str | None = None
    visa: bool = False
    description: str = ""
    skills: list[str] = Field(default_factory=list)
    createdAtIso: datetime | None = None


class AgileFluentJobsEnvelopeDTO(BaseModel):
    model_config = ConfigDict(extra="ignore")

    data: list[object]
    hasMore: bool
