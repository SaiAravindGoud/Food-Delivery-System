
"""add delivery partner location timestamp

Revision ID: 2c3f2dbde0e4
Revises: 647509aeca66
"""

from alembic import op
import sqlalchemy as sa


revision = "2c3f2dbde0e4"
down_revision = "647509aeca66"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "delivery_partners",
        sa.Column(
            "last_location_update",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column(
        "delivery_partners",
        "last_location_update",
    )
