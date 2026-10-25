
"""add estimated preparation minutes to orders

Revision ID: d3d45a2753c3
Revises: 09c875830019
Create Date: 2026-10-25
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "d3d45a2753c3"
down_revision: Union[str, Sequence[str], None] = "09c875830019"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "orders",
        sa.Column(
            "estimated_preparation_minutes",
            sa.Integer(),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column(
        "orders",
        "estimated_preparation_minutes",
    )
