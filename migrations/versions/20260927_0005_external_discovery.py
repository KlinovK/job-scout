"""Add external discovery provenance and canonical identity."""

import re
from collections.abc import Sequence
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import NAMESPACE_URL, uuid5

import sqlalchemy as sa
from alembic import op

revision: str = "20260927_0005"
down_revision: str | None = "20260924_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TRACKING = {
    "fbclid",
    "gclid",
    "gh_src",
    "pagenum",
    "position",
    "refid",
    "trackingid",
}


def _normalize_url(value: str) -> str:
    parts = urlsplit(value.strip())
    scheme = parts.scheme.casefold()
    host = (parts.hostname or "").casefold()
    port = parts.port
    if port is not None and not (
        (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
    ):
        host = f"{host}:{port}"
    path = re.sub(r"/{2,}", "/", parts.path).rstrip("/") or "/"
    query = urlencode(
        sorted(
            (key, item)
            for key, item in parse_qsl(parts.query, keep_blank_values=True)
            if not key.casefold().startswith("utm_") and key.casefold() not in _TRACKING
        ),
        doseq=True,
    )
    return urlunsplit((scheme, host, path, query, ""))


def _original_reference(value: str) -> str | None:
    parts = urlsplit(value)
    host = (parts.hostname or "").casefold()
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    greenhouse_id = query.get("gh_jid")
    if greenhouse_id and greenhouse_id.isdigit():
        return f"greenhouse:{greenhouse_id}"
    patterns = (
        ("greenhouse", "greenhouse.io", r"/(?:[^/]+/)?jobs/(\d+)(?:/|$)"),
        ("lever", "jobs.lever.co", r"/[^/]+/([0-9a-f-]{20,})(?:/|$)"),
        ("ashby", "jobs.ashbyhq.com", r"/[^/]+/([0-9a-f-]{20,})(?:/|$)"),
        ("hh", "hh.ru", r"/vacancy/(\d+)(?:/|$)"),
        ("linkedin", "linkedin.com", r"/jobs/view/(?:.*-)?(\d{6,})(?:/|$)"),
    )
    for source, expected_host, pattern in patterns:
        if expected_host not in host:
            continue
        match = re.search(pattern, parts.path, re.IGNORECASE)
        if match:
            return f"{source}:{match.group(1).casefold()}"
    return None


def upgrade() -> None:
    op.add_column("job_vacancies", sa.Column("canonical_url", sa.Text()))
    op.add_column("job_vacancies", sa.Column("original_source", sa.String(length=32)))
    op.add_column("job_vacancies", sa.Column("salary", sa.String(length=255)))
    op.add_column("job_vacancies", sa.Column("employment", sa.String(length=255)))
    op.add_column("job_vacancies", sa.Column("experience", sa.String(length=255)))
    op.create_index(
        "ix_job_vacancies_canonical_url",
        "job_vacancies",
        ["canonical_url"],
    )
    op.create_table(
        "vacancy_observations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("vacancy_id", sa.String(length=36), nullable=False),
        sa.Column("discovered_via", sa.String(length=32), nullable=False),
        sa.Column("source_job_id", sa.String(length=255), nullable=False),
        sa.Column("original_source", sa.String(length=32)),
        sa.Column("observation_url", sa.Text(), nullable=False),
        sa.Column("canonical_url", sa.Text()),
        sa.Column("original_reference", sa.String(length=512)),
        sa.Column("employer_name", sa.String(length=255)),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("work_location", sa.String(length=500)),
        sa.Column("first_seen_at", sa.String(length=40), nullable=False),
        sa.Column("last_seen_at", sa.String(length=40), nullable=False),
        sa.ForeignKeyConstraint(
            ["vacancy_id"],
            ["job_vacancies.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "discovered_via",
            "source_job_id",
            name="uq_vacancy_observations_source_identity",
        ),
    )
    op.create_index(
        "ix_vacancy_observations_vacancy_id",
        "vacancy_observations",
        ["vacancy_id"],
    )
    op.create_index(
        "ix_vacancy_observations_canonical_url",
        "vacancy_observations",
        ["canonical_url"],
    )
    op.create_index(
        "ix_vacancy_observations_original_reference",
        "vacancy_observations",
        ["original_reference"],
    )

    connection = op.get_bind()
    rows = connection.execute(
        sa.text(
            "SELECT id, source, source_job_id, url, employer_name, title, "
            "work_location, first_seen_at, last_seen_at FROM job_vacancies"
        )
    ).mappings()
    observations: list[dict[str, object]] = []
    for row in rows:
        canonical_url = _normalize_url(str(row["url"]))
        connection.execute(
            sa.text(
                "UPDATE job_vacancies SET canonical_url = :canonical_url, "
                "original_source = :original_source WHERE id = :id"
            ),
            {
                "canonical_url": canonical_url,
                "original_source": row["source"],
                "id": row["id"],
            },
        )
        identity = f"{row['source']}:{row['source_job_id']}"
        observations.append(
            {
                "id": str(uuid5(NAMESPACE_URL, f"vacancy-observation:{identity}")),
                "vacancy_id": row["id"],
                "discovered_via": row["source"],
                "source_job_id": row["source_job_id"],
                "original_source": row["source"],
                "observation_url": row["url"],
                "canonical_url": canonical_url,
                "original_reference": _original_reference(str(row["url"])),
                "employer_name": row["employer_name"],
                "title": row["title"],
                "work_location": row["work_location"],
                "first_seen_at": row["first_seen_at"],
                "last_seen_at": row["last_seen_at"],
            }
        )
    if observations:
        table = sa.table(
            "vacancy_observations",
            *(sa.column(key) for key in observations[0]),
        )
        op.bulk_insert(table, observations)


def downgrade() -> None:
    op.drop_index(
        "ix_vacancy_observations_original_reference",
        table_name="vacancy_observations",
    )
    op.drop_index(
        "ix_vacancy_observations_canonical_url",
        table_name="vacancy_observations",
    )
    op.drop_index(
        "ix_vacancy_observations_vacancy_id",
        table_name="vacancy_observations",
    )
    op.drop_table("vacancy_observations")
    op.drop_index("ix_job_vacancies_canonical_url", table_name="job_vacancies")
    op.drop_column("job_vacancies", "experience")
    op.drop_column("job_vacancies", "employment")
    op.drop_column("job_vacancies", "salary")
    op.drop_column("job_vacancies", "original_source")
    op.drop_column("job_vacancies", "canonical_url")
