"""Add persistence fields required for vacancy lifecycle reconciliation."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261009_0006"
down_revision: str | None = "20260927_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "companies",
        sa.Column("last_complete_snapshot_at", sa.String(length=40), nullable=True),
    )
    op.add_column(
        "job_vacancies",
        sa.Column("closed_at", sa.String(length=40), nullable=True),
    )
    with op.batch_alter_table("job_vacancies") as batch_op:
        batch_op.drop_constraint(
            "uq_job_vacancies_source_identity",
            type_="unique",
        )
        batch_op.create_unique_constraint(
            "uq_job_vacancies_company_source_identity",
            ["company_id", "source", "source_job_id"],
        )
    op.create_index(
        "ix_job_vacancies_status",
        "job_vacancies",
        ["status"],
    )

    op.add_column(
        "vacancy_observations",
        sa.Column("source_company_id", sa.String(length=36), nullable=True),
    )
    op.add_column(
        "vacancy_observations",
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default="active",
        ),
    )
    op.add_column(
        "vacancy_observations",
        sa.Column("closed_at", sa.String(length=40), nullable=True),
    )
    op.add_column(
        "vacancy_observations",
        sa.Column(
            "missing_complete_snapshots",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )

    connection = op.get_bind()
    connection.execute(
        sa.text(
            "UPDATE vacancy_observations AS observation "
            "SET source_company_id = ("
            "  SELECT vacancy.company_id FROM job_vacancies AS vacancy "
            "  WHERE vacancy.id = observation.vacancy_id"
            ") "
            "WHERE EXISTS ("
            "  SELECT 1 FROM job_vacancies AS vacancy "
            "  WHERE vacancy.id = observation.vacancy_id "
            "    AND vacancy.source = observation.discovered_via "
            "    AND vacancy.source_job_id = observation.source_job_id"
            ")"
        )
    )
    connection.execute(
        sa.text(
            "UPDATE vacancy_observations AS observation "
            "SET status = ("
            "  SELECT vacancy.status FROM job_vacancies AS vacancy "
            "  WHERE vacancy.id = observation.vacancy_id"
            ") "
            "WHERE observation.source_company_id IS NOT NULL"
        )
    )

    with op.batch_alter_table("vacancy_observations") as batch_op:
        batch_op.drop_constraint(
            "uq_vacancy_observations_source_identity",
            type_="unique",
        )
        batch_op.create_foreign_key(
            "fk_vacancy_observations_source_company_id",
            "companies",
            ["source_company_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_check_constraint(
            "ck_vacancy_observations_status",
            "status IN ('active', 'closed')",
        )
        batch_op.create_check_constraint(
            "ck_vacancy_observations_missing_nonnegative",
            "missing_complete_snapshots >= 0",
        )

    op.create_index(
        "uq_vacancy_observations_owned_source_identity",
        "vacancy_observations",
        ["source_company_id", "discovered_via", "source_job_id"],
        unique=True,
        sqlite_where=sa.text("source_company_id IS NOT NULL"),
    )
    op.create_index(
        "uq_vacancy_observations_unowned_source_identity",
        "vacancy_observations",
        ["discovered_via", "source_job_id"],
        unique=True,
        sqlite_where=sa.text("source_company_id IS NULL"),
    )
    op.create_index(
        "ix_vacancy_observations_company_reconciliation",
        "vacancy_observations",
        ["source_company_id", "discovered_via", "status"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_vacancy_observations_company_reconciliation",
        table_name="vacancy_observations",
    )
    op.drop_index(
        "uq_vacancy_observations_unowned_source_identity",
        table_name="vacancy_observations",
    )
    op.drop_index(
        "uq_vacancy_observations_owned_source_identity",
        table_name="vacancy_observations",
    )

    with op.batch_alter_table("vacancy_observations") as batch_op:
        batch_op.drop_constraint(
            "ck_vacancy_observations_missing_nonnegative",
            type_="check",
        )
        batch_op.drop_constraint(
            "ck_vacancy_observations_status",
            type_="check",
        )
        batch_op.drop_constraint(
            "fk_vacancy_observations_source_company_id",
            type_="foreignkey",
        )
        batch_op.create_unique_constraint(
            "uq_vacancy_observations_source_identity",
            ["discovered_via", "source_job_id"],
        )
        batch_op.drop_column("missing_complete_snapshots")
        batch_op.drop_column("closed_at")
        batch_op.drop_column("status")
        batch_op.drop_column("source_company_id")

    with op.batch_alter_table("job_vacancies") as batch_op:
        batch_op.drop_constraint(
            "uq_job_vacancies_company_source_identity",
            type_="unique",
        )
        batch_op.create_unique_constraint(
            "uq_job_vacancies_source_identity",
            ["source", "source_job_id"],
        )
    op.drop_index("ix_job_vacancies_status", table_name="job_vacancies")
    op.drop_column("job_vacancies", "closed_at")
    op.drop_column("companies", "last_complete_snapshot_at")
