from datetime import datetime

from pydantic import BaseModel, ConfigDict, HttpUrl


class AshbyJobDTO(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str | None = None
    title: str
    location: str | None = None
    isListed: bool = True
    isRemote: bool | None = None
    workplaceType: str | None = None
    descriptionPlain: str = ""
    publishedAt: datetime | None = None
    jobUrl: HttpUrl
    applyUrl: HttpUrl | None = None


class AshbyJobsEnvelopeDTO(BaseModel):
    model_config = ConfigDict(extra="ignore")

    apiVersion: str
    jobs: list[object]
