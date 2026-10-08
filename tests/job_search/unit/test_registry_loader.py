import json
from pathlib import Path

import pytest

from job_search.domain.enums import ATSType, RegistryProvenance
from job_search.infrastructure.registry import (
    RegistryImportError,
    load_company_registry,
)
from tests.job_search.factories import NOW


def _entry(
    *,
    name: str = "Example",
    ats_type: str = "greenhouse",
    identifier: str = "example",
) -> dict[str, object]:
    return {
        "name": name,
        "country": "Netherlands",
        "careers_url": f"https://jobs.example.com/{identifier}",
        "ats_type": ats_type,
        "ats_identifier": identifier,
        "priority": 80,
        "enabled": True,
        "provenance": ats_type,
        "source_verified_at": "2026-09-17T08:00:00Z",
    }


def _write_registry(path: Path, companies: list[dict[str, object]]) -> None:
    path.write_text(
        json.dumps({"version": 1, "companies": companies}),
        encoding="utf-8",
    )


def test_valid_registry_is_normalized_and_has_stable_identity(tmp_path: Path) -> None:
    path = tmp_path / "companies.json"
    _write_registry(
        path,
        [
            _entry(name=" Example ", identifier=" Example-Board "),
            _entry(name="Remote Inc", ats_type="ashby", identifier="Remote"),
        ],
    )

    first = load_company_registry(path, NOW)
    second = load_company_registry(path, NOW)

    assert len(first) == 2
    assert first[0].id == second[0].id
    assert first[0].name == "Example"
    assert first[0].ats_identifier == "example-board"
    assert first[0].ats_type is ATSType.GREENHOUSE
    assert first[0].provenance is RegistryProvenance.GREENHOUSE
    assert first[1].ats_type is ATSType.ASHBY


def test_agilefluent_registry_entry_is_supported(tmp_path: Path) -> None:
    path = tmp_path / "companies.json"
    entry = _entry(
        name="AgileFluent Job Board",
        ats_type="agilefluent",
        identifier="jobboard.agilefluent.ru",
    )
    _write_registry(path, [entry])

    companies = load_company_registry(path, NOW)

    assert companies[0].ats_type is ATSType.AGILEFLUENT
    assert companies[0].provenance is RegistryProvenance.AGILEFLUENT


def test_recruitee_registry_entry_is_supported(tmp_path: Path) -> None:
    path = tmp_path / "companies.json"
    entry = _entry(
        name="Publitas.com",
        ats_type="recruitee",
        identifier="publitas",
    )
    _write_registry(path, [entry])

    companies = load_company_registry(path, NOW)

    assert companies[0].ats_type is ATSType.RECRUITEE
    assert companies[0].provenance is RegistryProvenance.RECRUITEE


@pytest.mark.parametrize(
    "contents",
    [
        "{",
        json.dumps({"version": 1}),
        json.dumps({"version": 1, "companies": [{"name": "Incomplete"}]}),
    ],
)
def test_malformed_registry_is_rejected_as_a_whole(
    tmp_path: Path,
    contents: str,
) -> None:
    path = tmp_path / "companies.json"
    path.write_text(contents, encoding="utf-8")

    with pytest.raises(RegistryImportError, match="Invalid company registry"):
        load_company_registry(path, NOW)


def test_duplicate_ats_identity_is_rejected_case_insensitively(
    tmp_path: Path,
) -> None:
    path = tmp_path / "companies.json"
    _write_registry(
        path,
        [
            _entry(name="One", identifier="Board"),
            _entry(name="Two", identifier="board"),
        ],
    )

    with pytest.raises(RegistryImportError, match="Duplicate ATS identity"):
        load_company_registry(path, NOW)


def test_duplicate_company_name_is_rejected_case_insensitively(
    tmp_path: Path,
) -> None:
    path = tmp_path / "companies.json"
    _write_registry(
        path,
        [
            _entry(name="Same", identifier="one"),
            _entry(name="same", identifier="two"),
        ],
    )

    with pytest.raises(RegistryImportError, match="Duplicate company name"):
        load_company_registry(path, NOW)


@pytest.mark.parametrize("ats_type", ["custom", "workday", "unknown"])
def test_unsupported_provider_is_rejected(tmp_path: Path, ats_type: str) -> None:
    path = tmp_path / "companies.json"
    entry = _entry(ats_type=ats_type)
    entry["provenance"] = "manual"
    _write_registry(path, [entry])

    with pytest.raises(RegistryImportError, match="Invalid company registry"):
        load_company_registry(path, NOW)


def test_naive_verification_timestamp_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "companies.json"
    entry = _entry()
    entry["source_verified_at"] = "2026-09-17T08:00:00"
    _write_registry(path, [entry])

    with pytest.raises(RegistryImportError, match="timezone-aware"):
        load_company_registry(path, NOW)
