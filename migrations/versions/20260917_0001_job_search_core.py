"""Create the Phase 1 company registry and vacancy tables."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260917_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "companies",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("country", sa.String(length=120), nullable=True),
        sa.Column("careers_url", sa.Text(), nullable=False),
        sa.Column("ats_type", sa.String(length=32), nullable=False),
        sa.Column("ats_identifier", sa.String(length=255), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("last_checked_at", sa.String(length=40), nullable=True),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        sa.Column("updated_at", sa.String(length=40), nullable=False),
        sa.CheckConstraint(
            "priority >= 0",
            name="ck_companies_priority_nonnegative",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "ats_type",
            "ats_identifier",
            name="uq_companies_ats_identity",
        ),
    )
    op.create_table(
        "job_vacancies",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("company_id", sa.String(length=36), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("company_location", sa.String(length=255), nullable=True),
        sa.Column("work_location", sa.String(length=500), nullable=True),
        sa.Column("remote_policy", sa.String(length=32), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("source_job_id", sa.String(length=255), nullable=False),
        sa.Column("published_at", sa.String(length=40), nullable=True),
        sa.Column("first_seen_at", sa.String(length=40), nullable=False),
        sa.Column("last_seen_at", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.CheckConstraint(
            "remote_policy IN ('unknown', 'remote', 'hybrid', 'onsite')",
            name="ck_job_vacancies_remote_policy",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'closed')",
            name="ck_job_vacancies_status",
        ),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source",
            "source_job_id",
            name="uq_job_vacancies_source_identity",
        ),
    )
    op.create_index(
        "ix_job_vacancies_company_id",
        "job_vacancies",
        ["company_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_job_vacancies_company_id", table_name="job_vacancies")
    op.drop_table("job_vacancies")
    op.drop_table("companies")
