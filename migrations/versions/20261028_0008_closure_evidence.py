"""Persist authoritative vacancy closure evidence."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261028_0008"
down_revision: str | None = "20261027_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "vacancy_observations",
        sa.Column(
            "closure_evidence",
            sa.String(length=32),
            nullable=False,
            server_default="unknown",
        ),
    )
    with op.batch_alter_table("vacancy_observations") as batch_op:
        batch_op.create_check_constraint(
            "ck_vacancy_observations_closure_evidence",
            "closure_evidence IN "
            "('unknown', 'provider_explicit', 'authoritative_absence')",
        )
        batch_op.create_check_constraint(
            "ck_vacancy_observations_authoritative_closure_shape",
            "closure_evidence = 'unknown' OR "
            "(status = 'closed' AND closed_at IS NOT NULL)",
        )

    # Neither databases upgraded through 0007 nor a fresh upgrade from an older
    # revision contain persisted evidence provenance. Ownership, timestamps and
    # counters alone cannot prove how historical closure was established.
    op.execute(
        sa.text(
            "UPDATE job_vacancies "
            "SET status = 'active', closed_at = NULL "
            "WHERE status = 'closed' OR closed_at IS NOT NULL"
        )
    )


def downgrade() -> None:
    with op.batch_alter_table("vacancy_observations") as batch_op:
        batch_op.drop_constraint(
            "ck_vacancy_observations_authoritative_closure_shape",
            type_="check",
        )
        batch_op.drop_constraint(
            "ck_vacancy_observations_closure_evidence",
            type_="check",
        )
        batch_op.drop_column("closure_evidence")
