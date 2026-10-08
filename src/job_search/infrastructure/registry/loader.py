from datetime import UTC, datetime
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from pydantic import ValidationError

from job_search.domain.models import Company
from job_search.infrastructure.registry.dto import CompanyRegistryDTO


class RegistryImportError(ValueError):
    """A registry file failed whole-file validation."""


def load_company_registry(path: Path, now: datetime) -> tuple[Company, ...]:
    try:
        payload = CompanyRegistryDTO.model_validate_json(path.read_bytes())
    except (OSError, ValidationError) as exc:
        raise RegistryImportError(f"Invalid company registry {path}: {exc}") from exc

    identities: set[tuple[str, str]] = set()
    names: set[str] = set()
    companies: list[Company] = []
    for entry in payload.companies:
        identifier = entry.ats_identifier.strip().casefold()
        identity = (entry.ats_type.value, identifier)
        normalized_name = entry.name.strip().casefold()
        if identity in identities:
            raise RegistryImportError(
                f"Duplicate ATS identity: {entry.ats_type.value}/{identifier}"
            )
        if normalized_name in names:
            raise RegistryImportError(f"Duplicate company name: {entry.name.strip()}")
        identities.add(identity)
        names.add(normalized_name)
        companies.append(
            Company(
                id=uuid5(NAMESPACE_URL, f"{entry.ats_type.value}:{identifier}"),
                name=entry.name.strip(),
                country=entry.country.strip() if entry.country else None,
                careers_url=str(entry.careers_url),
                ats_type=entry.ats_type,
                ats_identifier=identifier,
                priority=entry.priority,
                enabled=entry.enabled,
                last_checked_at=None,
                created_at=now,
                updated_at=now,
                provenance=entry.provenance,
                source_verified_at=entry.source_verified_at.astimezone(UTC),
            )
        )
    return tuple(companies)
