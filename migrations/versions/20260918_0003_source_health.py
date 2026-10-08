"""Add registry provenance and current source health state."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260918_0003"
down_revision: str | None = "20260917_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "companies",
        sa.Column(
            "provenance",
            sa.String(length=32),
            nullable=False,
            server_default="manual",
        ),
    )
    op.add_column(
        "companies",
        sa.Column("source_verified_at", sa.String(length=40), nullable=True),
    )
    op.add_column(
        "companies",
        sa.Column("last_success_at", sa.String(length=40), nullable=True),
    )
    op.add_column(
        "companies",
        sa.Column("last_job_count", sa.Integer(), nullable=True),
    )
    op.add_column(
        "companies",
        sa.Column("last_status", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "companies",
        sa.Column("last_error_category", sa.String(length=120), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("companies", "last_error_category")
    op.drop_column("companies", "last_status")
    op.drop_column("companies", "last_job_count")
    op.drop_column("companies", "last_success_at")
    op.drop_column("companies", "source_verified_at")
    op.drop_column("companies", "provenance")
