from datetime import datetime
from pathlib import Path

from job_search.domain.models import Company
from job_search.infrastructure.registry import load_company_registry

DEFAULT_REGISTRY_PATH = Path(__file__).parents[3] / "data" / "companies.json"


def development_companies(now: datetime) -> tuple[Company, ...]:
    return load_company_registry(DEFAULT_REGISTRY_PATH, now)
