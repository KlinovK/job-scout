"""Repair unsafe legacy canonical lifecycle state."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261027_0007"
down_revision: str | None = "20261009_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE job_vacancies AS vacancy "
            "SET status = 'active', closed_at = NULL "
            "WHERE vacancy.status = 'closed' AND ("
            "  vacancy.closed_at IS NULL "
            "  OR NOT EXISTS ("
            "    SELECT 1 FROM vacancy_observations AS observation "
            "    WHERE observation.vacancy_id = vacancy.id"
            "  ) "
            "  OR EXISTS ("
            "    SELECT 1 FROM vacancy_observations AS observation "
            "    WHERE observation.vacancy_id = vacancy.id "
            "      AND ("
            "        observation.source_company_id IS NULL "
            "        OR observation.status != 'closed' "
            "        OR observation.closed_at IS NULL"
            "      )"
            "  )"
            ")"
        )
    )


def downgrade() -> None:
    # The conservative repair discards no trustworthy closure evidence, but the
    # previous ambiguous canonical state cannot be reconstructed safely.
    pass
