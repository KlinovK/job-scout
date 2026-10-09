import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config


def test_initial_migration_creates_constraints_and_indexes(
    tmp_path: Path,
    monkeypatch,
) -> None:
    database_path = tmp_path / "migration.db"
    monkeypatch.setenv(
        "JOB_SEARCH_DATABASE_URL",
        f"sqlite+aiosqlite:///{database_path}",
    )
    config = Config("alembic.ini")

    command.upgrade(config, "head")

    connection = sqlite3.connect(database_path)
    try:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        vacancy_indexes = {
            row[1] for row in connection.execute("PRAGMA index_list('job_vacancies')")
        }
        observation_indexes = {
            row[1]
            for row in connection.execute("PRAGMA index_list('vacancy_observations')")
        }
        company_indexes = {
            row[1] for row in connection.execute("PRAGMA index_list('companies')")
        }
        company_columns = {
            row[1] for row in connection.execute("PRAGMA table_info('companies')")
        }
        classification_indexes = {
            row[1]
            for row in connection.execute(
                "PRAGMA index_list('vacancy_classifications')"
            )
        }
        vacancy_columns = {
            row[1] for row in connection.execute("PRAGMA table_info('job_vacancies')")
        }
        observation_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info('vacancy_observations')")
        }
        observation_foreign_keys = {
            (row[2], row[3], row[4], row[6])
            for row in connection.execute(
                "PRAGMA foreign_key_list('vacancy_observations')"
            )
        }
    finally:
        connection.close()

    assert {
        "companies",
        "job_vacancies",
        "vacancy_observations",
        "vacancy_classifications",
        "alembic_version",
    } <= tables
    assert "ix_job_vacancies_company_id" in vacancy_indexes
    assert "ix_job_vacancies_status" in vacancy_indexes
    assert any("autoindex" in name for name in vacancy_indexes)
    assert "employer_name" in vacancy_columns
    assert {
        "canonical_url",
        "original_source",
        "salary",
        "employment",
        "experience",
        "closed_at",
    } <= vacancy_columns
    assert any("autoindex" in name for name in company_indexes)
    assert {
        "provenance",
        "source_verified_at",
        "last_success_at",
        "last_job_count",
        "last_status",
        "last_error_category",
        "last_complete_snapshot_at",
    } <= company_columns
    assert {
        "source_company_id",
        "status",
        "closed_at",
        "missing_complete_snapshots",
    } <= observation_columns
    assert {
        "uq_vacancy_observations_owned_source_identity",
        "uq_vacancy_observations_unowned_source_identity",
        "ix_vacancy_observations_company_reconciliation",
    } <= observation_indexes
    assert (
        "companies",
        "source_company_id",
        "id",
        "SET NULL",
    ) in observation_foreign_keys
    assert "ix_vacancy_classifications_version_decision" in classification_indexes
    assert any("autoindex" in name for name in classification_indexes)


def test_phase_3b_migration_backfills_existing_vacancy_observation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    database_path = tmp_path / "existing.db"
    monkeypatch.setenv(
        "JOB_SEARCH_DATABASE_URL",
        f"sqlite+aiosqlite:///{database_path}",
    )
    config = Config("alembic.ini")
    command.upgrade(config, "20260924_0004")
    connection = sqlite3.connect(database_path)
    try:
        connection.execute(
            "INSERT INTO companies "
            "(id, name, country, careers_url, ats_type, ats_identifier, "
            "priority, enabled, created_at, updated_at, provenance) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "00000000-0000-0000-0000-000000000001",
                "Existing",
                None,
                "https://example.com/careers",
                "greenhouse",
                "existing",
                10,
                1,
                "2026-09-17T08:00:00+00:00",
                "2026-09-17T08:00:00+00:00",
                "greenhouse",
            ),
        )
        connection.execute(
            "INSERT INTO job_vacancies "
            "(id, company_id, title, description, employer_name, remote_policy, "
            "url, source, source_job_id, first_seen_at, last_seen_at, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "00000000-0000-0000-0000-000000000002",
                "00000000-0000-0000-0000-000000000001",
                "Senior iOS Engineer",
                "Swift",
                "Existing",
                "remote",
                "https://example.com/jobs/42?utm_source=test",
                "greenhouse",
                "42",
                "2026-09-17T08:00:00+00:00",
                "2026-09-17T08:00:00+00:00",
                "active",
            ),
        )
        connection.commit()
    finally:
        connection.close()

    command.upgrade(config, "head")

    connection = sqlite3.connect(database_path)
    try:
        vacancy = connection.execute(
            "SELECT canonical_url, original_source FROM job_vacancies"
        ).fetchone()
        observation = connection.execute(
            "SELECT discovered_via, source_job_id, canonical_url "
            "FROM vacancy_observations"
        ).fetchone()
    finally:
        connection.close()

    assert vacancy == ("https://example.com/jobs/42", "greenhouse")
    assert observation == ("greenhouse", "42", "https://example.com/jobs/42")


