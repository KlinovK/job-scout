import sqlite3
from pathlib import Path

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
    assert any("autoindex" in name for name in vacancy_indexes)
    assert "employer_name" in vacancy_columns
    assert {
        "canonical_url",
        "original_source",
        "salary",
        "employment",
        "experience",
    } <= vacancy_columns
    assert any("autoindex" in name for name in company_indexes)
    assert {
        "provenance",
        "source_verified_at",
        "last_success_at",
        "last_job_count",
        "last_status",
        "last_error_category",
    } <= company_columns
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
