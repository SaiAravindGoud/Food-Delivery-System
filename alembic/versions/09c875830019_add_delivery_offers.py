"""add delivery offers

Revision ID: 09c875830019
Revises: 2c3f2dbde0e4
Create Date: 2026-10-24 23:18:34.970410
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "09c875830019"
down_revision: Union[str, Sequence[str], None] = "2c3f2dbde0e4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create the delivery offers table."""
    op.create_table(
        "delivery_offers",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("order_id", sa.Integer(), nullable=False),
        sa.Column("delivery_partner_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column(
            "offered_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("responded_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["delivery_partner_id"], ["delivery_partners.id"]
        ),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index(
        "ix_delivery_offers_delivery_partner_id",
        "delivery_offers",
        ["delivery_partner_id"],
        unique=False,
    )
    op.create_index(
        "ix_delivery_offers_id",
        "delivery_offers",
        ["id"],
        unique=False,
    )
    op.create_index(
        "ix_delivery_offers_order_id",
        "delivery_offers",
        ["order_id"],
        unique=False,
    )
    op.create_index(
        "ix_delivery_offers_order_status",
        "delivery_offers",
        ["order_id", "status"],
        unique=False,
    )
    op.create_index(
        "ix_delivery_offers_partner_status",
        "delivery_offers",
        ["delivery_partner_id", "status"],
        unique=False,
    )
    op.create_index(
        "ix_delivery_offers_status_expires",
        "delivery_offers",
        ["status", "expires_at"],
        unique=False,
    )


def downgrade() -> None:
    """Remove the delivery offers table."""
    op.drop_index(
        "ix_delivery_offers_status_expires",
        table_name="delivery_offers",
    )
    op.drop_index(
        "ix_delivery_offers_partner_status",
        table_name="delivery_offers",
    )
    op.drop_index(
        "ix_delivery_offers_order_status",
        table_name="delivery_offers",
    )
    op.drop_index(
        "ix_delivery_offers_order_id",
        table_name="delivery_offers",
    )
    op.drop_index(
        "ix_delivery_offers_id",
        table_name="delivery_offers",
    )
    op.drop_index(
        "ix_delivery_offers_delivery_partner_id",
        table_name="delivery_offers",
    )
    op.drop_table("delivery_offers")