def test_lifecycle_migration_preserves_data_and_backfills_only_exact_ownership(
    tmp_path: Path,
    monkeypatch,
) -> None:
    database_path = tmp_path / "lifecycle-existing.db"
    monkeypatch.setenv(
        "JOB_SEARCH_DATABASE_URL",
        f"sqlite+aiosqlite:///{database_path}",
    )
    config = Config("alembic.ini")
    command.upgrade(config, "20260927_0005")

    company_a = "00000000-0000-0000-0000-000000000001"
    company_b = "00000000-0000-0000-0000-000000000002"
    vacancy_exact = "00000000-0000-0000-0000-000000000011"
    vacancy_ambiguous = "00000000-0000-0000-0000-000000000012"
    observation_exact = "00000000-0000-0000-0000-000000000021"
    observation_ambiguous = "00000000-0000-0000-0000-000000000022"
    first_seen = "2026-09-17T08:00:00+00:00"
    last_seen = "2026-09-18T08:00:00+00:00"

    connection = sqlite3.connect(database_path)
    try:
        connection.executemany(
            "INSERT INTO companies "
            "(id, name, country, careers_url, ats_type, ats_identifier, "
            "priority, enabled, created_at, updated_at, provenance) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    company_a,
                    "Company A",
                    None,
                    "https://a.example/careers",
                    "greenhouse",
                    "company-a",
                    10,
                    1,
                    first_seen,
                    first_seen,
                    "manual",
                ),
                (
                    company_b,
                    "Company B",
                    None,
                    "https://b.example/careers",
                    "greenhouse",
                    "company-b",
                    10,
                    1,
                    first_seen,
                    first_seen,
                    "manual",
                ),
            ],
        )
        connection.executemany(
            "INSERT INTO job_vacancies "
            "(id, company_id, title, description, remote_policy, url, source, "
            "source_job_id, first_seen_at, last_seen_at, status, canonical_url, "
            "original_source) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    vacancy_exact,
                    company_a,
                    "Exact",
                    "Swift",
                    "remote",
                    "https://a.example/jobs/exact",
                    "greenhouse",
                    "exact-id",
                    first_seen,
                    last_seen,
                    "closed",
                    "https://a.example/jobs/exact",
                    "greenhouse",
                ),
                (
                    vacancy_ambiguous,
                    company_b,
                    "Ambiguous",
                    "Swift",
                    "remote",
                    "https://b.example/jobs/official",
                    "greenhouse",
                    "official-id",
                    first_seen,
                    last_seen,
                    "closed",
                    "https://b.example/jobs/official",
                    "greenhouse",
                ),
            ],
        )
        connection.executemany(
            "INSERT INTO vacancy_observations "
            "(id, vacancy_id, discovered_via, source_job_id, original_source, "
            "observation_url, canonical_url, title, first_seen_at, last_seen_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    observation_exact,
                    vacancy_exact,
                    "greenhouse",
                    "exact-id",
                    "greenhouse",
                    "https://a.example/jobs/exact",
                    "https://a.example/jobs/exact",
                    "Exact",
                    first_seen,
                    last_seen,
                ),
                (
                    observation_ambiguous,
                    vacancy_ambiguous,
                    "agilefluent",
                    "aggregated-id",
                    "greenhouse",
                    "https://feed.example/jobs/aggregated-id",
                    "https://b.example/jobs/official",
                    "Ambiguous",
                    first_seen,
                    last_seen,
                ),
            ],
        )
        connection.execute(
            "INSERT INTO vacancy_classifications "
            "(vacancy_id, classifier_version, decision, role_category, seniority, "
            "ios_relevance, work_mode, relocation, geography, positive_signals, "
            "warnings, rejection_reasons, classified_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                vacancy_exact,
                "ios-v1",
                "match",
                "ios",
                "senior",
                "strong",
                "remote",
                "unknown",
                "europe",
                "swift",
                "",
                "",
                last_seen,
            ),
        )
        connection.commit()
    finally:
        connection.close()

    command.upgrade(config, "head")

    connection = sqlite3.connect(database_path)
    connection.execute("PRAGMA foreign_keys=ON")
    try:
        observations = connection.execute(
            "SELECT id, source_company_id, status, closed_at, "
            "missing_complete_snapshots, first_seen_at, last_seen_at "
            "FROM vacancy_observations ORDER BY id"
        ).fetchall()
        vacancies = connection.execute(
            "SELECT id, status, closed_at, first_seen_at, last_seen_at "
            "FROM job_vacancies ORDER BY id"
        ).fetchall()
        classification = connection.execute(
            "SELECT vacancy_id, classifier_version, decision "
            "FROM vacancy_classifications"
        ).fetchone()
        company_watermarks = connection.execute(
            "SELECT last_complete_snapshot_at FROM companies ORDER BY id"
        ).fetchall()

        assert observations == [
            (
                observation_exact,
                company_a,
                "closed",
                None,
                0,
                first_seen,
                last_seen,
            ),
            (
                observation_ambiguous,
                None,
                "active",
                None,
                0,
                first_seen,
                last_seen,
            ),
        ]
        assert vacancies == [
            (vacancy_exact, "closed", None, first_seen, last_seen),
            (vacancy_ambiguous, "closed", None, first_seen, last_seen),
        ]
        assert classification == (vacancy_exact, "ios-v1", "match")
        assert company_watermarks == [(None,), (None,)]

        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE vacancy_observations "
                "SET missing_complete_snapshots = -1 WHERE id = ?",
                (observation_exact,),
            )
        connection.rollback()

        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE vacancy_observations SET status = 'unknown' WHERE id = ?",
                (observation_exact,),
            )
        connection.rollback()

        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE vacancy_observations SET source_company_id = ? WHERE id = ?",
                ("00000000-0000-0000-0000-999999999999", observation_exact),
            )
        connection.rollback()

        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO vacancy_observations "
                "(id, vacancy_id, source_company_id, discovered_via, "
                "source_job_id, observation_url, title, first_seen_at, "
                "last_seen_at, status, missing_complete_snapshots) "
                "VALUES (?, ?, NULL, ?, ?, ?, ?, ?, ?, 'active', 0)",
                (
                    "00000000-0000-0000-0000-000000000031",
                    vacancy_ambiguous,
                    "agilefluent",
                    "aggregated-id",
                    "https://feed.example/jobs/duplicate",
                    "Duplicate unowned",
                    first_seen,
                    last_seen,
                ),
            )
        connection.rollback()

        connection.execute(
            "INSERT INTO vacancy_observations "
            "(id, vacancy_id, source_company_id, discovered_via, source_job_id, "
            "observation_url, title, first_seen_at, last_seen_at, status, "
            "missing_complete_snapshots) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', 0)",
            (
                "00000000-0000-0000-0000-000000000032",
                vacancy_ambiguous,
                company_b,
                "greenhouse",
                "exact-id",
                "https://b.example/jobs/exact",
                "Same provider ID, different company",
                first_seen,
                last_seen,
            ),
        )
        connection.commit()

        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO vacancy_observations "
                "(id, vacancy_id, source_company_id, discovered_via, "
                "source_job_id, observation_url, title, first_seen_at, "
                "last_seen_at, status, missing_complete_snapshots) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', 0)",
                (
                    "00000000-0000-0000-0000-000000000033",
                    vacancy_ambiguous,
                    company_b,
                    "greenhouse",
                    "exact-id",
                    "https://b.example/jobs/exact-duplicate",
                    "Duplicate owned",
                    first_seen,
                    last_seen,
                ),
            )
        connection.rollback()
    finally:
        connection.close()
