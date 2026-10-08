from collections.abc import Mapping

from job_search.application.ports import JobSource
from job_search.domain.enums import ATSType


class ConfiguredJobSourceProvider:
    def __init__(self, sources: Mapping[ATSType, JobSource]) -> None:
        self._sources = dict(sources)

    def get_source(self, ats_type: ATSType) -> JobSource | None:
        return self._sources.get(ats_type)
