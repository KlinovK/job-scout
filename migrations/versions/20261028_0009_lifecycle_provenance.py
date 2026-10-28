"""Persist observation lifecycle provenance."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261028_0009"
down_revision: str | None = "20261028_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "vacancy_observations",
        sa.Column(
            "lifecycle_provenance",
            sa.String(length=32),
            nullable=False,
            server_default="unknown",
        ),
    )
    with op.batch_alter_table("vacancy_observations") as batch_op:
        batch_op.create_check_constraint(
            "ck_vacancy_observations_lifecycle_provenance",
            "lifecycle_provenance IN "
            "('unknown', 'generic_upsert', 'accepted_collection')",
        )


def downgrade() -> None:
    with op.batch_alter_table("vacancy_observations") as batch_op:
        batch_op.drop_constraint(
            "ck_vacancy_observations_lifecycle_provenance",
            type_="check",
        )
        batch_op.drop_column("lifecycle_provenance")
