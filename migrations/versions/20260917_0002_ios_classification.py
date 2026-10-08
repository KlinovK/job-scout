"""Add versioned iOS vacancy classifications."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260917_0002"
down_revision: str | None = "20260917_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "vacancy_classifications",
        sa.Column("vacancy_id", sa.String(length=36), nullable=False),
        sa.Column("classifier_version", sa.String(length=64), nullable=False),
        sa.Column("decision", sa.String(length=32), nullable=False),
        sa.Column("role_category", sa.String(length=32), nullable=False),
        sa.Column("seniority", sa.String(length=32), nullable=False),
        sa.Column("ios_relevance", sa.String(length=32), nullable=False),
        sa.Column("work_mode", sa.String(length=32), nullable=False),
        sa.Column("relocation", sa.String(length=32), nullable=False),
        sa.Column("geography", sa.String(length=32), nullable=False),
        sa.Column("positive_signals", sa.Text(), nullable=False),
        sa.Column("warnings", sa.Text(), nullable=False),
        sa.Column("rejection_reasons", sa.Text(), nullable=False),
        sa.Column("classified_at", sa.String(length=40), nullable=False),
        sa.CheckConstraint(
            "decision IN ('match', 'possible_match', 'reject')",
            name="ck_vacancy_classifications_decision",
        ),
        sa.CheckConstraint(
            "role_category IN "
            "('ios', 'swift', 'apple_platforms', 'ios_sdk', "
            "'mobile_ios', 'mixed_mobile', 'other')",
            name="ck_vacancy_classifications_role_category",
        ),
        sa.CheckConstraint(
            "seniority IN "
            "('middle', 'senior', 'lead', 'junior', 'intern', "
            "'staff', 'principal', 'unknown')",
            name="ck_vacancy_classifications_seniority",
        ),
        sa.CheckConstraint(
            "ios_relevance IN ('strong', 'substantial', 'weak', 'none')",
            name="ck_vacancy_classifications_ios_relevance",
        ),
        sa.CheckConstraint(
            "work_mode IN ('remote', 'hybrid', 'onsite', 'unknown')",
            name="ck_vacancy_classifications_work_mode",
        ),
        sa.CheckConstraint(
            "relocation IN ('available', 'unavailable', 'unknown')",
            name="ck_vacancy_classifications_relocation",
        ),
        sa.CheckConstraint(
            "geography IN "
            "('worldwide', 'emea', 'europe', 'eu_only', 'us_only', "
            "'uk_only', 'country_only', 'unknown')",
            name="ck_vacancy_classifications_geography",
        ),
        sa.ForeignKeyConstraint(
            ["vacancy_id"],
            ["job_vacancies.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("vacancy_id", "classifier_version"),
    )
    op.create_index(
        "ix_vacancy_classifications_version_decision",
        "vacancy_classifications",
        ["classifier_version", "decision"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_vacancy_classifications_version_decision",
        table_name="vacancy_classifications",
    )
    op.drop_table("vacancy_classifications")
