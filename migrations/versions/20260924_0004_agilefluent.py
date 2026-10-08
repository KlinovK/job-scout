"""Add AgileFluent employer identity to normalized vacancies."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260924_0004"
down_revision: str | None = "20260918_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "job_vacancies",
        sa.Column("employer_name", sa.String(length=255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("job_vacancies", "employer_name")
