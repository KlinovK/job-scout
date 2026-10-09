from pydantic import AnyHttpUrl, BaseModel, ConfigDict, JsonValue


class GreenhouseLocationDTO(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str


class GreenhouseJobDTO(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: int
    title: str
    absolute_url: AnyHttpUrl
    content: str = ""
    location: GreenhouseLocationDTO | None = None


class GreenhouseJobsMetaDTO(BaseModel):
    model_config = ConfigDict(extra="ignore")

    total: int


class GreenhouseJobsEnvelopeDTO(BaseModel):
    model_config = ConfigDict(extra="ignore")

    jobs: list[JsonValue]
    meta: GreenhouseJobsMetaDTO | None = None
