from pydantic import BaseModel, ConfigDict, Field, HttpUrl, RootModel


class LeverCategoriesDTO(BaseModel):
    model_config = ConfigDict(extra="ignore")

    location: str | None = None
    allLocations: list[str] = Field(default_factory=list)


class LeverPostingDTO(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    text: str
    categories: LeverCategoriesDTO
    descriptionPlain: str = ""
    hostedUrl: HttpUrl
    applyUrl: HttpUrl | None = None
    workplaceType: str | None = None
    createdAt: int | None = None


class LeverPostingsEnvelopeDTO(RootModel[list[object]]):
    pass
